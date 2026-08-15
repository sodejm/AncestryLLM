#!/usr/bin/env python3
"""Generate a sanitized, canonical, deterministic CycloneDX environment SBOM."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_NORMALIZE_NAME = re.compile(r"[-_.]+")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_MAXIMUM_SBOM_BYTES = 64 * 1024 * 1024
_PROJECT_COMPONENT_TYPE = "library"
_DEPENDENCY_FIELDS = frozenset({"ref", "dependsOn", "provides"})
_LOCAL_DISTRIBUTION_REFERENCE = {
    "comment": "PackageSource: Local",
    "type": "distribution",
}

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class SbomError(RuntimeError):
    """A stable coded release-SBOM generation failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str) -> None:
    raise SbomError(code, message)


def _normalized_name(name: str) -> str:
    return _NORMALIZE_NAME.sub("-", name).lower()


def _is_local_path(value: str) -> bool:
    if urlsplit(value).scheme.lower() == "file":
        return True
    return value.startswith(("/", "\\\\")) or _WINDOWS_ABSOLUTE_PATH.match(value) is not None


def _is_recognized_local_distribution(reference: dict[str, Any]) -> bool:
    return (
        set(reference) == {"comment", "type", "url"}
        and reference.get("comment") == _LOCAL_DISTRIBUTION_REFERENCE["comment"]
        and reference.get("type") == _LOCAL_DISTRIBUTION_REFERENCE["type"]
        and isinstance(reference.get("url"), str)
        and urlsplit(reference["url"]).scheme.lower() == "file"
    )


def _sanitize_local_values(value: Any) -> None:
    if isinstance(value, dict):
        references = value.get("externalReferences")
        if references is not None:
            if not isinstance(references, list):
                _fail(
                    "SBOM_STRUCTURE_INVALID",
                    "An externalReferences field is not a list.",
                )
            retained_references: list[Any] = []
            for reference in references:
                if not isinstance(reference, dict):
                    _fail(
                        "SBOM_STRUCTURE_INVALID",
                        "An external reference is not an object.",
                    )
                if _is_recognized_local_distribution(reference):
                    continue
                retained_references.append(reference)
            value["externalReferences"] = retained_references

        for nested_value in value.values():
            _sanitize_local_values(nested_value)
        return

    if isinstance(value, list):
        for item in value:
            _sanitize_local_values(item)
        return

    if isinstance(value, str) and _is_local_path(value):
        _fail(
            "SBOM_LOCAL_PATH",
            "The generated SBOM contains an unrecognized local path.",
        )


def _require_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("SBOM_STRUCTURE_INVALID", f"The {field} field is not an object.")
    return value


def _require_list(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        _fail("SBOM_STRUCTURE_INVALID", f"The {field} field is not a list.")
    return value


def _project_body(component: dict[str, Any]) -> dict[str, Any]:
    comparable = copy.deepcopy(component)
    comparable.pop("bom-ref", None)
    comparable.pop("purl", None)
    return comparable


def _canonicalize_project_component(
    components: list[dict[str, Any]],
    *,
    project_name: str,
    project_version: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    normalized_project_name = _normalized_name(project_name)
    expected_reference = f"{normalized_project_name}=={project_version}"
    expected_purl = f"pkg:pypi/{normalized_project_name}@{project_version}"
    project_components = [
        component
        for component in components
        if isinstance(component.get("name"), str)
        and _normalized_name(component["name"]) == normalized_project_name
    ]
    if not project_components:
        _fail(
            "SBOM_PROJECT_IDENTITY",
            "The generated SBOM does not contain the expected project component.",
        )

    aliases: dict[str, str] = {}
    expected_body: dict[str, Any] | None = None
    for component in project_components:
        reference = component.get("bom-ref")
        if not isinstance(reference, str) or not reference:
            _fail(
                "SBOM_COMPONENT_INVALID",
                "A project component has no usable bom-ref.",
            )
        if component.get("version") != project_version:
            _fail(
                "SBOM_PROJECT_IDENTITY",
                "A project component has an unexpected version.",
            )
        if component.get("type") != _PROJECT_COMPONENT_TYPE:
            _fail(
                "SBOM_PROJECT_IDENTITY",
                "A project component has an unexpected type.",
            )
        purl = component.get("purl")
        if purl is not None and purl != expected_purl:
            _fail(
                "SBOM_COMPONENT_CONFLICT",
                "Project components disagree about their package identity.",
            )
        body = _project_body(component)
        if expected_body is None:
            expected_body = body
        elif body != expected_body:
            _fail(
                "SBOM_COMPONENT_CONFLICT",
                "Duplicate project components are not semantically equivalent.",
            )
        aliases[reference] = expected_reference

    canonical = copy.deepcopy(project_components[0])
    canonical["bom-ref"] = expected_reference
    canonical["purl"] = expected_purl
    retained = [component for component in components if component not in project_components]
    retained.append(canonical)
    return retained, aliases


def _canonicalize_components(raw_components: list[Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    references: set[str] = set()
    for raw_component in raw_components:
        component = _require_object(raw_component, field="components entry")
        reference = component.get("bom-ref")
        if not isinstance(reference, str) or not reference or reference in references:
            _fail(
                "SBOM_COMPONENT_INVALID",
                "Component bom-ref values must be non-empty and unique.",
            )
        references.add(reference)
        components.append(component)
    return components


def _canonicalize_dependencies(
    raw_dependencies: list[Any],
    *,
    aliases: dict[str, str],
    component_references: set[str],
) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, set[str]]] = {}
    for raw_dependency in raw_dependencies:
        dependency = _require_object(raw_dependency, field="dependencies entry")
        if not set(dependency).issubset(_DEPENDENCY_FIELDS):
            _fail(
                "SBOM_GRAPH_INVALID",
                "A dependency node contains unsupported fields.",
            )
        raw_reference = dependency.get("ref")
        if not isinstance(raw_reference, str) or not raw_reference:
            _fail("SBOM_GRAPH_INVALID", "A dependency node has no usable ref.")
        reference = aliases.get(raw_reference, raw_reference)
        node = nodes.setdefault(reference, {"dependsOn": set(), "provides": set()})
        for edge_field in ("dependsOn", "provides"):
            raw_edges = dependency.get(edge_field, [])
            if not isinstance(raw_edges, list) or not all(
                isinstance(edge, str) and edge for edge in raw_edges
            ):
                _fail(
                    "SBOM_GRAPH_INVALID",
                    "A dependency edge list is malformed.",
                )
            node[edge_field].update(
                aliases.get(edge, edge)
                for edge in raw_edges
                if aliases.get(edge, edge) != reference
            )

    if set(nodes) != component_references:
        _fail(
            "SBOM_GRAPH_INVALID",
            "Dependency nodes do not exactly cover the component graph.",
        )
    for node in nodes.values():
        if not (node["dependsOn"] | node["provides"]).issubset(component_references):
            _fail(
                "SBOM_GRAPH_INVALID",
                "A dependency edge references an unknown component.",
            )

    dependencies: list[dict[str, Any]] = []
    for reference in sorted(nodes):
        dependency: dict[str, Any] = {"ref": reference}
        for edge_field in ("dependsOn", "provides"):
            if nodes[reference][edge_field]:
                dependency[edge_field] = sorted(nodes[reference][edge_field])
        dependencies.append(dependency)
    return dependencies


def canonicalize_sbom(
    payload: dict[str, Any],
    *,
    project_name: str,
    project_version: str,
) -> dict[str, Any]:
    """Return a sanitized, canonical SBOM or fail with a stable error code."""
    if not isinstance(payload, dict):
        _fail("SBOM_STRUCTURE_INVALID", "The CycloneDX document is not an object.")
    if (
        payload.get("bomFormat") != "CycloneDX"
        or payload.get("specVersion") != "1.6"
        or payload.get("version") != 1
    ):
        _fail(
            "SBOM_SCHEMA_UNSUPPORTED",
            "The CycloneDX schema identity is unsupported.",
        )
    if not project_name or not project_version:
        _fail("SBOM_PROJECT_IDENTITY", "The expected project identity is incomplete.")

    result = copy.deepcopy(payload)
    result.pop("serialNumber", None)
    metadata = _require_object(result.get("metadata"), field="metadata")
    metadata.pop("timestamp", None)
    _sanitize_local_values(result)

    components = _canonicalize_components(
        _require_list(result.get("components"), field="components")
    )
    components, aliases = _canonicalize_project_component(
        components,
        project_name=project_name,
        project_version=project_version,
    )
    components.sort(key=lambda component: component["bom-ref"])
    component_references = {component["bom-ref"] for component in components}
    if len(component_references) != len(components):
        _fail(
            "SBOM_COMPONENT_INVALID",
            "Canonical component bom-ref values are not unique.",
        )
    result["components"] = components
    result["dependencies"] = _canonicalize_dependencies(
        _require_list(result.get("dependencies"), field="dependencies"),
        aliases=aliases,
        component_references=component_references,
    )

    _sanitize_local_values(result)
    return result


def serialize_sbom(payload: dict[str, Any]) -> str:
    """Serialize canonical SBOM evidence deterministically."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _run_cyclonedx(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        arguments,
        capture_output=True,
        check=False,
        text=True,
    )


def _read_generated_sbom(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAXIMUM_SBOM_BYTES:
            _fail("SBOM_JSON_INVALID", "The generated SBOM exceeds the size limit.")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except SbomError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SbomError(
            "SBOM_JSON_INVALID",
            "The generated SBOM is not valid UTF-8 JSON.",
        ) from error
    if not isinstance(payload, dict):
        _fail("SBOM_JSON_INVALID", "The generated SBOM is not a JSON object.")
    return payload


def _publish_atomically(output: Path, rendered: str) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(output)
        temporary_path = None
    except OSError as error:
        raise SbomError(
            "SBOM_OUTPUT_FAILED",
            "The canonical SBOM could not be published atomically.",
        ) from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def generate_sbom(
    *,
    python: Path,
    output: Path,
    project_name: str,
    project_version: str,
    runner: Runner = _run_cyclonedx,
    temporary_root: Path | None = None,
) -> None:
    """Generate and atomically publish one canonical release SBOM."""
    try:
        with tempfile.TemporaryDirectory(
            prefix="ancestryllm-sbom-", dir=temporary_root
        ) as directory:
            raw_output = Path(directory) / "raw-sbom.json"
            arguments = [
                "cyclonedx-py",
                "environment",
                "--output-reproducible",
                "--output-file",
                str(raw_output),
                str(python),
            ]
            completed = runner(arguments)
            if completed.returncode != 0:
                _fail(
                    "SBOM_TOOL_FAILED",
                    "The locked CycloneDX generator did not complete successfully.",
                )
            canonical = canonicalize_sbom(
                _read_generated_sbom(raw_output),
                project_name=project_name,
                project_version=project_version,
            )
    except SbomError:
        raise
    except OSError as error:
        raise SbomError(
            "SBOM_TOOL_FAILED",
            "The locked CycloneDX generator could not be executed.",
        ) from error

    _publish_atomically(output, serialize_sbom(canonical))


def _load_project_identity(path: Path) -> tuple[str, str]:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise SbomError(
            "SBOM_PROJECT_INVALID",
            "The project metadata is not valid UTF-8 TOML.",
        ) from error
    project = payload.get("project")
    if not isinstance(project, dict):
        _fail("SBOM_PROJECT_INVALID", "The project metadata has no project table.")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
        _fail("SBOM_PROJECT_INVALID", "The project name or version is invalid.")
    return name, version


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the release-SBOM generator CLI."""
    arguments = _parser().parse_args(argv)
    try:
        project_name, project_version = _load_project_identity(arguments.project)
        generate_sbom(
            python=arguments.python,
            output=arguments.output,
            project_name=project_name,
            project_version=project_version,
        )
    except SbomError as error:
        print(f"release SBOM generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
