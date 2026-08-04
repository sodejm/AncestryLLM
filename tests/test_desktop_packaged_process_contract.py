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
