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
    (source / "_Sidebar.md").write_text("[Guide](guides/Guide.md)\n", encoding="utf-8")
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
            "-c",
            "commit.gpgsign=false",
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


def test_sync_resolves_nested_links_before_flattening_pages(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "wiki"
    (source / "guides" / "advanced").mkdir(parents=True)
    (source / "assets").mkdir()
    (source / "Home.md").write_text(
        '[Child](guides/Child.md?view=full#start "Child title")\n',
        encoding="utf-8",
    )
    (source / "_Sidebar.md").write_text("[Child](guides/Child.md)\n", encoding="utf-8")
    (source / "guides" / "Child.md").write_text(
        "# Start\n\n[Root](../Home.md) [Sibling](Sibling%20Page.md#part) "
        "[Parent asset](../assets/logo.svg)\n",
        encoding="utf-8",
    )
    (source / "guides" / "Sibling Page.md").write_text("# Sibling\n\n## Part\n", encoding="utf-8")
    (source / "assets" / "logo.svg").write_text("<svg/>\n", encoding="utf-8")
    destination.mkdir()

    result = _run_sync(source, destination)

    assert result.returncode == 0, result.stderr
    assert (destination / "Home.md").read_text(encoding="utf-8") == (
        '[Child](Child?view=full#start "Child title")\n'
    )
    assert (destination / "Child.md").read_text(encoding="utf-8") == (
        "# Start\n\n[Root](Home) [Sibling](Sibling%20Page#part) [Parent asset](assets/logo.svg)\n"
    )
    assert (destination / "_Sidebar.md").read_text(encoding="utf-8") == "[Child](Child)\n"
    assert (destination / "assets" / "logo.svg").read_text(encoding="utf-8") == "<svg/>\n"


def test_sync_preserves_query_only_same_page_links(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "wiki"
    _write_source(source)
    (source / "guides" / "Guide.md").write_text(
        "# Guide\n\n## Topic\n\n[Filtered view](?view=compact#topic)\n",
        encoding="utf-8",
    )
    destination.mkdir()

    result = _run_sync(source, destination)

    assert result.returncode == 0, result.stderr
    assert "[Filtered view](?view=compact#topic)" in (destination / "Guide.md").read_text(
        encoding="utf-8"
    )


def test_sync_removes_only_assets_recorded_as_previously_managed(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "wiki"
    (source / "assets").mkdir(parents=True)
    (source / "Home.md").write_text("# Home\n\n![Logo](assets/logo.svg)\n", encoding="utf-8")
    (source / "_Sidebar.md").write_text("[Home](Home.md)\n", encoding="utf-8")
    (source / "assets" / "logo.svg").write_text("<svg/>\n", encoding="utf-8")
    (destination / "assets").mkdir(parents=True)
    (destination / "assets" / "user-owned.txt").write_text("keep\n", encoding="utf-8")

    first = _run_sync(source, destination)
    assert first.returncode == 0, first.stderr
    assert (destination / "assets" / "logo.svg").is_file()

    (source / "Home.md").write_text("# Home\n", encoding="utf-8")
    second = _run_sync(source, destination)

    assert second.returncode == 0, second.stderr
    assert not (destination / "assets" / "logo.svg").exists()
    assert (destination / "assets" / "user-owned.txt").read_text(encoding="utf-8") == "keep\n"


def test_invalid_asset_manifest_fails_before_destination_mutation(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "wiki"
    _write_source(source)
    destination.mkdir()
    (destination / "Stale.md").write_text("# Preserve until valid\n", encoding="utf-8")
    (destination / ".ancestryllm-managed-assets.json").write_text(
        '{"version": 1, "assets": ["../escape.svg"]}\n', encoding="utf-8"
    )

    result = _run_sync(source, destination)

    assert result.returncode == 1
    assert "unsafe path" in result.stderr
    assert (destination / "Stale.md").read_text(encoding="utf-8") == "# Preserve until valid\n"
    assert not (destination / "Home.md").exists()


def test_sync_refuses_to_overwrite_unrecorded_destination_asset(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "wiki"
    (source / "assets").mkdir(parents=True)
    (source / "Home.md").write_text("# Home\n\n![Logo](assets/logo.svg)\n", encoding="utf-8")
    (source / "_Sidebar.md").write_text("[Home](Home.md)\n", encoding="utf-8")
    (source / "assets" / "logo.svg").write_text("managed\n", encoding="utf-8")
    (destination / "assets").mkdir(parents=True)
    (destination / "assets" / "logo.svg").write_text("user owned\n", encoding="utf-8")

    result = _run_sync(source, destination)

    assert result.returncode == 1
    assert "unrecorded wiki asset" in result.stderr
    assert (destination / "assets" / "logo.svg").read_text(encoding="utf-8") == "user owned\n"
    assert not (destination / "Home.md").exists()


def test_git_metadata_manifest_path_is_rejected_before_destination_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "wiki"
    _write_source(source)
    (destination / ".git").mkdir(parents=True)
    config = destination / ".git" / "config"
    config.write_text("preserve git metadata\n", encoding="utf-8")
    (destination / "Stale.md").write_text("# Preserve until valid\n", encoding="utf-8")
    (destination / ".ancestryllm-managed-assets.json").write_text(
        '{"version": 1, "assets": [".git/config"]}\n', encoding="utf-8"
    )

    result = _run_sync(source, destination)

    assert result.returncode == 1
    assert "unsafe path" in result.stderr
    assert config.read_text(encoding="utf-8") == "preserve git metadata\n"
    assert (destination / "Stale.md").read_text(encoding="utf-8") == "# Preserve until valid\n"
    assert not (destination / "Home.md").exists()


def test_symlinked_destination_is_rejected_before_manifest_inspection(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    actual_destination = tmp_path / "outside-wiki"
    destination = tmp_path / "wiki"
    _write_source(source)
    actual_destination.mkdir()
    (actual_destination / ".ancestryllm-managed-assets.json").write_text(
        "not valid JSON\n", encoding="utf-8"
    )
    destination.symlink_to(actual_destination, target_is_directory=True)

    result = _run_sync(source, destination)

    assert result.returncode == 1
    assert "symlinked destination directory" in result.stderr
    assert "invalid" not in result.stderr
