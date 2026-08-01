"""Contract tests for the exact-head desktop verification workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "desktop-sidecar.yml"
VERIFICATION_DOC = ROOT / "docs" / "DESKTOP_VERIFICATION.md"
VERIFICATION_BUILDER_CONFIG = ROOT / "desktop" / "electron-builder.verification.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_desktop_workflow_has_an_always_reported_exact_head_gate() -> None:
    workflow = _workflow()

    assert "paths:" not in workflow
    assert "changes:" in workflow
    assert "desktop-security:" in workflow
    assert "native-package:" in workflow
    assert "desktop-gate:" in workflow
    assert "name: Desktop gate" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "needs: [changes, desktop-security, native-package]" in workflow
    assert "github.event.pull_request.head.sha || github.sha" in workflow
    assert "git rev-parse HEAD" in workflow
    assert "verification-evidence.mjs aggregate" in workflow


def test_native_matrix_is_the_supported_six_row_boundary() -> None:
    workflow = _workflow()

    expected_rows = (
        ("macos-15", "darwin-arm64", "macOS 15", "arm64"),
        ("macos-15-intel", "darwin-x64", "macOS 15", "x64"),
        ("macos-26", "darwin-arm64", "macOS 26", "arm64"),
        ("macos-26-intel", "darwin-x64", "macOS 26", "x64"),
        ("windows-2025", "win32-x64", "Windows 11", "x64"),
        ("ubuntu-24.04", "linux-x64", "Ubuntu 24.04", "x64"),
    )
    for runner, sidecar_target, expected_os, arch in expected_rows:
        assert f"runner: {runner}" in workflow
        assert f"sidecar_target: {sidecar_target}" in workflow
        assert f'expected_os: "{expected_os}"' in workflow
        assert f"arch: {arch}" in workflow

    assert 'actual_os: "Windows Server 2025"' in workflow
    assert "package_boundary: unpacked-native" in workflow
    assert "release_supported:" not in workflow
    assert "--release-supported" not in workflow


def test_workflow_uses_pinned_pnpm_action_and_machine_readable_evidence() -> None:
    workflow = _workflow()

    assert workflow.count("pnpm/action-setup@d15e628ca66d93ee5f352c71671a7bc6a97af5c9") == 2
    assert workflow.count('version: "11.9.0"') == 2
    assert "npm install --global pnpm" not in workflow
    assert "pnpm --dir desktop run test:e2e:packaged" in workflow
    assert "verification-receipt.mjs" in workflow
    assert "inspect-package-fuses.mjs --output" in workflow
    assert "pnpm --dir desktop run check:secrets" in workflow
    assert "pnpm --dir desktop sbom" in workflow
    assert "verification-evidence.mjs target" in workflow
    assert "verification-evidence.mjs security" in workflow
    assert "--receipts" in workflow
    assert "--artifact metrics=" in workflow
    assert "--artifact fuseInspection=" in workflow
    assert "--artifact sbom=" in workflow
    assert "--audit-passed" not in workflow
    assert "desktop/release/" not in workflow
    assert "--config electron-builder.verification.yml" in workflow

    builder = VERIFICATION_BUILDER_CONFIG.read_text(encoding="utf-8")
    assert "extends: ./electron-builder.yml" in builder
    assert 'identity: "-"' in builder


def test_verification_document_covers_external_release_blockers() -> None:
    assert VERIFICATION_DOC.exists()
    document = VERIFICATION_DOC.read_text(encoding="utf-8")

    assert "Windows Server 2025" in document
    assert "Windows 11" in document
    assert "releaseSupported" in document
    assert "unpacked-native" in document
    assert "signed installer" in document
    assert "ad hoc" in document
    assert "#231" in document
    assert "#131" in document
