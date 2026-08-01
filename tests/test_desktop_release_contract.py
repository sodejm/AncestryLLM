"""Contracts for supported v0.5 signed desktop release artifacts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
BUILDER_CONFIG = ROOT / "desktop" / "electron-builder.release.yml"
ENTITLEMENTS = ROOT / "desktop" / "resources" / "entitlements.mac.plist"
ASSEMBLER = ROOT / "scripts" / "assemble_desktop_release.py"


def test_release_workflow_builds_and_verifies_the_supported_installer_matrix() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    expected_rows = (
        ("macos-15", "darwin-arm64", "macOS 15", "arm64", "dmg"),
        ("macos-15-intel", "darwin-x64", "macOS 15", "x64", "dmg"),
        ("ancestryllm-windows-11", "win32-x64", "Windows 11", "x64", "nsis"),
        ("ubuntu-24.04", "linux-x64", "Ubuntu 24.04", "x64", "deb"),
    )
    for runner, sidecar_target, expected_os, arch, installer_target in expected_rows:
        assert runner in workflow
        assert f"sidecar_target: {sidecar_target}" in workflow
        assert f'expected_os: "{expected_os}"' in workflow
        assert f"arch: {arch}" in workflow
        assert f"installer_target: {installer_target}" in workflow

    assert "desktop-installers:" in workflow
    assert "electron-builder.release.yml" in workflow
    assert "scripts/smoke_sidecar.py" in workflow
    assert "ANCESTRYLLM_PACKAGED_RUNTIME_PATH" in workflow
    assert "Get-AuthenticodeSignature" in workflow
    assert "codesign --verify --deep --strict" in workflow
    assert "spctl --assess" in workflow
    assert "stapler validate" in workflow
    assert "gpg --detach-sign" in workflow
    assert "--status-fd 1 --verify" in workflow
    assert "LINUX_GPG_SIGNING_FINGERPRINT: ${{ secrets.LINUX_GPG_SIGNING_FINGERPRINT }}" in workflow
    assert "ancestryllm-signing-gnupg" in workflow
    assert "ancestryllm-verification-gnupg" in workflow
    assert '--local-user "$expected_fingerprint!"' in workflow
    assert "--status-fd 1 --verify" in workflow
    assert 'grep -Fq "[GNUPG:] VALIDSIG $expected_fingerprint "' in workflow


def test_release_packaging_is_signed_manual_full_installer_only() -> None:
    builder = BUILDER_CONFIG.read_text(encoding="utf-8")
    entitlements = ENTITLEMENTS.read_text(encoding="utf-8")

    assert "appId: org.ancestryllm.desktop" in builder
    assert "forceCodeSigning: true" in builder
    assert "identity: null" not in builder
    assert "target: dmg" in builder
    assert "target: nsis" in builder
    assert "target: deb" in builder
    assert "hardenedRuntime: true" in builder
    assert "entitlements.mac.plist" in builder
    assert "notarize: true" in builder
    assert "oneClick: false" in builder
    assert "allowToChangeInstallationDirectory: true" in builder
    assert "differentialPackage: false" in builder
    assert "publish: null" in builder
    assert "generateUpdatesFilesForAllChannels: false" in builder
    assert "com.apple.security.cs.allow-jit" in entitlements
    assert "com.apple.security.cs.allow-unsigned-executable-memory" in entitlements
    assert "com.apple.security.cs.disable-library-validation" not in entitlements


def test_release_workflow_binds_installers_evidence_sboms_and_provenance() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "assemble_desktop_release.py target" in workflow
    assert "assemble_desktop_release.py aggregate" in workflow
    assert "desktop-artifact-manifest.json" in workflow
    assert "desktop-sbom.json" in workflow
    assert "desktop-exact-head-evidence.json" in workflow
    assert 'pnpm --dir desktop pkg set version="$VERSION"' in workflow
    assert workflow.index('pnpm --dir desktop pkg set version="$VERSION"') < workflow.index(
        "pnpm --dir desktop sbom"
    )
    assert "download-artifact" in workflow
    assert "desktop-evidence-aggregate" in workflow
    assert "subject-path: dist/*" in workflow
    assert "generate_release_checksums.py --directory dist" in workflow
    assert 'test "$(git rev-parse HEAD)" = "$COMMIT_SHA"' in workflow
    aggregate = workflow.index("assemble_desktop_release.py aggregate")
    final_manifest = workflow.index("scripts/create_release_evidence.py", aggregate)
    checksums = workflow.index("generate_release_checksums.py --directory dist", aggregate)
    assert aggregate < final_manifest < checksums
    for publisher in (
        "publish-build-provenance",
        "draft-github-release",
        "publish-testpypi",
        "publish-pypi",
        "publish-github-release",
    ):
        job = workflow[workflow.index(f"  {publisher}:") :]
        assert "desktop-evidence-aggregate" in job.split("runs-on:", maxsplit=1)[0]


def test_release_docs_define_the_exact_matrix_and_manual_upgrade_contract() -> None:
    releasing = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    desktop = (ROOT / "docs" / "DESKTOP_VERIFICATION.md").read_text(encoding="utf-8")
    normalized = " ".join((releasing + "\n" + desktop).split())

    for expected in (
        "macOS 15 arm64",
        "macOS 15 x64",
        "Windows 11 x64",
        "Ubuntu 24.04 x64",
        "SHA256SUMS",
        "Authenticode",
        "Gatekeeper",
        "notarization",
        "detached GPG signature",
        "full installer",
    ):
        assert expected in normalized
    assert "no updater feed" in normalized
    assert "no background update" in normalized
    assert "no staged rollout" in normalized
    assert "no automatic rollback" in normalized


def test_desktop_release_assembler_rejects_incomplete_target_evidence(tmp_path: Path) -> None:
    installer = tmp_path / "AncestryLLM-0.5.0-ubuntu-x64.deb"
    installer.write_bytes(b"installer")
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(ASSEMBLER),
            "target",
            "--git-head",
            "a" * 40,
            "--version",
            "0.5.0",
            "--target",
            "linux-x64",
            "--expected-os",
            "Ubuntu 24.04",
            "--arch",
            "x64",
            "--installer",
            str(installer),
            "--sbom",
            str(sbom),
            "--output",
            str(tmp_path / "evidence.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert completed.returncode != 0
    assert "missing required verification gate" in completed.stderr
