"""Tests for fail-closed desktop release evidence assembly."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLER = ROOT / "scripts" / "assemble_desktop_release.py"
GIT_HEAD = "a" * 40
VERSION = "1.0.0"
COMMON_GATES = (
    "exactHeadPassed",
    "installerBuiltPassed",
    "installedRuntimePassed",
    "runtimeIsolationPassed",
    "sbomGeneratedPassed",
    "sidecarHandshakePassed",
)
TARGETS = {
    "darwin-arm64": (
        "macOS 15",
        "arm64",
        ".dmg",
        (
            "codeSignaturePassed",
            "gatekeeperPassed",
            "hardenedRuntimePassed",
            "notarizationPassed",
            "staplingPassed",
        ),
    ),
    "darwin-x64": (
        "macOS 15",
        "x64",
        ".dmg",
        (
            "codeSignaturePassed",
            "gatekeeperPassed",
            "hardenedRuntimePassed",
            "notarizationPassed",
            "staplingPassed",
        ),
    ),
    "win32-arm64": ("Windows 11", "arm64", ".exe", ("authenticodePassed",)),
    "linux-x64": ("Ubuntu 24.04", "x64", ".deb", ("gpgSignaturePassed",)),
}


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ASSEMBLER), *arguments],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def _create_target(root: Path, target: str) -> tuple[Path, Path]:
    expected_os, arch, extension, target_gates = TARGETS[target]
    target_dir = root / target
    target_dir.mkdir()
    installer = target_dir / f"AncestryLLM-{VERSION}-{target}{extension}"
    installer.write_bytes(f"signed installer for {target}".encode())
    sbom = target_dir / f"desktop-{target}.cdx.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "metadata": {
                    "component": {
                        "type": "application",
                        "name": "ancestryllm-desktop",
                        "version": VERSION,
                        "bom-ref": f"pkg:npm/ancestryllm-desktop@{VERSION}",
                    }
                },
                "components": [
                    {
                        "type": "library",
                        "name": "fictional-component",
                        "version": "1.0.0",
                        "bom-ref": "pkg:npm/fictional-component@1.0.0",
                        "properties": [{"name": "existing", "value": target}],
                    }
                ],
                "dependencies": [
                    {
                        "ref": f"pkg:npm/ancestryllm-desktop@{VERSION}",
                        "dependsOn": ["pkg:npm/fictional-component@1.0.0"],
                    },
                    {
                        "ref": "pkg:npm/fictional-component@1.0.0",
                        "dependsOn": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    evidence = target_dir / "desktop-target-evidence.json"
    arguments = [
        "target",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--signing-mode",
        "trusted",
        "--target",
        target,
        "--expected-os",
        expected_os,
        "--arch",
        arch,
        "--installer",
        str(installer),
        "--sbom",
        str(sbom),
        "--output",
        str(evidence),
    ]
    if target == "linux-x64":
        signature = target_dir / f"{installer.name}.asc"
        signature.write_text("detached signature", encoding="utf-8")
        arguments.extend(("--signature", str(signature)))
    for gate in (*COMMON_GATES, *target_gates):
        arguments.extend(("--gate", gate))

    completed = _run(*arguments)

    assert completed.returncode == 0, completed.stderr
    return evidence, installer


def test_aggregate_binds_exact_matrix_assets_evidence_and_combined_sbom(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    installers = {target: _create_target(inputs, target)[1] for target in TARGETS}
    output = tmp_path / "release"

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--input-dir",
        str(inputs),
        "--output-dir",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    evidence = json.loads((output / "desktop-exact-head-evidence.json").read_text(encoding="utf-8"))
    assert evidence["schemaVersion"] == 3
    assert evidence["status"] == "passed"
    assert evidence["gitHead"] == GIT_HEAD
    assert evidence["binarySigningMode"] == "trusted"
    assert "must carry a verifiable publisher identity" in evidence["binarySigningDisclosure"]
    assert (
        "annotated release tag must pass signature verification"
        in evidence["binarySigningDisclosure"]
    )
    assert {row["target"] for row in evidence["targets"]} == set(TARGETS)
    manifest = json.loads((output / "desktop-artifact-manifest.json").read_text(encoding="utf-8"))
    assert manifest["binarySigningDisclosure"] == evidence["binarySigningDisclosure"]
    expected_assets = {path.name for path in installers.values()} | {
        f"{installers['linux-x64'].name}.asc",
        *(f"desktop-{target}.cdx.json" for target in TARGETS),
    }
    assert {item["name"] for item in manifest["artifacts"]} == expected_assets
    for item in manifest["artifacts"]:
        artifact = output / item["name"]
        assert item["bytes"] == artifact.stat().st_size
        assert item["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    sbom = json.loads((output / "desktop-sbom.json").read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["metadata"]["component"]["version"] == VERSION
    assert len(sbom["components"]) == len(TARGETS) * 2
    component_refs = [component["bom-ref"] for component in sbom["components"]]
    assert len(component_refs) == len(set(component_refs))
    assert all(ref.startswith("urn:ancestryllm:target:") for ref in component_refs)
    for component in sbom["components"]:
        target_properties = [
            prop for prop in component["properties"] if prop["name"] == "ancestryllm:target"
        ]
        assert len(target_properties) == 1
        assert target_properties[0]["value"] in TARGETS
        if component["name"] == "fictional-component":
            assert {prop["name"] for prop in component["properties"]} == {
                "existing",
                "ancestryllm:target",
            }
    dependency_refs = {
        reference
        for dependency in sbom["dependencies"]
        for reference in (dependency["ref"], *dependency.get("dependsOn", []))
    }
    assert dependency_refs <= set(component_refs) | {sbom["metadata"]["component"]["bom-ref"]}
    aggregate_dependency = next(
        dependency
        for dependency in sbom["dependencies"]
        if dependency["ref"] == sbom["metadata"]["component"]["bom-ref"]
    )
    assert len(aggregate_dependency["dependsOn"]) == len(TARGETS)


def test_aggregate_rejects_an_installer_changed_after_evidence(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    installers = {target: _create_target(inputs, target)[1] for target in TARGETS}
    installers["win32-arm64"].write_bytes(b"tampered")

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--input-dir",
        str(inputs),
        "--output-dir",
        str(tmp_path / "release"),
    )

    assert completed.returncode != 0
    assert "does not match its exact evidence digest" in completed.stderr


def test_aggregate_refuses_to_overwrite_an_existing_release_asset(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    installers = {target: _create_target(inputs, target)[1] for target in TARGETS}
    output = tmp_path / "release"
    output.mkdir()
    existing = output / installers["win32-arm64"].name
    existing.write_bytes(b"existing release artifact")

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--input-dir",
        str(inputs),
        "--output-dir",
        str(output),
    )

    assert completed.returncode != 0
    assert "refusing to overwrite release output" in completed.stderr
    assert existing.read_bytes() == b"existing release artifact"


def test_target_refuses_to_overwrite_existing_evidence(tmp_path: Path) -> None:
    target = "darwin-arm64"
    expected_os, arch, extension, target_gates = TARGETS[target]
    installer = tmp_path / f"AncestryLLM-{VERSION}-{target}{extension}"
    installer.write_bytes(b"signed installer")
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
    evidence = tmp_path / "desktop-target-evidence.json"
    evidence.write_text("preserve me\n", encoding="utf-8")
    arguments = [
        "target",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--signing-mode",
        "trusted",
        "--target",
        target,
        "--expected-os",
        expected_os,
        "--arch",
        arch,
        "--installer",
        str(installer),
        "--sbom",
        str(sbom),
        "--output",
        str(evidence),
    ]
    for gate in (*COMMON_GATES, *target_gates):
        arguments.extend(("--gate", gate))

    completed = _run(*arguments)

    assert completed.returncode != 0
    assert "refusing to overwrite release output" in completed.stderr
    assert evidence.read_text(encoding="utf-8") == "preserve me\n"


def test_pre_1_target_accepts_unsigned_build_without_signing_gates(tmp_path: Path) -> None:
    installer = tmp_path / "AncestryLLM-0.5.0-win32-arm64.exe"
    installer.write_bytes(b"unsigned installer")
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
    evidence = tmp_path / "desktop-target-evidence.json"
    arguments = [
        "target",
        "--git-head",
        GIT_HEAD,
        "--version",
        "0.5.0",
        "--signing-mode",
        "unsigned",
        "--target",
        "win32-arm64",
        "--expected-os",
        "Windows 11",
        "--arch",
        "arm64",
        "--installer",
        str(installer),
        "--sbom",
        str(sbom),
        "--output",
        str(evidence),
    ]
    for gate in (*COMMON_GATES, "unsignedArtifactPassed"):
        arguments.extend(("--gate", gate))

    completed = _run(*arguments)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["binarySigningMode"] == "unsigned"
    assert payload["gates"]["unsignedArtifactPassed"] is True
    assert "authenticodePassed" not in payload["gates"]


def test_pre_1_target_rejects_self_signed_build(tmp_path: Path) -> None:
    installer = tmp_path / "AncestryLLM-0.5.0-darwin-arm64.dmg"
    installer.write_bytes(b"self-signed installer")
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
    evidence = tmp_path / "desktop-target-evidence.json"
    arguments = [
        "target",
        "--git-head",
        GIT_HEAD,
        "--version",
        "0.5.0",
        "--signing-mode",
        "self-signed",
        "--target",
        "darwin-arm64",
        "--expected-os",
        "macOS 15",
        "--arch",
        "arm64",
        "--installer",
        str(installer),
        "--sbom",
        str(sbom),
        "--output",
        str(evidence),
    ]
    for gate in (*COMMON_GATES, "codeSignaturePassed", "hardenedRuntimePassed"):
        arguments.extend(("--gate", gate))

    completed = _run(*arguments)

    assert completed.returncode != 0
    assert "self-signed" in completed.stderr
    assert not evidence.exists()


def test_target_rejects_symlinked_installer(tmp_path: Path) -> None:
    installer = tmp_path / "real.deb"
    installer.write_bytes(b"installer")
    symlink = tmp_path / "AncestryLLM-0.5.0-linux-x64.deb"
    symlink.symlink_to(installer)
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
    arguments = [
        "target",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--signing-mode",
        "trusted",
        "--target",
        "linux-x64",
        "--expected-os",
        "Ubuntu 24.04",
        "--arch",
        "x64",
        "--installer",
        str(symlink),
        "--sbom",
        str(sbom),
        "--output",
        str(tmp_path / "evidence.json"),
    ]
    for gate in (*COMMON_GATES, "gpgSignaturePassed"):
        arguments.extend(("--gate", gate))

    completed = _run(*arguments)

    assert completed.returncode != 0
    assert "regular, non-symlink file" in completed.stderr


def _create_unsigned_target(root: Path, target: str, version: str = "0.5.0") -> tuple[Path, Path]:
    """Create a pre-1.0 target evidence row with unsigned signing mode."""
    expected_os, arch, extension, _trusted_gates = TARGETS[target]
    target_dir = root / target
    target_dir.mkdir()
    installer = target_dir / f"AncestryLLM-{version}-{target}{extension}"
    installer.write_bytes(f"unsigned installer for {target}".encode())
    sbom = target_dir / f"desktop-{target}.cdx.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "metadata": {
                    "component": {
                        "type": "application",
                        "name": "ancestryllm-desktop",
                        "version": version,
                        "bom-ref": f"pkg:npm/ancestryllm-desktop@{version}",
                    }
                },
                "components": [
                    {
                        "type": "library",
                        "name": "fictional-unsigned-component",
                        "version": "1.0.0",
                        "bom-ref": "pkg:npm/fictional-unsigned-component@1.0.0",
                        "properties": [{"name": "existing", "value": target}],
                    }
                ],
                "dependencies": [
                    {
                        "ref": f"pkg:npm/ancestryllm-desktop@{version}",
                        "dependsOn": ["pkg:npm/fictional-unsigned-component@1.0.0"],
                    },
                    {
                        "ref": "pkg:npm/fictional-unsigned-component@1.0.0",
                        "dependsOn": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    evidence = target_dir / "desktop-target-evidence.json"
    arguments = [
        "target",
        "--git-head",
        GIT_HEAD,
        "--version",
        version,
        "--signing-mode",
        "unsigned",
        "--target",
        target,
        "--expected-os",
        expected_os,
        "--arch",
        arch,
        "--installer",
        str(installer),
        "--sbom",
        str(sbom),
        "--output",
        str(evidence),
    ]
    for gate in COMMON_GATES:
        arguments.extend(("--gate", gate))
    arguments.extend(("--gate", "unsignedArtifactPassed"))

    completed = _run(*arguments)
    assert completed.returncode == 0, completed.stderr
    return evidence, installer


def test_pre_1_aggregate_assembles_unsigned_matrix_with_no_signing_disclosure(
    tmp_path: Path,
) -> None:
    """v0.5.0 official aggregate must use unsigned mode and include the pre-1.0 disclosure."""
    pre_1_version = "0.5.0"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    installers = {
        target: _create_unsigned_target(inputs, target, pre_1_version)[1] for target in TARGETS
    }
    output = tmp_path / "release"

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        pre_1_version,
        "--input-dir",
        str(inputs),
        "--output-dir",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    evidence = json.loads((output / "desktop-exact-head-evidence.json").read_text(encoding="utf-8"))
    assert evidence["schemaVersion"] == 3
    assert evidence["status"] == "passed"
    assert evidence["gitHead"] == GIT_HEAD
    assert evidence["version"] == pre_1_version
    assert evidence["binarySigningMode"] == "unsigned"
    assert (
        "pre-1.0 release" in evidence["binarySigningDisclosure"].lower()
        or "pre-1.0" in evidence["binarySigningDisclosure"]
    )
    assert "unsigned" in evidence["binarySigningDisclosure"]
    assert {row["target"] for row in evidence["targets"]} == set(TARGETS)
    manifest = json.loads((output / "desktop-artifact-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 2
    assert manifest["version"] == pre_1_version
    assert manifest["binarySigningMode"] == "unsigned"
    assert manifest["binarySigningDisclosure"] == evidence["binarySigningDisclosure"]
    expected_assets = {path.name for path in installers.values()} | {
        f"desktop-{target}.cdx.json" for target in TARGETS
    }
    assert {item["name"] for item in manifest["artifacts"]} == expected_assets


def test_pre_1_aggregate_rejects_trusted_signing_mode(tmp_path: Path) -> None:
    """v0.5.0 aggregate must reject trusted signing mode; trusted is reserved for v1.0.0+."""
    pre_1_version = "0.5.0"
    inputs = tmp_path / "inputs"
    inputs.mkdir()

    for target in TARGETS:
        expected_os, arch, extension, target_gates = TARGETS[target]
        target_dir = inputs / target
        target_dir.mkdir()
        installer = target_dir / f"AncestryLLM-{pre_1_version}-{target}{extension}"
        installer.write_bytes(f"fake installer {target}".encode())
        sbom = target_dir / f"desktop-{target}.cdx.json"
        sbom.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
        evidence = target_dir / "desktop-target-evidence.json"
        evidence.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "status": "passed",
                    "gitHead": GIT_HEAD,
                    "version": pre_1_version,
                    "target": target,
                    "expectedOs": expected_os,
                    "arch": arch,
                    "binarySigningMode": "trusted",
                    "gates": {gate: True for gate in sorted((*COMMON_GATES, *target_gates))},
                    "artifacts": {
                        "installer": {
                            "name": installer.name,
                            "bytes": installer.stat().st_size,
                            "sha256": "a" * 64,
                        },
                        "sbom": {
                            "name": sbom.name,
                            "bytes": sbom.stat().st_size,
                            "sha256": "b" * 64,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        pre_1_version,
        "--input-dir",
        str(inputs),
        "--output-dir",
        str(tmp_path / "release"),
    )

    assert completed.returncode != 0
    assert "trusted" in completed.stderr.lower() or "signing" in completed.stderr.lower()
