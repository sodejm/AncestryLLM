"""Contracts for packaged Electron process-tree evidence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_SPEC = ROOT / "desktop" / "e2e" / "packaged-shell.wdio.ts"
PROCESS_RECORDS = ROOT / "desktop" / "e2e" / "process-records.ts"
PACKAGED_RUNNER = ROOT / "desktop" / "scripts" / "run-wdio.mjs"
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


def test_packaged_renderer_evidence_uses_the_native_electron_session() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    process_records_source = PROCESS_RECORDS.read_text(encoding="utf-8")
    main_source = MAIN_INDEX.read_text(encoding="utf-8")

    assert "browser.electron.execute" not in source
    assert (
        "const automatedPackagedExecutable = process.env.ANCESTRYLLM_PACKAGED_EXECUTABLE" in source
    )
    assert "matchesPackagedMainProcess(" in source
    assert "record.commandLine" in process_records_source
    assert "commandLine.includes(expectedExecutable)" in process_records_source
    assert "commandLine.includes(expectedProfile)" in process_records_source
    assert "!commandLine.includes('--type=')" in process_records_source
    assert "descendantProcessTree(await processSnapshot(), rootPid)" in source
    assert "record.commandLine.includes('--type=renderer')" in source
    assert "!record.commandLine.includes('--no-sandbox')" in source
    assert "app.enableSandbox()" in main_source
    for forbidden in (
        "newBrowserCDPSession",
        "SystemInfo.getProcessInfo",
        "connectOverCDP",
        "remote-debugging-port",
    ):
        assert forbidden not in source


def test_packaged_capability_bridge_burst_is_bounded_and_completes() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    ready_index = source.index("await browser.waitUntil(async () => browser.execute")
    burst_index = source.index("const capabilityBurst = await browser.execute")
    assert ready_index < burst_index
    assert "Array.from({ length: 32 }" in source
    assert "Promise.all(" in source
    assert "ancestry.getCapabilities()" in source
    assert "assert.deepEqual(capabilityBurst, {" in source
    assert "successful: 32" in source
    assert "overloaded: 0" in source
    assert "unexpectedErrorCodes: []" in source


def test_packaged_clean_quit_uses_native_window_close_and_proves_zero_exit() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    main_source = MAIN_INDEX.read_text(encoding="utf-8")
    runtime_bridge_source = RUNTIME_BRIDGE.read_text(encoding="utf-8")
    quit_start = source.index("async function closeApplicationWindow")
    quit_end = source.index("\nasync function terminateVerificationApplication", quit_start)
    quit_source = source[quit_start:quit_end]
    normal_quit_start = source.index("async function requestNormalApplicationQuit")
    normal_quit_end = source.index(
        "\nasync function expectNormalLaunchWithoutDebugSurface", normal_quit_start
    )
    normal_quit_source = source[normal_quit_start:normal_quit_end]

    assert "async function closeApplicationWindow(sidecarPath: string)" in quit_source
    assert "const activeSidecarPid = await sidecarPid(pid, sidecarPath)" in quit_source
    assert "await browser.closeWindow()" in quit_source
    assert "await Promise.all([" in quit_source
    assert "expectProcessAbsent(pid)" in quit_source
    assert "expectProcessAbsent(activeSidecarPid)" in quit_source
    assert "browser.electron.execute" not in quit_source
    assert source.count("await closeApplicationWindow(copiedSidecarPath)") == 4
    assert "await closeApplicationWindow()" not in source

    assert "CloseMainWindow()" in normal_quit_source
    assert "child.kill('SIGTERM')" in normal_quit_source
    assert "await waitForChildExit(child, 45_000)" in normal_quit_source
    assert "assert.deepEqual(exitStatus, { code: 0, signal: null })" in normal_quit_source

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
        in supervisor_source
    )


def test_normal_launch_waits_for_window_specific_readiness_without_debugging() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    main_source = MAIN_INDEX.read_text(encoding="utf-8")

    assert "outputContainsWindowReadyRecord(output)" in source
    assert "assert.equal(windowReady, true)" in source
    assert "tree.length > 1" not in source
    assert "console.info(WINDOW_READY_RECORD)" in main_source
    assert "window.once('ready-to-show'" in main_source
    assert "const remoteStem = ['remote', 'debugging'].join('-')" in source
    assert "assert.doesNotMatch(launchArguments.join('\\n'), disallowedControlArgument)" in source
    assert (
        "assert.doesNotMatch([...observedCommandLines].join('\\n'), "
        "disallowedControlArgument)" in source
    )
    assert "assert.doesNotMatch(output, /DevTools listening on /u)" in source
    assert "await requestNormalApplicationQuit(child)" in source
    assert "if (!exitedCleanly) await forceCloseProcess(child)" in source


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
        r"await rm\(root, \{\s*force: true,\s*recursive: true,\s*"
        r"maxRetries: 10,\s*retryDelay: 100\s*\}\)",
        source,
    )


def test_packaged_cleanup_terminates_the_process_tree_with_a_deadline() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    assert "async function forceCloseProcess(child: ChildProcessWithoutNullStreams)" in source
    assert "taskkill.exe" in source
    assert "['/PID', String(child.pid), '/T', '/F']" in source
    assert "child.kill('SIGTERM')" in source
    assert "child.kill('SIGKILL')" in source
    assert "await forceCloseProcess(child)" in source


def test_packaged_startup_diagnostics_are_bounded_and_record_failure_context() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    runner_source = PACKAGED_RUNNER.read_text(encoding="utf-8")

    assert (
        "const integrityDiagnosticsPath = process.env.ANCESTRYLLM_INTEGRITY_DIAGNOSTICS" in source
    )
    assert "type StartupExpectation" in source
    assert "await browser.waitUntil" in source
    assert "let lastActual: StartupDiagnostics | null = null" in source
    assert "lastActual = actual" in source
    assert "Packaged startup diagnostics did not match" in source
    assert "JSON.stringify(lastActual)" in source
    assert "async function writeIntegrityDiagnostics" in source
    assert "let phase = 'launch and readiness'" in source
    assert "let failure: unknown" in source
    assert "failure = error" in source
    assert "status: failure ? 'failed' : 'passed'" in source
    assert "await writeIntegrityDiagnostics" in source
    assert "} finally {" in runner_source
    assert "if (preparedPackage.cleanupPath)" in runner_source
    assert re.search(
        r"rmSyncImpl\(preparedPackage\.cleanupPath, \{\s*force: true,\s*"
        r"recursive: true,\s*maxRetries: 10,\s*retryDelay: 100,?\s*\}\)",
        runner_source,
    )
    assert "if (createdUserDataDirectory)" in runner_source


def test_packaged_startup_diagnostic_poll_retries_transient_unavailability() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    helper_start = source.index("async function expectStartupDiagnostics")
    helper_end = source.index("\nasync function expectSafeDiagnosticsAlert", helper_start)
    helper_source = source[helper_start:helper_end]

    assert "startupDiagnostics().catch(() => null)" in helper_source
    assert "if (actual === null) return false" in helper_source
    assert "return matchesStartup(actual, expected)" in helper_source
    assert "lastActual = actual" in helper_source


def test_linux_package_copies_restore_the_chromium_suid_sandbox() -> None:
    source = PACKAGED_RUNNER.read_text(encoding="utf-8")

    assert "function prepareCopiedLinuxSandbox(packageRoot, execFileSyncImpl)" in source
    assert "if (process.platform !== 'linux') return" in source
    assert "const sandboxPath = join(packageRoot, 'chrome-sandbox')" in source
    assert "sandbox.isSymbolicLink()" in source
    assert "sandbox.isFile()" in source
    assert "'--non-interactive', 'chown', 'root:root', '--', sandboxPath" in source
    assert "'--non-interactive', 'chmod', '4755', '--', sandboxPath" in source
    assert "prepared.uid, 0" in source
    assert "prepared.gid, 0" in source
    assert "prepared.mode & 0o7777, 0o4755" in source
    assert "prepareCopiedLinuxSandbox(copiedPackageRoot, execFileSyncImpl)" in source
    assert source.index("cpSync(sourcePackageRoot, copiedPackageRoot") < source.index(
        "prepareCopiedLinuxSandbox(copiedPackageRoot, execFileSyncImpl)"
    )
    assert "--no-sandbox" not in source
