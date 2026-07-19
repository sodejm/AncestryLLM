#!/usr/bin/env python3
"""Stage and commit changed wiki content with deterministic bot metadata."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
_SOURCE_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")


@dataclass(frozen=True)
class CommitResult:
    """The outcome of staging and possibly committing wiki changes."""

    committed: bool
    commit_sha: str | None = None


class WikiCommitError(RuntimeError):
    """A deterministic, user-facing wiki commit failure."""


def _run_git(
    repository: Path,
    *args: str,
    check: bool = True,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - arguments are passed without a shell.
        ["git", "-C", str(repository), *args],  # noqa: S607 - Git is required on PATH.
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if check and result.returncode != 0:
        detail = (
            result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        )
        raise WikiCommitError(f"git {args[0]} failed: {detail}")
    return result


def _validate_repository(repository: Path) -> None:
    if repository.is_symlink():
        raise WikiCommitError("symlinked repository directory is not supported")
    if not repository.exists():
        raise WikiCommitError(f"repository directory does not exist: {repository}")
    if not repository.is_dir():
        raise WikiCommitError(f"repository path is not a directory: {repository}")

    top_level = _run_git(repository, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top_level).resolve() != repository.resolve():
        raise WikiCommitError(f"repository path is not the Git worktree root: {repository}")


def commit_wiki_changes(repository: Path, source_sha: str) -> CommitResult:
    """Stage all wiki changes and commit them with traceable bot metadata."""
    if not _SOURCE_SHA.fullmatch(source_sha):
        raise WikiCommitError("source SHA must contain 7 to 64 hexadecimal characters")
    _validate_repository(repository)

    _run_git(repository, "add", "--all")
    staged_diff = _run_git(repository, "diff", "--cached", "--quiet", check=False)
    if staged_diff.returncode == 0:
        return CommitResult(committed=False)
    if staged_diff.returncode != 1:
        detail = staged_diff.stderr.strip() or f"exit status {staged_diff.returncode}"
        raise WikiCommitError(f"git diff failed: {detail}")

    identity_environment = os.environ.copy()
    identity_environment.update(
        {
            "GIT_AUTHOR_NAME": BOT_NAME,
            "GIT_AUTHOR_EMAIL": BOT_EMAIL,
            "GIT_COMMITTER_NAME": BOT_NAME,
            "GIT_COMMITTER_EMAIL": BOT_EMAIL,
        }
    )
    _run_git(
        repository,
        "-c",
        "commit.gpgSign=false",
        "commit",
        "-m",
        f"docs: synchronize from {source_sha}",
        environment=identity_environment,
    )
    commit_sha = _run_git(repository, "rev-parse", "HEAD").stdout.strip()
    return CommitResult(committed=True, commit_sha=commit_sha)


def _write_workflow_output(path: Path, result: CommitResult) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(f"committed={'true' if result.committed else 'false'}\n")
        output.write(f"commit-sha={result.commit_sha or ''}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        required=True,
        help="wiki Git worktree whose current changes should be committed",
    )
    parser.add_argument(
        "--source-sha",
        required=True,
        help="source repository commit SHA included in the wiki commit message",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional GitHub Actions output file receiving committed and commit-sha values",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = commit_wiki_changes(args.repository, args.source_sha)
        if args.output is not None:
            _write_workflow_output(args.output, result)
    except (OSError, WikiCommitError) as error:
        print(f"wiki-commit: {error}", file=sys.stderr)
        return 1

    if result.committed:
        print(f"wiki-commit: created {result.commit_sha}")
    else:
        print("wiki-commit: no changes to commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
