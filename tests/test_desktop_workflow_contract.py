"""Contract tests for the exact-head desktop verification workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "desktop-sidecar.yml"
VERIFICATION_DOC = ROOT / "docs" / "DESKTOP_VERIFICATION.md"
VERIFICATION_BUILDER_CONFIG = ROOT / "desktop" / "electron-builder.verification.yml"
RUNTIME_BRIDGE = ROOT / "desktop" / "src" / "main" / "runtime-bridge.ts"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_desktop_workflow_has_an_always_reported_exact_head_gate() -> None:
    workflow = _workflow()

    assert "commit_sha:" in workflow
    assert "inputs.commit_sha || github.sha" in workflow
    assert "pull_request:" not in workflow
    assert "github.event.pull_request" not in workflow
    assert "git fetch --no-tags origin main" in workflow
    assert 'test "$EXPECTED_HEAD" = "$(git rev-parse refs/remotes/origin/main)"' in workflow
    assert "paths:" not in workflow
    assert "changes:" in workflow
    assert "desktop-security:" in workflow
    assert "native-package:" in workflow
    assert "desktop-gate:" in workflow
    assert "name: Desktop gate" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "needs: [changes, desktop-security, native-package]" in workflow
    assert "git rev-parse HEAD" in workflow
    assert "verification-evidence.mjs aggregate" in workflow


def test_native_matrix_is_the_supported_six_row_boundary() -> None:
    workflow = _workflow()

    expected_rows = (
        ("macos-15", "darwin-arm64", "macOS 15", "arm64"),
        ("macos-15-intel", "darwin-x64", "macOS 15", "x64"),
        ("macos-26", "darwin-arm64", "macOS 26", "arm64"),
        ("macos-26-intel", "darwin-x64", "macOS 26", "x64"),
        ("ancestryllm-windows-11", "win32-x64", "Windows 11", "x64"),
        ("ubuntu-24.04", "linux-x64", "Ubuntu 24.04", "x64"),
    )
    for runner, sidecar_target, expected_os, arch in expected_rows:
        assert f"runner: {runner}" in workflow
        assert f"sidecar_target: {sidecar_target}" in workflow
        assert f'expected_os: "{expected_os}"' in workflow
        assert f"arch: {arch}" in workflow

    assert "matrix.actual_os" not in workflow
    assert "actual_os:" not in workflow
    assert "id: macos-host" in workflow
    assert 'actual_os="macOS $(sw_vers -productVersion | cut -d. -f1)"' in workflow
    assert "id: windows-host" in workflow
    assert "Get-CimInstance Win32_OperatingSystem" in workflow
    assert "id: linux-host" in workflow
    assert "source /etc/os-release" in workflow
    assert (
        "ACTUAL_OS: ${{ steps.macos-host.outputs.actual_os || "
        "steps.windows-host.outputs.actual_os || steps.linux-host.outputs.actual_os }}" in workflow
    )
    assert 'runs_on: \'["self-hosted", "Windows", "X64", "ancestryllm-windows-11"]\'' in workflow
    assert "ephemeral one-job runner" in workflow
    assert "runs-on: ${{ fromJSON(matrix.runs_on) }}" in workflow
    assert "windows-2025" not in workflow
    assert "Windows Server 2025" not in workflow
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
    assert workflow.count("verification-receipt.mjs") == workflow.count("--allow-output")
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


def test_workflow_receipts_bind_black_box_packaged_sidecar_faults() -> None:
    workflow = _workflow()

    assert "scripts/build_verification_sidecar.py" in workflow
    assert "ANCESTRYLLM_WRONG_BUILD_SIDECAR" in workflow
    assert "packaged-sidecar-withhold-retry.json" in workflow
    assert "--gate packagedSidecarWithholdRetryPassed" in workflow
    assert '--artifact faultEvidence="$ANCESTRYLLM_WITHHOLD_EVIDENCE"' in workflow
    assert "packaged-sidecar-restart-exhaustion-quit.json" in workflow
    assert "--gate packagedSidecarRestartExhaustionQuitPassed" in workflow
    assert '--artifact faultEvidence="$ANCESTRYLLM_RESTART_EVIDENCE"' in workflow
    assert "packaged-sidecar-version-mismatch.json" in workflow
    assert "--gate packagedSidecarVersionMismatchPassed" in workflow
    assert '--artifact faultEvidence="$ANCESTRYLLM_MISMATCH_EVIDENCE"' in workflow
    assert '--artifact wrongBuildSidecar="$ANCESTRYLLM_WRONG_BUILD_SIDECAR"' in workflow

    production_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "desktop" / "src").rglob("*"))
        if path.is_file()
    )
    assert "ANCESTRYLLM_WITHHOLD_EVIDENCE" not in production_sources
    assert "ANCESTRYLLM_RESTART_EVIDENCE" not in production_sources
    assert "ANCESTRYLLM_MISMATCH_EVIDENCE" not in production_sources
    assert "ANCESTRYLLM_WRONG_BUILD_SIDECAR" not in production_sources


def test_fatal_sidecar_startup_uses_the_renderer_diagnostics_recovery_surface() -> None:
    runtime_bridge = RUNTIME_BRIDGE.read_text(encoding="utf-8")

    assert "onFatal:" not in runtime_bridge
    assert "import { app } from 'electron'" in runtime_bridge
    assert "dialog.showMessageBox" not in runtime_bridge
    assert "dialog.showErrorBox" not in runtime_bridge


def test_verification_document_covers_external_release_blockers() -> None:
    assert VERIFICATION_DOC.exists()
    document = VERIFICATION_DOC.read_text(encoding="utf-8")

    assert "Windows 11" in document
    assert "platformValidated" in document
    assert "unpacked-native" in document
    assert "signed installer" in document
    assert "ad hoc" in document
    assert "#231" in document
    assert "#131" in document
    assert "Windows Server 2025" not in document
