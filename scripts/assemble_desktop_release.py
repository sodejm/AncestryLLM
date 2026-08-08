#!/usr/bin/env python3
"""Create and aggregate fail-closed desktop release evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from release_signing_policy import signing_disclosure, validate_signing_mode

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
ARTIFACT_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMON_GATES = {
    "exactHeadPassed",
    "installerBuiltPassed",
    "installedRuntimePassed",
    "runtimeIsolationPassed",
    "sbomGeneratedPassed",
    "sidecarHandshakePassed",
}
VALIDATION_GATES = {
    "exactHeadPassed",
    "installedRuntimePassed",
    "operatingSystemPassed",
    "runtimeIsolationPassed",
    "sidecarHandshakePassed",
}
TARGETS = {
    "darwin-arm64": {
        "expected_os": "macOS 15",
        "arch": "arm64",
        "extension": ".dmg",
        "gates": {
            "codeSignaturePassed",
            "gatekeeperPassed",
            "hardenedRuntimePassed",
            "notarizationPassed",
            "staplingPassed",
        },
    },
    "darwin-x64": {
        "expected_os": "macOS 15",
        "arch": "x64",
        "extension": ".dmg",
        "gates": {
            "codeSignaturePassed",
            "gatekeeperPassed",
            "hardenedRuntimePassed",
            "notarizationPassed",
            "staplingPassed",
        },
    },
    "win32-x64": {
        "expected_os": "Windows 11",
        "arch": "x64",
        "extension": ".exe",
        "gates": {"authenticodePassed"},
    },
    "linux-x64": {
        "expected_os": "Ubuntu 24.04",
        "arch": "x64",
        "extension": ".deb",
        "gates": {"gpgSignaturePassed"},
    },
}
SELF_SIGNED_GATES = {
    "darwin-arm64": {"codeSignaturePassed", "hardenedRuntimePassed"},
    "darwin-x64": {"codeSignaturePassed", "hardenedRuntimePassed"},
    "win32-x64": {"authenticodePassed"},
    "linux-x64": {"gpgSignaturePassed"},
}
VALIDATION_ENVIRONMENTS = {
    "macos-15": {
        "target": "darwin-arm64",
        "expected_os": "macOS 15",
        "arch": "arm64",
        "host_arch": "arm64",
    },
    "macos-15-intel": {
        "target": "darwin-x64",
        "expected_os": "macOS 15",
        "arch": "x64",
        "host_arch": "x64",
    },
    "macos-26": {
        "target": "darwin-arm64",
        "expected_os": "macOS 26",
        "arch": "arm64",
        "host_arch": "arm64",
    },
    "macos-26-intel": {
        "target": "darwin-x64",
        "expected_os": "macOS 26",
        "arch": "x64",
        "host_arch": "x64",
    },
    "windows-11-arm": {
        "target": "win32-x64",
        "expected_os": "Windows 11",
        "arch": "x64",
        "host_arch": "arm64",
    },
    "ubuntu-24.04": {
        "target": "linux-x64",
        "expected_os": "Ubuntu 24.04",
        "arch": "x64",
        "host_arch": "x64",
    },
}
AGGREGATE_OUTPUTS = {
    "desktop-exact-head-evidence.json",
    "desktop-artifact-manifest.json",
    "desktop-sbom.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must be a regular, non-symlink file")
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular, non-symlink file")
    return resolved


def _refuse_existing_output(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite release output: {path.name}")


def _artifact(path: Path) -> dict[str, object]:
    return {
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def _validate_identity(git_head: str, version: str) -> None:
    if not COMMIT.fullmatch(git_head):
        raise ValueError("git head must be a lowercase 40-character commit SHA")
    if not SEMVER.fullmatch(version):
        raise ValueError("version must be stable SemVer")


def _positive_integer(value: int, label: str) -> int:
    if value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _required_gates(target: str, signing_mode: str) -> set[str]:
    if signing_mode == "unsigned":
        signing_gates: set[str] = set()
    elif signing_mode == "self-signed":
        signing_gates = SELF_SIGNED_GATES[target]
    else:
        signing_gates = set(TARGETS[target]["gates"])
    return COMMON_GATES | signing_gates


def create_target(args: argparse.Namespace) -> None:
    _validate_identity(args.git_head, args.version)
    validate_signing_mode(args.version, args.signing_mode)
    configuration = TARGETS.get(args.target)
    if configuration is None:
        raise ValueError(f"unsupported desktop release target: {args.target}")
    if args.expected_os != configuration["expected_os"]:
        raise ValueError("expected OS does not match the supported target matrix")
    if args.arch != configuration["arch"]:
        raise ValueError("architecture does not match the supported target matrix")

    gates = set(args.gate)
    required = _required_gates(args.target, args.signing_mode)
    missing = required - gates
    unexpected = gates - required
    if missing:
        raise ValueError(f"missing required verification gate: {sorted(missing)[0]}")
    if unexpected:
        raise ValueError(f"unexpected verification gates: {sorted(unexpected)}")

    installer = _regular_file(args.installer, "installer")
    if installer.suffix.lower() != configuration["extension"]:
        raise ValueError("installer extension does not match the supported target")
    sbom = _regular_file(args.sbom, "SBOM")
    sbom_payload = _load_object(sbom, "SBOM")
    if sbom_payload.get("bomFormat") != "CycloneDX":
        raise ValueError("SBOM must be a CycloneDX document")

    artifacts: dict[str, dict[str, object]] = {
        "installer": _artifact(installer),
        "sbom": _artifact(sbom),
    }
    if args.target == "linux-x64" and args.signing_mode != "unsigned":
        if args.signature is None:
            raise ValueError("Linux release evidence requires a detached GPG signature")
        signature = _regular_file(args.signature, "detached GPG signature")
        if signature.name != f"{installer.name}.asc":
            raise ValueError("detached GPG signature name must match the installer")
        artifacts["signature"] = _artifact(signature)
    elif args.signature is not None:
        raise ValueError("only the Linux target accepts a detached signature artifact")

    payload = {
        "schemaVersion": 2,
        "status": "passed",
        "gitHead": args.git_head,
        "version": args.version,
        "binarySigningMode": args.signing_mode,
        "target": args.target,
        "expectedOs": args.expected_os,
        "arch": args.arch,
        "gates": {gate: True for gate in sorted(gates)},
        "artifacts": artifacts,
    }
    _refuse_existing_output(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_validation_receipt(args: argparse.Namespace) -> None:
    """Record one supported runner's validation of exact installer bytes."""

    _validate_identity(args.git_head, args.version)
    configuration = VALIDATION_ENVIRONMENTS.get(args.runner)
    if configuration is None:
        raise ValueError(f"unsupported desktop validation runner: {args.runner}")
    if (
        args.target != configuration["target"]
        or args.expected_os != configuration["expected_os"]
        or args.actual_os != configuration["expected_os"]
        or args.arch != configuration["arch"]
        or args.host_arch != configuration["host_arch"]
    ):
        raise ValueError("runner does not match the supported validation matrix")

    gates = set(args.gate)
    missing = VALIDATION_GATES - gates
    unexpected = gates - VALIDATION_GATES
    if missing:
        raise ValueError(f"missing required validation gate: {sorted(missing)[0]}")
    if unexpected:
        raise ValueError(f"unexpected validation gates: {sorted(unexpected)}")

    installer = _regular_file(args.installer, "validation installer")
    target_configuration = TARGETS[args.target]
    if installer.suffix.lower() != target_configuration["extension"]:
        raise ValueError("validation installer extension does not match the supported target")
    run_id = _positive_integer(args.workflow_run_id, "workflow run ID")
    run_attempt = _positive_integer(args.workflow_run_attempt, "workflow run attempt")
    artifact_id = _positive_integer(args.source_artifact_id, "source artifact ID")
    if not ARTIFACT_DIGEST.fullmatch(args.source_artifact_digest):
        raise ValueError("source artifact digest must be a lowercase sha256 digest")

    payload = {
        "schemaVersion": 1,
        "status": "passed",
        "gitHead": args.git_head,
        "version": args.version,
        "runner": args.runner,
        "target": args.target,
        "expectedOs": args.expected_os,
        "actualOs": args.actual_os,
        "arch": args.arch,
        "hostArch": args.host_arch,
        "gates": {gate: True for gate in sorted(gates)},
        "installer": _artifact(installer),
        "workflow": {
            "runId": run_id,
            "runAttempt": run_attempt,
            "sourceArtifactId": artifact_id,
            "sourceArtifactDigest": args.source_artifact_digest,
        },
    }
    _refuse_existing_output(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _find_adjacent(evidence_path: Path, artifact: dict[str, Any], label: str) -> Path:
    name = artifact.get("name")
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError(f"{label}.name must be a safe basename")
    path = _regular_file(evidence_path.parent / name, label)
    if artifact.get("bytes") != path.stat().st_size or artifact.get("sha256") != _sha256(path):
        raise ValueError(f"{label} does not match its exact evidence digest")
    return path


def _target_ref(target: str, reference: object, label: str) -> str:
    if not isinstance(reference, str) or not reference:
        raise ValueError(f"{label} must be a non-empty string")
    return f"urn:ancestryllm:target:{target}:{reference}"


def _target_component(
    component: dict[str, Any], target: str, label: str
) -> tuple[dict[str, Any], set[str]]:
    copied = copy.deepcopy(component)
    properties = copied.get("properties", [])
    if not isinstance(properties, list) or not all(isinstance(item, dict) for item in properties):
        raise ValueError(f"{label} properties must be a list of objects")
    if any(item.get("name") == "ancestryllm:target" for item in properties):
        raise ValueError(f"{label} must not predeclare ancestryllm:target")
    copied["properties"] = [
        *properties,
        {"name": "ancestryllm:target", "value": target},
    ]

    references: set[str] = set()
    if "bom-ref" in copied:
        reference = _target_ref(target, copied["bom-ref"], f"{label} bom-ref")
        copied["bom-ref"] = reference
        references.add(reference)

    children = copied.get("components")
    if children is not None:
        if not isinstance(children, list) or not all(isinstance(child, dict) for child in children):
            raise ValueError(f"{label} nested components must be a list of objects")
        copied_children: list[dict[str, Any]] = []
        for index, child in enumerate(children):
            copied_child, child_references = _target_component(
                child, target, f"{label} nested component {index}"
            )
            if references & child_references:
                raise ValueError(f"{label} contains duplicate bom-ref values")
            references.update(child_references)
            copied_children.append(copied_child)
        copied["components"] = copied_children
    return copied, references


def _validated_environments(
    validation_dir: Path,
    git_head: str,
    version: str,
    targets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    receipt_paths = sorted(validation_dir.rglob("desktop-validation-receipt.json"))
    if len(receipt_paths) != len(VALIDATION_ENVIRONMENTS):
        raise ValueError(
            f"expected {len(VALIDATION_ENVIRONMENTS)} desktop validation receipt files"
        )

    validations: dict[str, dict[str, Any]] = {}
    for receipt_path in receipt_paths:
        payload = _load_object(receipt_path, "desktop validation receipt")
        runner = payload.get("runner")
        if runner not in VALIDATION_ENVIRONMENTS or runner in validations:
            raise ValueError(f"unexpected or duplicate desktop validation runner: {runner!r}")
        if payload.get("schemaVersion") != 1 or payload.get("status") != "passed":
            raise ValueError(f"desktop validation runner {runner} did not pass")
        if payload.get("gitHead") != git_head or payload.get("version") != version:
            raise ValueError(
                f"desktop validation runner {runner} is not bound to the release identity"
            )
        configuration = VALIDATION_ENVIRONMENTS[runner]
        if (
            payload.get("target") != configuration["target"]
            or payload.get("expectedOs") != configuration["expected_os"]
            or payload.get("actualOs") != configuration["expected_os"]
            or payload.get("arch") != configuration["arch"]
            or payload.get("hostArch") != configuration["host_arch"]
        ):
            raise ValueError(
                f"desktop validation runner {runner} does not match the supported matrix"
            )
        if payload.get("gates") != {gate: True for gate in sorted(VALIDATION_GATES)}:
            raise ValueError(
                f"desktop validation runner {runner} has incomplete verification gates"
            )
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict) or set(workflow) != {
            "runId",
            "runAttempt",
            "sourceArtifactId",
            "sourceArtifactDigest",
        }:
            raise ValueError(
                f"desktop validation runner {runner} workflow provenance is incomplete"
            )
        for key, label in (
            ("runId", "workflow run ID"),
            ("runAttempt", "workflow run attempt"),
            ("sourceArtifactId", "source artifact ID"),
        ):
            value = workflow.get(key)
            if not isinstance(value, int):
                raise ValueError(f"desktop validation runner {runner} {label} is invalid")
            _positive_integer(value, label)
        digest = workflow.get("sourceArtifactDigest")
        if not isinstance(digest, str) or not ARTIFACT_DIGEST.fullmatch(digest):
            raise ValueError(
                f"desktop validation runner {runner} source artifact digest is invalid"
            )
        installer = payload.get("installer")
        if not isinstance(installer, dict):
            raise ValueError(f"desktop validation runner {runner} installer must be an object")
        _find_adjacent(receipt_path, installer, f"{runner}.installer")

        target = configuration["target"]
        target_installer = targets[target]["artifacts"]["installer"]
        if installer != target_installer:
            raise ValueError(
                f"desktop validation runner {runner} is not bound to the target installer digest"
            )
        validations[runner] = payload

    if set(validations) != set(VALIDATION_ENVIRONMENTS):
        raise ValueError("desktop validation environment matrix is incomplete")
    return [validations[runner] for runner in sorted(validations)]


def aggregate(args: argparse.Namespace) -> None:
    _validate_identity(args.git_head, args.version)
    evidence_paths = sorted(args.input_dir.rglob("desktop-target-evidence.json"))
    if len(evidence_paths) != len(TARGETS):
        raise ValueError(f"expected {len(TARGETS)} desktop target evidence files")

    targets: dict[str, dict[str, Any]] = {}
    resolved_assets: dict[str, Path] = {}
    sbom_components: list[dict[str, Any]] = []
    sbom_dependencies: list[dict[str, Any]] = []
    sbom_references: set[str] = set()
    dependency_references: set[str] = set()
    target_root_references: list[str] = []
    for evidence_path in evidence_paths:
        payload = _load_object(evidence_path, "desktop target evidence")
        target = payload.get("target")
        if target not in TARGETS or target in targets:
            raise ValueError(f"unexpected or duplicate desktop target: {target!r}")
        if payload.get("schemaVersion") != 2 or payload.get("status") != "passed":
            raise ValueError(f"desktop target {target} evidence did not pass")
        if payload.get("gitHead") != args.git_head or payload.get("version") != args.version:
            raise ValueError(f"desktop target {target} is not bound to the release identity")
        signing_mode = payload.get("binarySigningMode")
        if not isinstance(signing_mode, str):
            raise ValueError(f"desktop target {target} does not declare a binary-signing mode")
        validate_signing_mode(args.version, signing_mode)
        configuration = TARGETS[target]
        if (
            payload.get("expectedOs") != configuration["expected_os"]
            or payload.get("arch") != configuration["arch"]
        ):
            raise ValueError(f"desktop target {target} does not match the supported matrix")
        required = _required_gates(target, signing_mode)
        if payload.get("gates") != {gate: True for gate in sorted(required)}:
            raise ValueError(f"desktop target {target} has incomplete verification gates")
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError(f"desktop target {target} artifacts must be an object")
        expected_artifacts = {"installer", "sbom"} | (
            {"signature"} if target == "linux-x64" and signing_mode != "unsigned" else set()
        )
        if set(artifacts) != expected_artifacts:
            raise ValueError(f"desktop target {target} artifact inventory is incomplete")
        for label, artifact in artifacts.items():
            if not isinstance(artifact, dict):
                raise ValueError(f"desktop target {target} {label} must be an object")
            path = _find_adjacent(evidence_path, artifact, f"{target}.{label}")
            if path.name in resolved_assets:
                raise ValueError(f"duplicate desktop release asset name: {path.name}")
            resolved_assets[path.name] = path
            if label == "sbom":
                sbom_payload = _load_object(path, f"{target} SBOM")
                if sbom_payload.get("bomFormat") != "CycloneDX":
                    raise ValueError(f"{target} SBOM must be CycloneDX")
                metadata = sbom_payload.get("metadata")
                if not isinstance(metadata, dict) or not isinstance(
                    metadata.get("component"), dict
                ):
                    raise ValueError(f"{target} SBOM must identify its root component")
                root_component = metadata["component"]
                if root_component.get("version") != args.version:
                    raise ValueError(f"{target} SBOM root version is not the release version")
                components = sbom_payload.get("components", [])
                if not isinstance(components, list) or not all(
                    isinstance(component, dict) for component in components
                ):
                    raise ValueError(f"{target} SBOM components must be a list of objects")
                target_components = [root_component, *components]
                target_references: set[str] = set()
                for index, component in enumerate(target_components):
                    copied_component, component_references = _target_component(
                        component, target, f"{target} SBOM component {index}"
                    )
                    if target_references & component_references:
                        raise ValueError(f"{target} SBOM contains duplicate bom-ref values")
                    target_references.update(component_references)
                    sbom_components.append(copied_component)
                root_reference = _target_ref(
                    target, root_component.get("bom-ref"), f"{target} SBOM root bom-ref"
                )
                if root_reference not in target_references:
                    raise ValueError(f"{target} SBOM root bom-ref was not preserved")
                if sbom_references & target_references:
                    raise ValueError("combined desktop SBOM contains duplicate bom-ref values")
                sbom_references.update(target_references)
                target_root_references.append(root_reference)

                dependencies = sbom_payload.get("dependencies", [])
                if not isinstance(dependencies, list) or not all(
                    isinstance(dependency, dict) for dependency in dependencies
                ):
                    raise ValueError(f"{target} SBOM dependencies must be a list of objects")
                for index, dependency in enumerate(dependencies):
                    copied_dependency = copy.deepcopy(dependency)
                    reference = _target_ref(
                        target,
                        copied_dependency.get("ref"),
                        f"{target} SBOM dependency {index} ref",
                    )
                    if reference in dependency_references:
                        raise ValueError(f"{target} SBOM contains duplicate dependency refs")
                    copied_dependency["ref"] = reference
                    for key in ("dependsOn", "provides"):
                        related = copied_dependency.get(key, [])
                        if not isinstance(related, list) or not all(
                            isinstance(item, str) and item for item in related
                        ):
                            raise ValueError(
                                f"{target} SBOM dependency {index} {key} must be strings"
                            )
                        copied_dependency[key] = [
                            _target_ref(
                                target,
                                item,
                                f"{target} SBOM dependency {index} {key}",
                            )
                            for item in related
                        ]
                    referenced = {
                        reference,
                        *copied_dependency.get("dependsOn", []),
                        *copied_dependency.get("provides", []),
                    }
                    if not referenced <= target_references:
                        raise ValueError(
                            f"{target} SBOM dependency graph references an unknown component"
                        )
                    dependency_references.add(reference)
                    sbom_dependencies.append(copied_dependency)
        targets[target] = payload

    if set(targets) != set(TARGETS):
        raise ValueError("desktop release target matrix is incomplete")
    signing_modes = {payload["binarySigningMode"] for payload in targets.values()}
    if len(signing_modes) != 1:
        raise ValueError("desktop release target matrix mixes binary-signing modes")
    binary_signing_mode = signing_modes.pop()
    validations = (
        _validated_environments(args.validation_dir, args.git_head, args.version, targets)
        if args.validation_dir is not None
        else None
    )
    if args.output_dir.is_symlink():
        raise ValueError("release output directory must not be a symlink")
    destinations = [
        *(args.output_dir / name for name in resolved_assets),
        *(args.output_dir / name for name in AGGREGATE_OUTPUTS),
    ]
    for destination in destinations:
        _refuse_existing_output(destination)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, source in sorted(resolved_assets.items()):
        shutil.copyfile(source, args.output_dir / name)

    evidence = {
        "schemaVersion": 3,
        "status": "passed",
        "gitHead": args.git_head,
        "version": args.version,
        "binarySigningMode": binary_signing_mode,
        "binarySigningDisclosure": signing_disclosure(args.version, binary_signing_mode),
        "targets": [targets[target] for target in sorted(targets)],
    }
    if validations is not None:
        evidence["platformValidated"] = True
        evidence["validations"] = validations
    manifest = {
        "schemaVersion": 2,
        "gitHead": args.git_head,
        "version": args.version,
        "binarySigningMode": binary_signing_mode,
        "binarySigningDisclosure": signing_disclosure(args.version, binary_signing_mode),
        "artifacts": [
            {"name": name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for name, path in sorted(resolved_assets.items())
        ],
    }
    aggregate_reference = f"urn:ancestryllm:desktop:{args.version}"
    combined_sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "AncestryLLM Desktop",
                "version": args.version,
                "bom-ref": aggregate_reference,
            }
        },
        "components": sbom_components,
        "dependencies": [
            {"ref": aggregate_reference, "dependsOn": sorted(target_root_references)},
            *sbom_dependencies,
        ],
    }
    outputs = {
        "desktop-exact-head-evidence.json": evidence,
        "desktop-artifact-manifest.json": manifest,
        "desktop-sbom.json": combined_sbom,
    }
    assert set(outputs) == AGGREGATE_OUTPUTS
    for name, payload in outputs.items():
        (args.output_dir / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    target = subparsers.add_parser("target", help="create one installer evidence row")
    target.add_argument("--git-head", required=True)
    target.add_argument("--version", required=True)
    target.add_argument(
        "--signing-mode",
        required=True,
        choices=("unsigned", "self-signed", "trusted"),
    )
    target.add_argument("--target", required=True)
    target.add_argument("--expected-os", required=True)
    target.add_argument("--arch", required=True)
    target.add_argument("--installer", required=True, type=Path)
    target.add_argument("--signature", type=Path)
    target.add_argument("--sbom", required=True, type=Path)
    target.add_argument("--gate", action="append", default=[])
    target.add_argument("--output", required=True, type=Path)
    target.set_defaults(handler=create_target)

    validation = subparsers.add_parser(
        "validation-receipt",
        help="record one supported runner's validation of exact installer bytes",
    )
    validation.add_argument("--git-head", required=True)
    validation.add_argument("--version", required=True)
    validation.add_argument("--runner", required=True)
    validation.add_argument("--target", required=True)
    validation.add_argument("--expected-os", required=True)
    validation.add_argument("--actual-os", required=True)
    validation.add_argument("--arch", required=True)
    validation.add_argument("--host-arch", required=True)
    validation.add_argument("--installer", required=True, type=Path)
    validation.add_argument("--workflow-run-id", required=True, type=int)
    validation.add_argument("--workflow-run-attempt", required=True, type=int)
    validation.add_argument("--source-artifact-id", required=True, type=int)
    validation.add_argument("--source-artifact-digest", required=True)
    validation.add_argument("--gate", action="append", default=[])
    validation.add_argument("--output", required=True, type=Path)
    validation.set_defaults(handler=create_validation_receipt)

    aggregate_parser = subparsers.add_parser("aggregate", help="aggregate the exact release matrix")
    aggregate_parser.add_argument("--git-head", required=True)
    aggregate_parser.add_argument("--version", required=True)
    aggregate_parser.add_argument("--input-dir", required=True, type=Path)
    aggregate_parser.add_argument("--validation-dir", type=Path)
    aggregate_parser.add_argument("--output-dir", required=True, type=Path)
    aggregate_parser.set_defaults(handler=aggregate)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        args.handler(args)
    except (OSError, ValueError) as exc:
        print(f"desktop release evidence error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
