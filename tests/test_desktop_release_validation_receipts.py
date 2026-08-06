"""Tests for platform validation receipts bound to desktop installer digests."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLER = ROOT / "scripts" / "assemble_desktop_release.py"
GIT_HEAD = "b" * 40
VERSION = "1.0.0"
WORKFLOW_RUN_ID = 123456789
WORKFLOW_RUN_ATTEMPT = 1
SOURCE_ARTIFACT_ID = 987654321
SOURCE_ARTIFACT_DIGEST = f"sha256:{'a' * 64}"
COMMON_GATES = (
    "exactHeadPassed",
    "installerBuiltPassed",
    "installedRuntimePassed",
    "runtimeIsolationPassed",
    "sbomGeneratedPassed",
    "sidecarHandshakePassed",
)
VALIDATION_GATES = (
    "exactHeadPassed",
    "installedRuntimePassed",
    "operatingSystemPassed",
    "runtimeIsolationPassed",
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
VALIDATIONS = {
    "macos-15": ("darwin-arm64", "macOS 15", "arm64"),
    "macos-15-intel": ("darwin-x64", "macOS 15", "x64"),
    "macos-26": ("darwin-arm64", "macOS 26", "arm64"),
    "macos-26-intel": ("darwin-x64", "macOS 26", "x64"),
    "windows-11-arm": ("win32-arm64", "Windows 11", "arm64"),
    "ubuntu-24.04": ("linux-x64", "Ubuntu 24.04", "x64"),
}


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ASSEMBLER), *arguments],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def _create_target(root: Path, target: str) -> Path:
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
                "components": [],
                "dependencies": [
                    {
                        "ref": f"pkg:npm/ancestryllm-desktop@{VERSION}",
                        "dependsOn": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
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
        str(target_dir / "desktop-target-evidence.json"),
    ]
    if target == "linux-x64":
        signature = target_dir / f"{installer.name}.asc"
        signature.write_text("detached signature", encoding="utf-8")
        arguments.extend(("--signature", str(signature)))
    for gate in (*COMMON_GATES, *target_gates):
        arguments.extend(("--gate", gate))
    completed = _run(*arguments)
    assert completed.returncode == 0, completed.stderr
    return installer


def _create_validation_receipt(root: Path, runner: str, source_installer: Path) -> Path:
    target, expected_os, arch = VALIDATIONS[runner]
    receipt_dir = root / runner
    receipt_dir.mkdir(parents=True)
    installer = receipt_dir / source_installer.name
    shutil.copyfile(source_installer, installer)
    arguments = [
        "validation-receipt",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--runner",
        runner,
        "--target",
        target,
        "--expected-os",
        expected_os,
        "--actual-os",
        expected_os,
        "--arch",
        arch,
        "--installer",
        str(installer),
        "--workflow-run-id",
        str(WORKFLOW_RUN_ID),
        "--workflow-run-attempt",
        str(WORKFLOW_RUN_ATTEMPT),
        "--source-artifact-id",
        str(SOURCE_ARTIFACT_ID),
        "--source-artifact-digest",
        SOURCE_ARTIFACT_DIGEST,
        "--output",
        str(receipt_dir / "desktop-validation-receipt.json"),
    ]
    for gate in VALIDATION_GATES:
        arguments.extend(("--gate", gate))
    completed = _run(*arguments)
    assert completed.returncode == 0, completed.stderr
    return receipt_dir / "desktop-validation-receipt.json"


def _create_release_inputs(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    inputs = tmp_path / "inputs"
    validations = tmp_path / "validations"
    inputs.mkdir()
    validations.mkdir()
    installers = {target: _create_target(inputs, target) for target in TARGETS}
    for runner, (target, _expected_os, _arch) in VALIDATIONS.items():
        _create_validation_receipt(validations, runner, installers[target])
    return inputs, validations, installers


def test_validation_receipt_rejects_runner_target_matrix_mismatch(tmp_path: Path) -> None:
    installer = tmp_path / "AncestryLLM-0.5.0-darwin-x64.dmg"
    installer.write_bytes(b"signed installer")

    completed = _run(
        "validation-receipt",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--runner",
        "macos-26",
        "--target",
        "darwin-x64",
        "--expected-os",
        "macOS 26",
        "--actual-os",
        "macOS 26",
        "--arch",
        "x64",
        "--installer",
        str(installer),
        "--workflow-run-id",
        str(WORKFLOW_RUN_ID),
        "--workflow-run-attempt",
        str(WORKFLOW_RUN_ATTEMPT),
        "--source-artifact-id",
        str(SOURCE_ARTIFACT_ID),
        "--source-artifact-digest",
        SOURCE_ARTIFACT_DIGEST,
        *(argument for gate in VALIDATION_GATES for argument in ("--gate", gate)),
        "--output",
        str(tmp_path / "desktop-validation-receipt.json"),
    )

    assert completed.returncode != 0
    assert "supported validation matrix" in completed.stderr


def test_validation_receipt_requires_observed_operating_system(tmp_path: Path) -> None:
    installer = tmp_path / "AncestryLLM-0.5.0-linux-x64.deb"
    installer.write_bytes(b"signed installer")

    completed = _run(
        "validation-receipt",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--runner",
        "ubuntu-24.04",
        "--target",
        "linux-x64",
        "--expected-os",
        "Ubuntu 24.04",
        "--arch",
        "x64",
        "--installer",
        str(installer),
        "--workflow-run-id",
        str(WORKFLOW_RUN_ID),
        "--workflow-run-attempt",
        str(WORKFLOW_RUN_ATTEMPT),
        "--source-artifact-id",
        str(SOURCE_ARTIFACT_ID),
        "--source-artifact-digest",
        SOURCE_ARTIFACT_DIGEST,
        *(argument for gate in VALIDATION_GATES for argument in ("--gate", gate)),
        "--output",
        str(tmp_path / "desktop-validation-receipt.json"),
    )

    assert completed.returncode != 0
    assert "--actual-os" in completed.stderr


def test_validation_receipt_rejects_observed_operating_system_mismatch(
    tmp_path: Path,
) -> None:
    installer = tmp_path / "AncestryLLM-0.5.0-win32-arm64.exe"
    installer.write_bytes(b"signed installer")

    completed = _run(
        "validation-receipt",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--runner",
        "windows-11-arm",
        "--target",
        "win32-arm64",
        "--expected-os",
        "Windows 11",
        "--actual-os",
        "Windows Server 2025",
        "--arch",
        "arm64",
        "--installer",
        str(installer),
        "--workflow-run-id",
        str(WORKFLOW_RUN_ID),
        "--workflow-run-attempt",
        str(WORKFLOW_RUN_ATTEMPT),
        "--source-artifact-id",
        str(SOURCE_ARTIFACT_ID),
        "--source-artifact-digest",
        SOURCE_ARTIFACT_DIGEST,
        *(argument for gate in VALIDATION_GATES for argument in ("--gate", gate)),
        "--output",
        str(tmp_path / "desktop-validation-receipt.json"),
    )

    assert completed.returncode != 0
    assert "supported validation matrix" in completed.stderr


def test_aggregate_joins_six_validation_environments_to_four_installers(
    tmp_path: Path,
) -> None:
    inputs, validations, installers = _create_release_inputs(tmp_path)
    output = tmp_path / "release"

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--input-dir",
        str(inputs),
        "--validation-dir",
        str(validations),
        "--output-dir",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    evidence = json.loads((output / "desktop-exact-head-evidence.json").read_text(encoding="utf-8"))
    assert evidence["platformValidated"] is True
    assert {row["runner"] for row in evidence["validations"]} == set(VALIDATIONS)
    installer_digests = {
        row["target"]: row["artifacts"]["installer"]["sha256"] for row in evidence["targets"]
    }
    for validation in evidence["validations"]:
        assert validation["actualOs"] == validation["expectedOs"]
        assert validation["gates"]["operatingSystemPassed"] is True
        assert validation["installer"]["sha256"] == installer_digests[validation["target"]]
    macos_15 = next(row for row in evidence["validations"] if row["runner"] == "macos-15")
    macos_26 = next(row for row in evidence["validations"] if row["runner"] == "macos-26")
    assert macos_15["target"] == macos_26["target"] == "darwin-arm64"
    assert macos_15["installer"] == macos_26["installer"]

    manifest = json.loads((output / "desktop-artifact-manifest.json").read_text(encoding="utf-8"))
    installer_names = {path.name for path in installers.values()}
    assert {
        artifact["name"]
        for artifact in manifest["artifacts"]
        if Path(artifact["name"]).suffix in {".dmg", ".exe", ".deb"}
    } == installer_names


def test_aggregate_rejects_an_incomplete_validation_matrix(tmp_path: Path) -> None:
    inputs, validations, _installers = _create_release_inputs(tmp_path)
    (validations / "macos-26" / "desktop-validation-receipt.json").unlink()

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--input-dir",
        str(inputs),
        "--validation-dir",
        str(validations),
        "--output-dir",
        str(tmp_path / "release"),
    )

    assert completed.returncode != 0
    assert "expected 6 desktop validation receipt files" in completed.stderr


def test_aggregate_rejects_a_duplicate_validation_record(tmp_path: Path) -> None:
    inputs, validations, _installers = _create_release_inputs(tmp_path)
    missing = validations / "macos-26-intel" / "desktop-validation-receipt.json"
    duplicate = validations / "macos-26" / "desktop-validation-receipt.json"
    shutil.copyfile(duplicate, missing)

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--input-dir",
        str(inputs),
        "--validation-dir",
        str(validations),
        "--output-dir",
        str(tmp_path / "release"),
    )

    assert completed.returncode != 0
    assert "unexpected or duplicate desktop validation runner" in completed.stderr


def test_aggregate_rejects_an_extra_validation_record(tmp_path: Path) -> None:
    inputs, validations, _installers = _create_release_inputs(tmp_path)
    extra = validations / "extra"
    extra.mkdir()
    shutil.copyfile(
        validations / "ubuntu-24.04" / "desktop-validation-receipt.json",
        extra / "desktop-validation-receipt.json",
    )

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--input-dir",
        str(inputs),
        "--validation-dir",
        str(validations),
        "--output-dir",
        str(tmp_path / "release"),
    )

    assert completed.returncode != 0
    assert "expected 6 desktop validation receipt files" in completed.stderr


def test_aggregate_rejects_observed_operating_system_tampering(tmp_path: Path) -> None:
    inputs, validations, _installers = _create_release_inputs(tmp_path)
    receipt_path = validations / "macos-26" / "desktop-validation-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["actualOs"] = "macOS 15"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--input-dir",
        str(inputs),
        "--validation-dir",
        str(validations),
        "--output-dir",
        str(tmp_path / "release"),
    )

    assert completed.returncode != 0
    assert "supported matrix" in completed.stderr


def test_aggregate_rejects_a_validation_installer_changed_after_receipt(
    tmp_path: Path,
) -> None:
    inputs, validations, _installers = _create_release_inputs(tmp_path)
    receipt_path = validations / "ubuntu-24.04" / "desktop-validation-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    installer = receipt_path.parent / receipt["installer"]["name"]
    installer.write_bytes(b"tampered after platform validation")

    completed = _run(
        "aggregate",
        "--git-head",
        GIT_HEAD,
        "--version",
        VERSION,
        "--input-dir",
        str(inputs),
        "--validation-dir",
        str(validations),
        "--output-dir",
        str(tmp_path / "release"),
    )

    assert completed.returncode != 0
    assert "does not match its exact evidence digest" in completed.stderr


def test_validation_receipt_records_exact_installer_digest(tmp_path: Path) -> None:
    installer = tmp_path / "AncestryLLM-0.5.0-linux-x64.deb"
    installer.write_bytes(b"the exact installed bytes")

    receipt_path = _create_validation_receipt(tmp_path / "validations", "ubuntu-24.04", installer)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert receipt["installer"] == {
        "name": installer.name,
        "bytes": installer.stat().st_size,
        "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
    }
    assert receipt["expectedOs"] == "Ubuntu 24.04"
    assert receipt["actualOs"] == "Ubuntu 24.04"
    assert receipt["gates"]["operatingSystemPassed"] is True
    assert receipt["workflow"] == {
        "runId": WORKFLOW_RUN_ID,
        "runAttempt": WORKFLOW_RUN_ATTEMPT,
        "sourceArtifactId": SOURCE_ARTIFACT_ID,
        "sourceArtifactDigest": SOURCE_ARTIFACT_DIGEST,
    }
