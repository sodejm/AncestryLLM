"""Functional tests for staging and committing synchronized wiki changes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "commit_wiki_changes.py"
_SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"
_BOT_NAME = "github-actions[bot]"
_BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _initialize_repository(repository: Path, files: dict[str, str] | None = None) -> str:
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    for name, content in (files or {"Home.md": "# Home\n"}).items():
        (repository / name).write_text(content, encoding="utf-8")
    _git(repository, "add", "--all")
    _git(
        repository,
        "-c",
        "user.name=Initial Author",
        "-c",
        "user.email=initial@example.test",
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--quiet",
        "-m",
        "initial wiki",
    )
    return _git(repository, "rev-parse", "HEAD").strip()


def _run_commit(repository: Path, output: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(_SCRIPT),
        "--repository",
        str(repository),
        "--source-sha",
        _SOURCE_SHA,
    ]
    if output is not None:
        command.extend(["--output", str(output)])
    return subprocess.run(command, check=False, capture_output=True, text=True)


def test_unchanged_repository_succeeds_without_a_commit(tmp_path: Path) -> None:
    repository = tmp_path / "wiki"
    initial_sha = _initialize_repository(repository)
    output = tmp_path / "github-output"

    result = _run_commit(repository, output)

    assert result.returncode == 0, result.stderr
    assert "no changes to commit" in result.stdout
    assert _git(repository, "rev-parse", "HEAD").strip() == initial_sha
    assert _git(repository, "status", "--porcelain") == ""
    assert output.read_text(encoding="utf-8") == "committed=false\ncommit-sha=\n"


def test_untracked_page_is_staged_and_committed(tmp_path: Path) -> None:
    repository = tmp_path / "wiki"
    _initialize_repository(repository)
    (repository / "Guide.md").write_text("# Guide\n", encoding="utf-8")

    result = _run_commit(repository)

    assert result.returncode == 0, result.stderr
    assert _git(repository, "show", "--format=", "--name-status", "HEAD") == "A\tGuide.md\n"
    assert _git(repository, "status", "--porcelain") == ""


def test_modified_and_deleted_pages_are_committed(tmp_path: Path) -> None:
    repository = tmp_path / "wiki"
    _initialize_repository(
        repository,
        {"Home.md": "# Home\n", "Stale.md": "# Stale\n"},
    )
    (repository / "Home.md").write_text("# Updated home\n", encoding="utf-8")
    (repository / "Stale.md").unlink()

    result = _run_commit(repository)

    assert result.returncode == 0, result.stderr
    assert _git(repository, "show", "--format=", "--name-status", "HEAD").splitlines() == [
        "M\tHome.md",
        "D\tStale.md",
    ]
    assert _git(repository, "status", "--porcelain") == ""


def test_commit_uses_bot_identity_and_source_sha_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "wiki"
    _initialize_repository(repository)
    (repository / "Home.md").write_text("# Updated home\n", encoding="utf-8")
    output = tmp_path / "github-output"
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Untrusted Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "untrusted-author@example.test")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Untrusted Committer")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "untrusted-committer@example.test")

    result = _run_commit(repository, output)

    assert result.returncode == 0, result.stderr
    metadata = _git(repository, "show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", "HEAD")
    assert metadata.splitlines() == [
        _BOT_NAME,
        _BOT_EMAIL,
        _BOT_NAME,
        _BOT_EMAIL,
        f"docs: synchronize from {_SOURCE_SHA}",
        "",
    ]
    commit_sha = _git(repository, "rev-parse", "HEAD").strip()
    assert output.read_text(encoding="utf-8") == f"committed=true\ncommit-sha={commit_sha}\n"
