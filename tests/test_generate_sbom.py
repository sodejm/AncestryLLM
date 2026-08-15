"""Tests for sanitized, canonical, deterministic release SBOM generation."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.generate_sbom import SbomError, canonicalize_sbom, generate_sbom, serialize_sbom

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]


def _project_component(*, canonical: bool) -> dict[str, object]:
    component: dict[str, object] = {
        "bom-ref": "ancestryllm==0.6.0" if canonical else "BomRef.fictional.local",
        "description": "Fictional package description.",
        "externalReferences": [
            {
                "comment": "PackageSource: Local",
                "type": "distribution",
                "url": "file:///workspace/fictional-project",
            },
            {
                "type": "documentation",
                "url": "https://example.test/docs",
            },
        ]
        if not canonical
        else [
            {
                "type": "documentation",
                "url": "https://example.test/docs",
            }
        ],
        "name": "ancestryllm",
        "type": "library",
        "version": "0.6.0",
    }
    if canonical:
        component["purl"] = "pkg:pypi/ancestryllm@0.6.0"
    return component


def _dependency_component() -> dict[str, object]:
    return {
        "bom-ref": "fictional-dependency==1.2.3",
        "name": "fictional-dependency",
        "purl": "pkg:pypi/fictional-dependency@1.2.3",
        "type": "library",
        "version": "1.2.3",
    }


def _raw_sbom() -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "components": [
            _project_component(canonical=False),
            _dependency_component(),
            _project_component(canonical=True),
        ],
        "dependencies": [
            {"ref": "fictional-dependency==1.2.3"},
            {
                "dependsOn": ["fictional-dependency==1.2.3"],
                "ref": "ancestryllm==0.6.0",
            },
        ],
        "metadata": {
            "timestamp": "2026-01-02T03:04:05Z",
            "tools": {"components": []},
        },
        "serialNumber": "urn:uuid:00000000-0000-4000-8000-000000000001",
        "specVersion": "1.6",
        "version": 1,
    }


def test_canonicalize_removes_local_reference_and_merges_project_root() -> None:
    result = canonicalize_sbom(
        _raw_sbom(),
        project_name="ancestryllm",
        project_version="0.6.0",
    )

    assert "serialNumber" not in result
    assert "timestamp" not in result["metadata"]
    roots = [component for component in result["components"] if component["name"] == "ancestryllm"]
    assert roots == [
        {
            "bom-ref": "ancestryllm==0.6.0",
            "description": "Fictional package description.",
            "externalReferences": [
                {
                    "type": "documentation",
                    "url": "https://example.test/docs",
                }
            ],
            "name": "ancestryllm",
            "purl": "pkg:pypi/ancestryllm@0.6.0",
            "type": "library",
            "version": "0.6.0",
        }
    ]
    component_refs = {component["bom-ref"] for component in result["components"]}
    assert {dependency["ref"] for dependency in result["dependencies"]} == component_refs
    rendered = serialize_sbom(result)
    assert "file:" not in rendered
    assert "/workspace/" not in rendered


def test_canonicalize_remaps_a_dependency_node_owned_by_the_duplicate() -> None:
    payload = _raw_sbom()
    payload["dependencies"].append(
        {
            "dependsOn": ["fictional-dependency==1.2.3"],
            "ref": "BomRef.fictional.local",
        }
    )

    result = canonicalize_sbom(
        payload,
        project_name="ancestryllm",
        project_version="0.6.0",
    )

    root_dependency = next(
        dependency
        for dependency in result["dependencies"]
        if dependency["ref"] == "ancestryllm==0.6.0"
    )
    assert root_dependency == {
        "dependsOn": ["fictional-dependency==1.2.3"],
        "ref": "ancestryllm==0.6.0",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", "file:///workspace/not-a-tool-distribution"),
        ("value", "/workspace/fictional-project/private.txt"),
        ("value", "C:\\workspace\\fictional-project\\private.txt"),
        ("value", "\\\\server\\share\\private.txt"),
    ],
)
def test_canonicalize_rejects_unrecognized_local_paths(field: str, value: str) -> None:
    payload = _raw_sbom()
    dependency = payload["components"][1]
    if field == "url":
        dependency["externalReferences"] = [
            {"type": "documentation", "url": value},
        ]
    else:
        dependency["properties"] = [{"name": "fictional", field: value}]

    with pytest.raises(SbomError, match="SBOM_LOCAL_PATH") as error:
        canonicalize_sbom(
            payload,
            project_name="ancestryllm",
            project_version="0.6.0",
        )

    assert error.value.code == "SBOM_LOCAL_PATH"


def test_canonicalize_rejects_conflicting_project_duplicates() -> None:
    payload = _raw_sbom()
    payload["components"][0]["description"] = "Conflicting description."

    with pytest.raises(SbomError, match="SBOM_COMPONENT_CONFLICT") as error:
        canonicalize_sbom(
            payload,
            project_name="ancestryllm",
            project_version="0.6.0",
        )

    assert error.value.code == "SBOM_COMPONENT_CONFLICT"


def test_canonicalize_rejects_unknown_dependency_references() -> None:
    payload = _raw_sbom()
    payload["dependencies"][0]["dependsOn"] = ["missing==9.9.9"]

    with pytest.raises(SbomError, match="SBOM_GRAPH_INVALID") as error:
        canonicalize_sbom(
            payload,
            project_name="ancestryllm",
            project_version="0.6.0",
        )

    assert error.value.code == "SBOM_GRAPH_INVALID"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda payload: payload.update(specVersion="1.5"), "SBOM_SCHEMA_UNSUPPORTED"),
        (
            lambda payload: payload["components"].clear(),
            "SBOM_PROJECT_IDENTITY",
        ),
        (
            lambda payload: payload["components"][0].update(version="9.9.9"),
            "SBOM_PROJECT_IDENTITY",
        ),
    ],
)
def test_canonicalize_rejects_schema_or_project_identity_drift(
    mutation: Callable[[dict[str, object]], object],
    expected_code: str,
) -> None:
    payload = _raw_sbom()
    mutation(payload)

    with pytest.raises(SbomError, match=expected_code) as error:
        canonicalize_sbom(
            payload,
            project_name="ancestryllm",
            project_version="0.6.0",
        )

    assert error.value.code == expected_code


def test_canonicalization_is_deterministic_for_equivalent_input_order() -> None:
    first = _raw_sbom()
    second = copy.deepcopy(first)
    second["components"].reverse()
    second["dependencies"].reverse()

    first_rendered = serialize_sbom(
        canonicalize_sbom(first, project_name="ancestryllm", project_version="0.6.0")
    )
    second_rendered = serialize_sbom(
        canonicalize_sbom(second, project_name="ancestryllm", project_version="0.6.0")
    )

    assert first_rendered == second_rendered


def test_generate_uses_reproducible_mode_and_publishes_atomically(tmp_path: Path) -> None:
    output = tmp_path / "sbom.json"
    output.write_text("previous evidence\n", encoding="utf-8")
    observed: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        observed.append(arguments)
        raw_output = Path(arguments[arguments.index("--output-file") + 1])
        raw_output.write_text(json.dumps(_raw_sbom()), encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    generate_sbom(
        python=Path(".venv/bin/python"),
        output=output,
        project_name="ancestryllm",
        project_version="0.6.0",
        runner=runner,
        temporary_root=tmp_path,
    )

    assert len(observed) == 1
    assert observed[0][:3] == ["cyclonedx-py", "environment", "--output-reproducible"]
    assert observed[0][-1] == ".venv/bin/python"
    assert output.read_text(encoding="utf-8") == serialize_sbom(
        canonicalize_sbom(
            _raw_sbom(),
            project_name="ancestryllm",
            project_version="0.6.0",
        )
    )


def test_generate_preserves_existing_output_when_the_tool_fails(tmp_path: Path) -> None:
    output = tmp_path / "sbom.json"
    output.write_text("previous evidence\n", encoding="utf-8")

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(arguments, 7, "sensitive output", "sensitive error")

    with pytest.raises(SbomError, match="SBOM_TOOL_FAILED") as error:
        generate_sbom(
            python=Path(".venv/bin/python"),
            output=output,
            project_name="ancestryllm",
            project_version="0.6.0",
            runner=runner,
            temporary_root=tmp_path,
        )

    assert error.value.code == "SBOM_TOOL_FAILED"
    assert output.read_text(encoding="utf-8") == "previous evidence\n"
    assert "sensitive" not in str(error.value)


def test_generate_preserves_existing_output_when_canonicalization_fails(
    tmp_path: Path,
) -> None:
    output = tmp_path / "sbom.json"
    output.write_text("previous evidence\n", encoding="utf-8")

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        raw_output = Path(arguments[arguments.index("--output-file") + 1])
        raw_output.write_text(
            json.dumps({**_raw_sbom(), "specVersion": "1.5"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    with pytest.raises(SbomError, match="SBOM_SCHEMA_UNSUPPORTED"):
        generate_sbom(
            python=Path(".venv/bin/python"),
            output=output,
            project_name="ancestryllm",
            project_version="0.6.0",
            runner=runner,
            temporary_root=tmp_path,
        )

    assert output.read_text(encoding="utf-8") == "previous evidence\n"


def test_make_sbom_uses_the_locked_sanitizing_runner() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert (
        "$(UV_BIN) run --locked --group security python scripts/generate_sbom.py "
        "--python $(VENV_PYTHON) --output $(SBOM_OUTPUT) --project pyproject.toml"
    ) in makefile
    assert "cyclonedx-py environment --output-file $(SBOM_OUTPUT)" not in makefile
