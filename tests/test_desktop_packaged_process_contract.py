"""Contracts for packaged Electron process-tree evidence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_SPEC = ROOT / "desktop" / "e2e" / "packaged-shell.spec.ts"


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


def test_packaged_renderer_identity_does_not_depend_on_process_arguments() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    assert "--type=renderer" not in source
    assert "--enable-sandbox" not in source
    assert "tree.length > 1" in source
    assert "expect(output).not.toContain('DevTools listening on ')" in source
