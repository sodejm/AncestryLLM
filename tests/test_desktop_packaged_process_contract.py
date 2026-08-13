"""Contracts for packaged Electron process-tree evidence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_SPEC = ROOT / "desktop" / "e2e" / "packaged-shell.spec.ts"
MAIN_INDEX = ROOT / "desktop" / "src" / "main" / "index.ts"
SIDECAR_SUPERVISOR = ROOT / "desktop" / "src" / "main" / "sidecar-supervisor.ts"


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


def test_packaged_capability_bridge_burst_is_bounded_and_completes() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    ready_index = source.index(
        "await expect(page.getByText(CAPABILITY_SUMMARY_READY)).toBeVisible()"
    )
    burst_index = source.index("running bounded packaged capability bridge burst")
    assert ready_index < burst_index
    assert "warming packaged capability bridge" not in source
    assert "running bounded packaged capability bridge burst" in source
    assert "Array.from({ length: 32 }" in source
    assert "Promise.all(" in source
    assert "ancestry.getCapabilities()" in source
    assert "successful: responses.filter((result) => result.ok).length" in source
    assert "result.error?.code === 'BRIDGE_OVERLOADED'" in source
    assert "expect(capabilityBurst.successful).toBe(32)" in source
    assert "expect(capabilityBurst.overloaded).toBe(0)" in source
    assert "expect(capabilityBurst.successful).toBeGreaterThan(0)" not in source
    assert "capabilityBurst.successful + capabilityBurst.overloaded" not in source
    assert "expect(capabilityBurst.unexpectedErrorCodes).toEqual([])" in source


def test_packaged_clean_quit_uses_established_session_and_waits_for_exit() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    main_source = MAIN_INDEX.read_text(encoding="utf-8")
    close_start = source.index("async function closePackaged")
    close_end = source.index("\nasync function launchPackaged", close_start)
    close_source = source[close_start:close_end]

    assert "const packagedQuitTimeoutMs = 30_000" in source
    assert "waitForProcessExit(result.process, 15_000)" not in close_source
    session_index = close_source.index("result.browser.newBrowserCDPSession()")
    process_wait_index = close_source.index(
        "const processExit = waitForProcessExit(result.process, packagedQuitTimeoutMs)"
    )
    command_index = close_source.index("session.send('Browser.close')", session_index)
    detach_index = close_source.index("session.detach()", command_index)
    browser_close_index = close_source.index("result.browser.close()", command_index)
    status_index = close_source.index("const status = await processExit", browser_close_index)

    assert "new WebSocket(" not in close_source
    assert "browserEndpoint" not in close_source
    assert "result.page.keyboard.press('Meta+Q')" not in close_source
    assert "result.process.kill('SIGTERM')" not in close_source
    assert "process.platform === 'darwin'" not in close_source
    assert "void session.send('Browser.close').catch(() => undefined)" not in close_source
    assert re.search(
        r"await withinDeadline\(\s*'requesting packaged clean quit',\s*"
        r"packagedCleanupTimeoutMs,\s*\(\) => session\.send\('Browser\.close'\),\s*"
        r"\)\.catch\(\(\) => undefined\)",
        close_source,
    )
    assert "'detaching packaged browser session'" in close_source
    assert "'closing packaged browser automation'" in close_source
    assert "packagedCleanupTimeoutMs" in close_source
    assert session_index < process_wait_index < command_index
    assert command_index < detach_index < browser_close_index < status_index
    assert "expect(status).toEqual({ code: 0, signal: null })" in close_source
    runtime_owner_index = main_source.index("sidecarSupervisor = runtime.supervisor")
    sigterm_handler_index = main_source.index("process.on('SIGTERM', () => app.quit())")
    packaged_window_index = main_source.index("createWindow()", sigterm_handler_index)
    assert runtime_owner_index < sigterm_handler_index < packaged_window_index
    assert "app.on('window-all-closed', () => { app.quit() })" in main_source


def test_linux_packaged_environment_preserves_native_keyring_session_boundary() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    supervisor_source = SIDECAR_SUPERVISOR.read_text(encoding="utf-8")

    inherited_linux_session = (
        "inheritedEnvironment(['HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', "
        "'XDG_DATA_HOME', 'XDG_RUNTIME_DIR'])"
    )
    assert inherited_linux_session in source
    assert re.search(
        r"if \(\s*process\.platform === 'linux'\s*&&\s*"
        r"process\.env\.ANCESTRYLLM_NATIVE_KEYRING_SESSION === '1'\s*\) \{\s*"
        r"Object\.assign\(environment, inheritedEnvironment.*?\)\s*"
        r"environment\.ANCESTRYLLM_NATIVE_KEYRING_SESSION = '1'",
        source,
        re.DOTALL,
    )
    assert re.search(
        r"platform === 'linux'\s*\?\s*\[.*?"
        r"'DBUS_SESSION_BUS_ADDRESS'.*?'XDG_RUNTIME_DIR'.*?\]",
        supervisor_source,
        re.DOTALL,
    )
    verifier_directories = re.search(
        r"const verificationKeyringDirectoriesAllowed = platform === 'linux'\s*&&\s*"
        r"source\.ANCESTRYLLM_NATIVE_KEYRING_SESSION === '1'\s*\?\s*\[(.*?)\]\s*:\s*\[\]",
        supervisor_source,
        re.DOTALL,
    )
    assert verifier_directories is not None
    assert "'HOME'" in verifier_directories.group(1)
    assert "'XDG_CACHE_HOME'" in verifier_directories.group(1)
    assert "'XDG_CONFIG_HOME'" in verifier_directories.group(1)
    assert "'XDG_DATA_HOME'" in verifier_directories.group(1)
    assert "'DBUS_SESSION_BUS_ADDRESS'" not in verifier_directories.group(1)
    assert "'XDG_RUNTIME_DIR'" not in verifier_directories.group(1)
    assert "'ANCESTRYLLM_NATIVE_KEYRING_SESSION'" not in supervisor_source
    assert "'PYTHON_KEYRING_BACKEND'" not in supervisor_source


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


def test_local_runtime_cli_shares_the_desktop_single_instance_lock() -> None:
    main_source = MAIN_INDEX.read_text(encoding="utf-8")

    assert "localRuntimeCliRequested || installSingleInstanceGuard" not in main_source
    assert re.search(
        r"const primaryInstance = localRuntimeCliRequested\s*"
        r"\? acquireSingleInstanceLock\(singleInstanceDependencies\)\s*"
        r": installSingleInstanceGuard\(",
        main_source,
    )
    assert "if (localRuntimeCliRequested && !primaryInstance)" in main_source
    assert "writeConcurrentLocalRuntimeCliFailure" in main_source


def test_temporary_package_cleanup_retries_transient_windows_file_locks() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    assert re.search(
        r"async function removeTemporaryPackage\(root: string\): Promise<void> \{\s*"
        r"await rm\(root, \{\s*recursive: true,\s*force: true,\s*"
        r"maxRetries: 10,\s*retryDelay: 100,\s*\}\)\s*\}",
        source,
    )
    assert source.count("await removeTemporaryPackage(root)") == 5


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
    deadline_helper = PACKAGED_SPEC.parent / "packaged-deadline.ts"
    deadline_source = deadline_helper.read_text(encoding="utf-8")

    assert (
        "const integrityDiagnosticsPath = process.env.ANCESTRYLLM_INTEGRITY_DIAGNOSTICS" in source
    )
    assert "import { withinDeadline } from './packaged-deadline'" in source
    assert "export async function withinDeadline<T>" in deadline_source
    assert "Timed out while ${operation}" in deadline_source
    assert "const packagedLaunchTimeoutMs = 120_000" in source
    assert (
        "return await withinDeadline(`launching packaged ${phase}`, packagedLaunchTimeoutMs"
        in source
    )
    assert (
        "await page.waitForLoadState('domcontentloaded', { timeout: packagedAttachTimeoutMs })"
        in source
    )
    assert (
        "withinDeadline('closing failed packaged browser automation', packagedCleanupTimeoutMs"
        in source
    )
    assert "return withinDeadline('reading packaged startup diagnostics'" in source
    assert "Packaged startup diagnostics did not match expected state" in source
    assert "JSON.stringify(actual)" in source
    assert "async function writeIntegrityDiagnostics" in source
    assert "let cleanupFailure: unknown" in source
    assert "let primaryFailurePhase: string | null = null" in source
    assert "primaryFailurePhase = phase" in source
    assert "let cleanupFailurePhase: string | null = null" in source
    assert "cleanupFailurePhase = phase" in source
    assert "phase: primaryFailurePhase ?? cleanupFailurePhase ?? phase" in source
    assert "status: failure || cleanupFailure ? 'failed' : 'passed'" in source
    assert "await writeIntegrityDiagnostics" in source


def test_packaged_startup_diagnostic_poll_retries_transient_unavailability() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    helper_start = source.index("async function expectStartupDiagnostics")
    helper_end = source.index("\nfunction packageRootForExecutable", helper_start)
    helper_source = source[helper_start:helper_end]

    poll_start = helper_source.index("await expect.poll")
    match_index = helper_source.index(").toMatchObject(expected)", poll_start)
    poll_source = helper_source[poll_start:match_index]

    assert "startupDiagnostics(page).catch(() => null)" in poll_source
    assert "return actual ?? {}" in poll_source
    assert ").toMatchObject(expected)" in helper_source


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
