"""Contracts for packaged Electron process-tree evidence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_SPEC = ROOT / "desktop" / "e2e" / "packaged-shell.spec.ts"
MAIN_INDEX = ROOT / "desktop" / "src" / "main" / "index.ts"


def test_posix_process_snapshot_requests_unbounded_command_lines() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    assert re.search(
        r"execFileAsync\(\s*'ps',\s*\[\s*'-ww',\s*'-axo',\s*"
        r"'pid=,ppid=,rss=,command='\s*\]",
        source,
    )


def test_packaged_renderer_evidence_joins_browser_scoped_cdp_pids() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    main_source = MAIN_INDEX.read_text(encoding="utf-8")

    assert "browser.newBrowserCDPSession()" in source
    assert "session.send('SystemInfo.getProcessInfo')" in source
    assert re.search(r"\.filter\(\(record\) => record\.type === 'renderer'\)", source)
    assert "rendererPids.has(record.pid)" in source
    assert "await session.detach()" in source
    assert re.search(
        r"packagedProcessTreeMetrics\(\s*browser:\s*Browser,\s*rootPid:\s*number",
        source,
    )
    assert "sandboxedRendererCorrelated" in source
    assert re.search(
        r"tree\.some\(\(record\)\s*=>\s*"
        r"isSandboxedRendererProcess\(record,\s*rendererPids\)\)",
        source,
    )
    assert re.search(
        r"correlatedRendererProcesses\s*=\s*tree\.filter\("
        r"\(record\)\s*=>\s*isSandboxedRendererProcess\(record,\s*rendererPids\)\)",
        source,
    )
    assert "correlatedRendererProcesses.length" in source
    assert "app.enableSandbox()" in main_source
    assert "commandLine.includes('--no-sandbox')" in source
    assert "commandLine.includes('--disable-setuid-sandbox')" not in source
    assert "commandLine.includes('--enable-sandbox')" not in source
    assert not re.search(r"--type=renderer", source)


def test_normal_launch_waits_for_window_specific_readiness_without_debugging() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    main_source = MAIN_INDEX.read_text(encoding="utf-8")

    assert "outputContainsWindowReadyRecord(output)" in source
    assert "expect(windowReady).toBe(true)" in source
    assert "tree.length > 1" not in source
    assert "console.info(WINDOW_READY_RECORD)" in main_source
    assert "window.once('ready-to-show'" in main_source
    assert "expect([...observedCommandLines].join('\\n')).not.toMatch(DEBUG_ARGUMENT)" in source
    assert "expect(output).not.toContain('DevTools listening on ')" in source


def test_temporary_package_cleanup_retries_transient_windows_file_locks() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    assert re.search(
        r"async function removeTemporaryPackage\(root: string\): Promise<void> \{\s*"
        r"await rm\(root, \{\s*recursive: true,\s*force: true,\s*"
        r"maxRetries: 10,\s*retryDelay: 100,\s*\}\)\s*\}",
        source,
    )
    assert source.count("await removeTemporaryPackage(root)") == 4


def test_packaged_cleanup_terminates_the_windows_process_tree_with_a_deadline() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    assert "async function forceCloseProcess(child: ChildProcessWithoutNullStreams)" in source
    assert "taskkill.exe" in source
    assert "['/PID', String(child.pid), '/T', '/F']" in source
    assert "timeout: 10_000" in source
    assert "await forceCloseProcess(result.process)" in source
    assert "await forceCloseProcess(child)" in source


def test_packaged_startup_diagnostics_are_bounded_and_record_failure_context() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    assert "const mismatchDiagnosticsPath = process.env.ANCESTRYLLM_MISMATCH_DIAGNOSTICS" in source
    assert "async function withinDeadline<T>" in source
    assert "Timed out while ${operation}" in source
    assert "return withinDeadline('reading packaged startup diagnostics'" in source
    assert "async function writeMismatchDiagnostics" in source
    assert "let cleanupFailure: unknown" in source
    assert "let primaryFailurePhase: string | null = null" in source
    assert "primaryFailurePhase = phase" in source
    assert "let cleanupFailurePhase: string | null = null" in source
    assert "cleanupFailurePhase = phase" in source
    assert "phase: primaryFailurePhase ?? cleanupFailurePhase ?? phase" in source
    assert "status: failure || cleanupFailure ? 'failed' : 'passed'" in source
    assert "await writeMismatchDiagnostics" in source


def test_linux_package_copies_restore_the_chromium_suid_sandbox() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    assert "async function prepareCopiedLinuxSandbox(packageRoot: string)" in source
    assert "if (process.platform !== 'linux') return" in source
    assert "const sandboxPath = join(packageRoot, 'chrome-sandbox')" in source
    assert "sandbox.isSymbolicLink()" in source
    assert "sandbox.isFile()" in source
    assert "['--non-interactive', 'chown', 'root:root', '--', sandboxPath]" in source
    assert "['--non-interactive', 'chmod', '4755', '--', sandboxPath]" in source
    assert "prepared.uid !== 0" in source
    assert "prepared.gid !== 0" in source
    assert "(prepared.mode & 0o7777) !== 0o4755" in source
    assert "await prepareCopiedLinuxSandbox(packageRoot)" in source
    assert source.index("await cp(sourcePackageRoot, packageRoot") < source.index(
        "await prepareCopiedLinuxSandbox(packageRoot)"
    )
    assert "args.push('--no-sandbox')" not in source
