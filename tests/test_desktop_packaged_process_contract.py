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

    assert "browser.newBrowserCDPSession()" in source
    assert "session.send('SystemInfo.getProcessInfo')" in source
    assert re.search(r"\.filter\(\(record\) => record\.type === 'renderer'\)", source)
    assert "rendererPids.has(record.pid)" in source
    assert "await session.detach()" in source
    assert re.search(
        r"packagedProcessTreeMetrics\(\s*browser:\s*Browser,\s*rootPid:\s*number",
        source,
    )
    assert re.search(
        r"correlatedRendererProcess\s*=\s*tree\.find\("
        r"\(record\)\s*=>\s*rendererPids\.has\(record\.pid\)\)",
        source,
    )
    assert "correlatedRendererProcess?.commandLine" in source
    assert ".toContain('--enable-sandbox')" in source


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
