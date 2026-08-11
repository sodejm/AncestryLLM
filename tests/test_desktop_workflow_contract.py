"""Contract tests for the exact-head desktop verification workflow."""

import re
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
    assert '[[ "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]' in workflow
    assert 'if test "$EVENT_NAME" = "push"; then' in workflow
    assert "git fetch --no-tags origin main" in workflow
    assert 'test "$EXPECTED_HEAD" = "$(git rev-parse refs/remotes/origin/main)"' in workflow
    assert 'elif test "$EVENT_NAME" = "workflow_dispatch"; then' in workflow
    assert 'test "$EXPECTED_HEAD" = "$GITHUB_SHA"' in workflow
    assert 'echo "unsupported desktop verification event: $EVENT_NAME" >&2' in workflow
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
        ("windows-11-arm", "win32-arm64", "Windows 11", "arm64"),
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
    assert "runs_on: '[\"windows-11-arm\"]'" in workflow
    assert "name: Windows 11 ARM64" in workflow
    assert "host_arch: arm64" in workflow
    assert workflow.count("runtime_arch:") == 6
    assert "runtime_arch: arm64" in workflow
    assert workflow.count("architecture: ${{ matrix.runtime_arch }}") == 2
    assert "EXPECTED_HOST_ARCH: ${{ matrix.host_arch || matrix.arch }}" in workflow
    assert "HOST_ARCH: ${{ matrix.host_arch || matrix.arch }}" in workflow
    assert "[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture" in workflow
    assert "name: Verify native ARM64 runtimes on Windows" in workflow
    assert "process.arch" in workflow
    assert "sysconfig.get_platform()" in workflow
    assert "platform.machine()" not in workflow
    assert "expected arm64 Node.js runtime" in workflow
    assert "expected win-arm64 Python runtime" in workflow
    assert "Microsoft.VisualStudio.Component.VC.Tools.ARM64" not in workflow
    assert "OPENSSL_DIR" not in workflow
    assert "dumpbin /headers" not in workflow
    assert (
        "uv sync --locked --no-default-groups --extra desktop-build --group test "
        "--no-install-project --no-build"
    ) in workflow
    assert "uv pip install --python .venv --no-deps --editable ." in workflow
    assert workflow.count("uv run --no-sync") == 5
    assert "self-hosted" not in workflow
    assert "ancestryllm-windows-11" not in workflow
    assert "runs-on: ${{ fromJSON(matrix.runs_on) }}" in workflow
    assert "windows-2025" not in workflow
    assert "Windows Server 2025" not in workflow
    assert "package_boundary: unpacked-native" in workflow
    assert "release_supported:" not in workflow
    assert "--release-supported" not in workflow
    assert '--host-arch "$HOST_ARCH"' in workflow


def test_workflow_uploads_partial_windows_diagnostics_after_a_failure() -> None:
    workflow = _workflow()

    assert "name: Upload partial native-package diagnostics" in workflow
    assert "if: ${{ failure() }}" in workflow
    assert "desktop-native-package-failure-${{ matrix.runner }}" in workflow
    assert "desktop/test-results" in workflow
    assert "if-no-files-found: warn" in workflow


def test_workflow_uses_pinned_pnpm_action_and_machine_readable_evidence() -> None:
    workflow = _workflow()

    assert workflow.count("pnpm/action-setup@d15e628ca66d93ee5f352c71671a7bc6a97af5c9") == 2
    assert workflow.count('version: "11.9.0"') == 2
    assert "npm install --global pnpm" not in workflow
    assert "pnpm --dir desktop run test:e2e:packaged" not in workflow
    assert workflow.count("node desktop/scripts/run-packaged-tests.mjs") == 5
    assert "verification-receipt.mjs" in workflow
    assert "--allow-output desktop/verification/security" not in workflow
    assert '--allow-output "$ROW_ROOT"' not in workflow
    assert "--allow-output desktop/out" not in workflow
    assert '--allow-output "$RECEIPTS_DIR/api-contract.json"' in workflow
    assert "inspect-package-fuses.mjs --output" in workflow
    assert "pnpm --dir desktop run check:secrets" in workflow
    assert "pnpm --dir desktop run sbom" in workflow
    assert "pnpm --dir desktop sbom" not in workflow
    assert "verification-evidence.mjs target" in workflow
    assert "verification-evidence.mjs security" in workflow
    assert "--receipts" in workflow
    assert "--artifact metrics=" in workflow
    assert "--artifact fuseInspection=" in workflow
    assert "--artifact fileGrantEvidence=" in workflow
    assert "--artifact sbom=" in workflow
    assert "--audit-passed" not in workflow
    assert "desktop/release/" not in workflow
    assert "--config electron-builder.verification.yml" in workflow
    assert "--config electron-builder.file-grant-verification.yml" in workflow

    builder = VERIFICATION_BUILDER_CONFIG.read_text(encoding="utf-8")
    assert "extends: ./electron-builder.yml" in builder
    assert re.search(
        r'mac:\n  identity: "-"\n  signIgnore:\n'
        r'    - "/Contents/Resources/sidecar/"\n?\Z',
        builder,
    )
    assert builder.count("signIgnore:") == 1


def test_workflow_receipts_bind_black_box_packaged_sidecar_faults() -> None:
    workflow = _workflow()

    assert "scripts/build_verification_sidecar.py" in workflow
    assert "sidecar-process-tree-guard.json" in workflow
    assert "--gate sidecarProcessTreeGuardPassed" in workflow
    assert "tests/api/test_sidecar_bootstrap.py" in workflow
    assert "ANCESTRYLLM_SUBSTITUTED_SIDECAR" in workflow
    assert "packaged-sidecar-withhold-retry.json" in workflow
    assert "--gate packagedSidecarWithholdRetryPassed" in workflow
    assert '--artifact faultEvidence="$ANCESTRYLLM_WITHHOLD_EVIDENCE"' in workflow
    assert "packaged-sidecar-restart-exhaustion-quit.json" in workflow
    assert "--gate packagedSidecarRestartExhaustionQuitPassed" in workflow
    assert '--artifact faultEvidence="$ANCESTRYLLM_RESTART_EVIDENCE"' in workflow
    assert "packaged-sidecar-integrity-substitution.json" in workflow
    assert "--gate packagedSidecarIntegritySubstitutionPassed" in workflow
    assert '--artifact faultEvidence="$ANCESTRYLLM_INTEGRITY_EVIDENCE"' in workflow
    assert '--artifact failureDiagnostics="$ANCESTRYLLM_INTEGRITY_DIAGNOSTICS"' in workflow
    assert '--artifact substitutedSidecar="$ANCESTRYLLM_SUBSTITUTED_SIDECAR"' in workflow
    assert '--allow-output "$ROW_ROOT/sidecar-integrity-substitution-diagnostics.json"' in workflow

    production_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "desktop" / "src").rglob("*"))
        if path.is_file()
    )
    assert "ANCESTRYLLM_WITHHOLD_EVIDENCE" not in production_sources
    assert "ANCESTRYLLM_RESTART_EVIDENCE" not in production_sources
    assert "ANCESTRYLLM_INTEGRITY_EVIDENCE" not in production_sources
    assert "ANCESTRYLLM_SUBSTITUTED_SIDECAR" not in production_sources


def test_workflow_binds_packaged_file_grant_mediation_without_production_dialog_hooks() -> None:
    workflow = _workflow()

    assert "ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION=1" in workflow
    assert "ANCESTRYLLM_FILE_GRANT_OPEN_PATH=" in workflow
    assert "ANCESTRYLLM_FILE_GRANT_SAVE_PATH=" in workflow
    assert "ANCESTRYLLM_FILE_GRANT_EVIDENCE=" in workflow
    assert "packaged-file-grants.json" in workflow
    assert "--gate packagedFileGrantSmokePassed" in workflow
    assert '--artifact fileGrantEvidence="$ANCESTRYLLM_FILE_GRANT_EVIDENCE"' in workflow
    assert '--file-grant-evidence "$ROW_ROOT/file-grant-mediation.json"' in workflow
    assert "desktop/release-file-grant-verification" in workflow

    production_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "desktop" / "src").rglob("*"))
        if path.is_file()
    )
    assert "ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION" not in production_sources
    assert "ANCESTRYLLM_FILE_GRANT_OPEN_PATH" not in production_sources
    assert "ANCESTRYLLM_FILE_GRANT_SAVE_PATH" not in production_sources
    assert "ANCESTRYLLM_FILE_GRANT_EVIDENCE" not in production_sources


def test_packaged_scenarios_forward_playwright_filters_without_a_pnpm_separator() -> None:
    workflow = _workflow()

    expected_scenarios = (
        "exercises first run, persistence, corrupt preferences, security, and resource evidence",
        "mediates opaque packaged open and save file grants",
        "withholds and restores the packaged sidecar through Diagnostics retry",
        "restarts a killed packaged sidecar, exhausts the budget, and cleans up on quit",
        "rejects a target-native substituted packaged sidecar before spawn",
    )
    assert workflow.count("run-packaged-tests.mjs") == len(expected_scenarios)
    for scenario in expected_scenarios:
        assert workflow.count(f'--grep "{scenario}"') == 1

    assert re.search(r"run-packaged-tests\.mjs --\s", workflow) is None


def test_packaged_runtime_uses_absolute_evidence_paths_and_preserves_linux_sandbox() -> None:
    workflow = _workflow()

    assert (
        'substituted_sidecar="$GITHUB_WORKSPACE/desktop/build/verification-sidecar/'
        '$SIDECAR_TARGET/ancestryllm-wrong-build-sidecar"' in workflow
    )
    expected_evidence_paths = (
        'ANCESTRYLLM_PACKAGED_METRICS="$GITHUB_WORKSPACE/$ROW_ROOT/packaged-metrics.json"',
        'ANCESTRYLLM_FILE_GRANT_EVIDENCE="$GITHUB_WORKSPACE/$ROW_ROOT/file-grant-mediation.json"',
        'ANCESTRYLLM_WITHHOLD_EVIDENCE="$GITHUB_WORKSPACE/$ROW_ROOT/sidecar-withhold-retry.json"',
        'ANCESTRYLLM_RESTART_EVIDENCE="$GITHUB_WORKSPACE/$ROW_ROOT/sidecar-restart-exhaustion-quit.json"',
        'ANCESTRYLLM_INTEGRITY_EVIDENCE="$GITHUB_WORKSPACE/$ROW_ROOT/sidecar-integrity-substitution.json"',
        'ANCESTRYLLM_INTEGRITY_DIAGNOSTICS="$GITHUB_WORKSPACE/$ROW_ROOT/sidecar-integrity-substitution-diagnostics.json"',
    )
    for evidence_path in expected_evidence_paths:
        assert evidence_path in workflow

    expected_recorded_evidence = (
        '--withhold-evidence "$ROW_ROOT/sidecar-withhold-retry.json"',
        '--restart-evidence "$ROW_ROOT/sidecar-restart-exhaustion-quit.json"',
        '--integrity-evidence "$ROW_ROOT/sidecar-integrity-substitution.json"',
        '--file-grant-evidence "$ROW_ROOT/file-grant-mediation.json"',
    )
    for evidence_path in expected_recorded_evidence:
        assert evidence_path in workflow
    assert '--withhold-evidence "$ANCESTRYLLM_WITHHOLD_EVIDENCE"' not in workflow
    assert '--restart-evidence "$ANCESTRYLLM_RESTART_EVIDENCE"' not in workflow
    assert '--integrity-evidence "$ANCESTRYLLM_INTEGRITY_EVIDENCE"' not in workflow

    sandbox_step = "Prepare Chromium sandbox for unpacked Linux verification"
    assert f"name: {sandbox_step}" in workflow
    assert "if: runner.os == 'Linux'" in workflow
    assert 'sandbox_path="$(dirname "$packaged_app")/chrome-sandbox"' in workflow
    assert '"$GITHUB_WORKSPACE/desktop/release"/*) ;;' in workflow
    assert 'echo "::error::Unexpected Chromium sandbox path"' in workflow
    assert 'test ! -L "$sandbox_path"' in workflow
    assert 'test -f "$sandbox_path"' in workflow
    assert 'sudo chown root:root -- "$sandbox_path"' in workflow
    assert 'sudo chmod 4755 -- "$sandbox_path"' in workflow
    assert 'test "$(stat -c \'%U:%G:%a\' -- "$sandbox_path")" = "root:root:4755"' in workflow
    assert "--no-sandbox" not in workflow
    assert (
        workflow.index("Assemble unpublished unpacked native application")
        < workflow.index(sandbox_step)
        < workflow.index("Exercise the packaged application")
    )


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
    assert "binary-signing mode" in document
    assert "v1.0.0" in document
    assert "ad hoc" in document
    assert "#231" in document
    assert "#131" in document
    assert "full 40-character head SHA" in document
    assert "immutable event SHA for the selected same-repository ref" in document
    assert "does not receive release credentials" in document
    assert "Windows Server 2025" not in document
