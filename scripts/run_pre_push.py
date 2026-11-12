#!/usr/bin/env python3
"""Block pushes until the committed branch passes local development and security gates."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


class PushGateError(RuntimeError):
    """An actionable, coded failure that must block the push."""


def _execute(arguments: list[str], root: Path, *, capture: bool = False) -> bytes:
    executable = shutil.which(arguments[0])
    if executable is None:
        raise PushGateError(
            f"PUSHGATE_TOOL_MISSING: install {arguments[0]} before pushing; "
            "see docs/reference/LOCAL_PUSH_GATE.md."
        )
    result = subprocess.run(  # noqa: S603 - fixed tool arguments; no shell evaluation
        [executable, *arguments[1:]],
        cwd=root,
        stdout=subprocess.PIPE if capture else None,
        check=False,
    )
    if result.returncode:
        raise PushGateError(
            f"PUSHGATE_COMMAND_FAILED: {arguments[0]} exited {result.returncode}; "
            "resolve the reported failure before pushing."
        )
    return result.stdout if capture else b""


def _git(root: Path, *arguments: str) -> str:
    return (
        _execute(["git", *arguments], root, capture=True).decode("utf-8", "surrogateescape").strip()
    )


def _check_checkout(root: Path, expected_head: str) -> None:
    if _git(root, "rev-parse", "HEAD") != expected_head:
        raise PushGateError("PUSHGATE_HEAD_MISMATCH: HEAD changed while the gate was running.")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise PushGateError(
            "PUSHGATE_DIRTY_TREE: commit or stash tracked changes so checks test the pushed commit."
        )


def require_test_changes(paths: set[str], *, eligible_test_paths: set[str] | None = None) -> None:
    """Require updated tests in each changed implementation stack across the branch."""
    eligible = paths if eligible_test_paths is None else eligible_test_paths
    python_behavior = any(
        (
            path.startswith(
                ("src/", "scripts/", "containers/", ".github/actions/", ".github/workflows/")
            )
            and not path.endswith((".md", ".lock"))
        )
        or path in {"Makefile", ".pre-commit-config.yaml"}
        for path in paths
    )
    python_tests = any(
        path.startswith("tests/") and Path(path).name.startswith("test_") and path.endswith(".py")
        for path in eligible
    )
    desktop_behavior = any(
        path.startswith("desktop/")
        and not path.endswith((".md", ".test.ts", ".test.tsx", ".test.mjs", ".spec.ts"))
        and path != "desktop/pnpm-lock.yaml"
        for path in paths
    )
    desktop_tests = any(
        path.startswith("desktop/")
        and path.endswith((".test.ts", ".test.tsx", ".test.mjs", ".spec.ts"))
        for path in eligible
    )
    missing = []
    if python_behavior and not python_tests:
        missing.append("Python/repository tooling (tests/test_*.py)")
    if desktop_behavior and not desktop_tests:
        missing.append("desktop (*.test.ts, *.test.tsx, *.test.mjs, *.spec.ts)")
    if missing:
        raise PushGateError(
            "PUSHGATE_TESTS_REQUIRED: add or update committed tests for " + ", ".join(missing) + "."
        )


def gate_commands(paths: set[str]) -> list[list[str]]:
    """Select canonical gates plus checks for changed desktop, workflow, and container inputs."""
    commands = [
        ["make", target] for target in ("lock-check", "lint", "typecheck", "test", "security")
    ]
    if any(path.startswith("desktop/") for path in paths):
        commands.extend(
            [
                ["make", "desktop-install"],
                ["make", "desktop-check"],
                ["pnpm", "--dir", "desktop", "audit", "--audit-level", "high"],
                ["pnpm", "--dir", "desktop", "check:secrets"],
            ]
        )
    if any(path.startswith((".github/workflows/", ".github/actions/")) for path in paths):
        commands.append(["make", "workflow-audit"])
    if any(path.startswith("containers/") for path in paths):
        commands.append(["make", "container-policy"])
    return commands


def run_gate(root: Path) -> None:
    """Validate the checked-out commit, scan its tracked snapshot, and run all selected checks."""
    root = Path(_git(root, "rev-parse", "--show-toplevel"))
    head = _git(root, "rev-parse", "HEAD")
    target = os.environ.get("PRE_COMMIT_TO_REF")
    if target and _git(root, "rev-parse", target) != head:
        raise PushGateError(
            "PUSHGATE_HEAD_MISMATCH: push only the currently checked-out branch and commit."
        )
    _check_checkout(root, head)
    base = _git(root, "merge-base", "origin/main", head)
    paths = set(_git(root, "diff", "--name-only", "-z", base, head).split("\0")) - {""}
    eligible = set(
        _git(
            root, "diff", "--name-only", "--no-renames", "--diff-filter=AMT", "-z", base, head
        ).split("\0")
    ) - {""}
    require_test_changes(paths, eligible_test_paths=eligible)
    print("Pre-push: Trivy on the committed tree (HIGH/CRITICAL findings block).", flush=True)
    archive = _execute(["git", "archive", "--format=tar", head], root, capture=True)
    with tempfile.TemporaryDirectory(prefix="ancestryllm-push-") as temporary:
        snapshot = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
            contents.extractall(snapshot, filter="data")
        _execute(
            [
                "trivy",
                "filesystem",
                "--scanners",
                "vuln,misconfig,secret",
                "--severity",
                "HIGH,CRITICAL",
                "--exit-code",
                "1",
                "--no-progress",
                "--skip-version-check",
                "--disable-telemetry",
                "--timeout",
                "10m",
                "--config=",
                "--ignorefile=",
                "--secret-config=",
                str(snapshot),
            ],
            root,
        )
    for command in gate_commands(paths):
        print("Pre-push: " + " ".join(command), flush=True)
        _execute(command, root)
    _check_checkout(root, head)
    print("Pre-push: all local gates passed.", flush=True)


def main() -> int:
    """Return a nonzero hook status for validation, tool, or scan failures."""
    try:
        run_gate(Path.cwd())
    except (PushGateError, OSError, tarfile.TarError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
