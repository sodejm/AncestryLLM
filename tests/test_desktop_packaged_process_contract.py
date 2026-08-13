"""Contracts for packaged Electron process-tree evidence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_SPEC = ROOT / "desktop" / "e2e" / "packaged-shell.spec.ts"
PACKAGED_NATIVE_VERIFICATION = (
    ROOT / "desktop" / "e2e" / "native-verification.packaged-verification.ts"
)
MAIN_INDEX = ROOT / "desktop" / "src" / "main" / "index.ts"
PRODUCTION_NATIVE_VERIFICATION = ROOT / "desktop" / "src" / "main" / "native-verification.ts"
RUNTIME_BRIDGE = ROOT / "desktop" / "src" / "main" / "runtime-bridge.ts"
SIDECAR_SUPERVISOR = ROOT / "desktop" / "src" / "main" / "sidecar-supervisor.ts"
DESKTOP_WORKFLOW = ROOT / ".github" / "workflows" / "desktop-sidecar.yml"


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


def test_packaged_clean_quit_requests_native_quit_and_releases_automation() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    main_source = MAIN_INDEX.read_text(encoding="utf-8")
    runtime_bridge_source = RUNTIME_BRIDGE.read_text(encoding="utf-8")
    retry_start = source.index("async function requestMacPackagedQuit")
    retry_end = source.index("\nasync function forceClosePackaged", retry_start)
    retry_source = source[retry_start:retry_end]
    close_start = source.index("async function closePackaged")
    close_end = source.index("\nasync function launchPackaged", close_start)
    close_source = source[close_start:close_end]

    assert "const packagedQuitRetryDelayMs = 20_000" in source
    assert "const packagedQuitTimeoutMs = 30_000" in source
    assert "waitForProcessExit(result.process, 15_000)" not in close_source
    platform_index = close_source.index("process.platform === 'darwin'")
    retry_index = close_source.index(
        "processExit = requestMacPackagedQuit(result.process)", platform_index
    )
    window_close_index = close_source.index(
        "result.page.close({ runBeforeUnload: false })", platform_index
    )
    browser_close_index = close_source.index("result.browser.close()", window_close_index)
    status_index = close_source.index("const status = await processExit", browser_close_index)

    assert "new WebSocket(" not in close_source
    assert "browserEndpoint" not in close_source
    assert "newBrowserCDPSession" not in close_source
    assert "session.send('Browser.close')" not in close_source
    assert "result.page.keyboard.press('Meta+Q')" not in close_source
    assert "result.process.kill('SIGKILL')" not in close_source
    assert "child.kill('SIGKILL')" not in retry_source
    assert re.search(
        r"const initialExit = waitForProcessExit\(child, packagedQuitRetryDelayMs\)\s*"
        r"requestQuit\('initial', initialExit\)\s*"
        r"try \{\s*return await initialExit\s*\} catch \{.*?"
        r"const retryExit = waitForProcessExit\(child, packagedQuitTimeoutMs\)\s*"
        r"requestQuit\('retry', retryExit\)\s*return retryExit",
        retry_source,
        re.DOTALL,
    )
    assert re.search(
        r"if \(!child\.kill\('SIGTERM'\)\) \{.*?"
        r"void processExit\.catch\(\(\) => undefined\)\s*"
        r"throw new Error\(`Packaged app rejected the macOS \$\{attempt\} quit request\.`\)\s*"
        r"\}",
        retry_source,
        re.DOTALL,
    )
    assert re.search(
        r"await withinDeadline\(\s*'closing packaged application window',\s*"
        r"packagedWindowCloseTimeoutMs,\s*"
        r"\(\) => result\.page\.close\(\{ runBeforeUnload: false \}\),\s*\)",
        close_source,
    )
    assert "'closing packaged browser automation'" in close_source
    assert "packagedCleanupTimeoutMs" in close_source
    assert "const packagedWindowCloseTimeoutMs = 20_000" in source
    assert platform_index < retry_index < browser_close_index
    assert platform_index < window_close_index < browser_close_index
    assert browser_close_index < status_index
    assert "expect(status).toEqual({ code: 0, signal: null })" in close_source
    assert "const requestVerifiedAppQuit = (): void => { app.quit() }" in main_source
    assert "process.off('SIGTERM', requestVerifiedAppQuit)" in main_source
    assert "process.on('SIGTERM', requestVerifiedAppQuit)" in main_source
    primary_instance_index = main_source.index("} else if (primaryInstance) {")
    sigterm_handler_index = main_source.index("armVerifiedSigtermHandler()", primary_instance_index)
    runtime_start_index = main_source.index("const runtime = await startRuntimeBridge(")
    runtime_owner_index = main_source.index("sidecarSupervisor = supervisor", runtime_start_index)
    runtime_rearm_index = main_source.index("armVerifiedSigtermHandler()", runtime_owner_index)
    packaged_window_index = main_source.index("createWindow()", sigterm_handler_index)
    assert (
        sigterm_handler_index
        < runtime_start_index
        < runtime_owner_index
        < runtime_rearm_index
        < packaged_window_index
    )
    own_supervisor_index = runtime_bridge_source.index(
        "onSupervisorOwned?.(supervisor, prepareJobShutdown)"
    )
    start_supervisor_index = runtime_bridge_source.index("await supervisor.start()")
    assert own_supervisor_index < start_supervisor_index
    assert "window.on('close', (event) => {" in main_source
    assert "requestVerifiedShutdownBeforeWindowClose(" in main_source
    assert (
        "!shutdownAuthorized && (sidecarSupervisor !== undefined || shutdownPromise !== undefined)"
        in main_source
    )
    assert "shutdownPromise !== undefined," in main_source
    assert "app.on('window-all-closed', () => { app.quit() })" in main_source
    assert "supervisor.isExplicitSafeEmpty()" in main_source
    assert "const shutdownProgress: AppShutdownProgress = { jobsPrepared: false }" in main_source
    assert re.search(
        r"\(\) => supervisor\.isExplicitSafeEmpty\(\),\s*shutdownProgress,",
        main_source,
    )
    verified_exit_start = main_source.index(
        "() => {\n          disposeIpcBoundary()",
        main_source.index("shutdownPromise = completeAppShutdown("),
    )
    verified_exit_end = main_source.index("\n        },", verified_exit_start)
    verified_exit_source = main_source[verified_exit_start:verified_exit_end]
    dispose_index = verified_exit_source.index("disposeIpcBoundary()")
    supervisor_release_index = verified_exit_source.index("sidecarSupervisor = undefined")
    authorization_index = verified_exit_source.index("shutdownAuthorized = true")
    exit_index = verified_exit_source.index("app.exit(0)")

    assert dispose_index < supervisor_release_index < authorization_index < exit_index
    assert "app.quit()" not in verified_exit_source


def test_linux_packaged_environment_authenticates_native_keyring_boundary() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    main_source = MAIN_INDEX.read_text(encoding="utf-8")
    production_verifier_source = PRODUCTION_NATIVE_VERIFICATION.read_text(encoding="utf-8")
    packaged_verifier_source = PACKAGED_NATIVE_VERIFICATION.read_text(encoding="utf-8")
    supervisor_source = SIDECAR_SUPERVISOR.read_text(encoding="utf-8")
    workflow_source = DESKTOP_WORKFLOW.read_text(encoding="utf-8")

    assert "ANCESTRYLLM_NATIVE_KEYRING_SESSION" not in source
    assert "ANCESTRYLLM_NATIVE_KEYRING_ROOT" in source
    assert "ANCESTRYLLM_NATIVE_KEYRING_ROOT" not in supervisor_source
    assert "inheritedEnvironment(['HOME', 'XDG_CACHE_HOME'" not in source
    assert "LINUX_KEYRING_VERIFICATION_SWITCH" in source
    assert "LINUX_KEYRING_VERIFICATION_SWITCH" not in main_source
    assert "ancestryllm-linux-keyring-verification-root" not in main_source
    assert "LINUX_KEYRING_VERIFICATION_SWITCH" not in production_verifier_source
    assert "ancestryllm-linux-keyring-verification-root" not in production_verifier_source
    assert "return undefined" in production_verifier_source
    assert "LINUX_KEYRING_VERIFICATION_SWITCH" in packaged_verifier_source
    assert "requestedLinuxKeyringVerificationRoot(app.commandLine)" in main_source
    production_build = workflow_source.index("pnpm --dir desktop run build\n")
    verification_build = workflow_source.index(
        "pnpm --dir desktop run build:packaged-native-verification"
    )
    production_assembly = workflow_source.index(
        "electron-builder --config electron-builder.verification.yml"
    )
    verification_assembly = workflow_source.index(
        "electron-builder --config electron-builder.native-verification.yml"
    )
    assert production_build < production_assembly < verification_build < verification_assembly
    assert "desktop/release-native-verification" in workflow_source
    assert "'DBUS_SESSION_BUS_ADDRESS'" not in supervisor_source
    assert "'XDG_RUNTIME_DIR'" not in supervisor_source
    assert "process.getuid?.()" in supervisor_source
    assert "posix.join('/run/user', String(userId))" in supervisor_source
    assert "`unix:path=${posix.join(runtimeDirectory, 'bus')}`" in supervisor_source
    assert "ANCESTRYLLM_NATIVE_KEYRING_SESSION" not in supervisor_source
    assert "linuxKeyringVerificationRoot" in supervisor_source
    assert "source.HOME" not in supervisor_source
    assert "source.XDG_CACHE_HOME" not in supervisor_source
    assert "source.XDG_CONFIG_HOME" not in supervisor_source
    assert "source.XDG_DATA_HOME" not in supervisor_source
    assert (
        "environment.PYTHON_KEYRING_BACKEND = 'keyring.backends.SecretService.Keyring'"
    ) in supervisor_source


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
