"""Regression tests for deterministic documentation-to-wiki mirroring."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[1] / "scripts" / "sync_wiki_docs.py"


def _run_sync(source: Path, destination: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--source",
            str(source),
            "--destination",
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _write_source(source: Path) -> None:
    nested = source / "guides"
    nested.mkdir(parents=True)
    (source / "Home.md").write_text("# Home\n", encoding="utf-8")
    (source / "_Sidebar.md").write_text("[Guide](Guide.md)\n", encoding="utf-8")
    (nested / "Guide.md").write_text("# Guide\n", encoding="utf-8")
    (nested / "ignored.txt").write_text("not a wiki page\n", encoding="utf-8")


def test_sync_flattens_markdown_and_removes_only_stale_managed_pages(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "wiki"
    _write_source(source)
    (destination / ".git").mkdir(parents=True)
    (destination / ".git" / "preserved").write_text("git metadata\n", encoding="utf-8")
    (destination / "assets").mkdir()
    (destination / "assets" / "logo.txt").write_text("asset\n", encoding="utf-8")
    (destination / "CNAME").write_text("docs.example.test\n", encoding="utf-8")
    (destination / "Stale.md").write_text("# Stale\n", encoding="utf-8")

    result = _run_sync(source, destination)

    assert result.returncode == 0, result.stderr
    assert (destination / "Home.md").read_text(encoding="utf-8") == "# Home\n"
    assert (destination / "Guide.md").read_text(encoding="utf-8") == "# Guide\n"
    assert (destination / "_Sidebar.md").read_text(encoding="utf-8") == "[Guide](Guide)\n"
    assert not (destination / "Stale.md").exists()
    assert not (destination / "ignored.txt").exists()
    assert (destination / ".git" / "preserved").read_text(encoding="utf-8") == "git metadata\n"
    assert (destination / "assets" / "logo.txt").read_text(encoding="utf-8") == "asset\n"
    assert (destination / "CNAME").read_text(encoding="utf-8") == "docs.example.test\n"


def test_second_sync_leaves_an_empty_git_diff(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "wiki"
    _write_source(source)
    destination.mkdir()
    subprocess.run(["git", "init", "--quiet", str(destination)], check=True)

    first = _run_sync(source, destination)
    assert first.returncode == 0, first.stderr
    subprocess.run(["git", "-C", str(destination), "add", "--all"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "user.name=Wiki Sync Test",
            "-c",
            "user.email=wiki-sync@example.test",
            "commit",
            "--quiet",
            "-m",
            "initial wiki",
        ],
        check=True,
    )

    second = _run_sync(source, destination)

    assert second.returncode == 0, second.stderr
    assert "destination is already synchronized" in second.stdout
    status = subprocess.run(
        ["git", "-C", str(destination), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
