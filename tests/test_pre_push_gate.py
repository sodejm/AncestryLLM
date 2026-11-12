"""Exercise the local push gate against disposable Git repositories and tools."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import run_pre_push
from scripts.run_pre_push import PushGateError, gate_commands, require_test_changes

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_pre_push.py"


def _git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *arguments],
        cwd=root,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        text=True,
    ).strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "fictional@example.invalid")
    _git(root, "config", "user.name", "Fictional Developer")
    (root / "README.md").write_text("# Example\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Initial tree")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(root, "switch", "-c", "feature/gate")
    return root


@pytest.mark.parametrize(
    "paths",
    [
        {"src/ancestryllm/example.py"},
        {"scripts/example.py", "tests/README.md"},
        {"Makefile"},
        {"desktop/src/main/example.ts", "tests/test_example.py"},
    ],
)
def test_behavior_changes_without_matching_tests_fail(paths: set[str]) -> None:
    with pytest.raises(PushGateError, match="PUSHGATE_TESTS_REQUIRED"):
        require_test_changes(paths)


@pytest.mark.parametrize(
    "paths",
    [
        {"src/ancestryllm/example.py", "tests/test_example.py"},
        {"scripts/example.py", "tests/test_example.py"},
        {"desktop/src/main/example.ts", "desktop/src/main/example.test.ts"},
        {"docs/reference/CI.md"},
        {"uv.lock"},
    ],
)
def test_test_changes_or_nonbehavior_changes_pass(paths: set[str]) -> None:
    require_test_changes(paths)


def test_stack_specific_checks_are_selected_by_branch_changes() -> None:
    core = gate_commands(set())
    assert core == [
        ["make", "lock-check"],
        ["make", "lint"],
        ["make", "typecheck"],
        ["make", "test"],
        ["make", "security"],
    ]
    selected = gate_commands(
        {"desktop/pnpm-lock.yaml", ".github/workflows/ci.yml", "containers/Dockerfile"}
    )
    assert selected[: len(core)] == core
    assert ["make", "desktop-install"] in selected
    assert ["make", "desktop-check"] in selected
    assert ["pnpm", "--dir", "desktop", "audit", "--audit-level", "high"] in selected
    assert ["pnpm", "--dir", "desktop", "check:secrets"] in selected
    assert ["make", "workflow-audit"] in selected
    assert ["make", "container-policy"] in selected


def _fake_tools(root: Path, *, trivy_status: int = 0) -> dict[str, str]:
    tools = root.parent / "tools"
    tools.mkdir()
    for name, body in {
        "make": ('printf "%s\\n" "$*" >> "$GATE_LOG"\n[ "$*" != "$GATE_FAIL_TARGET" ] || exit 1\n'),
        "trivy": (
            'printf "%s\\n" "$*" >> "$GATE_LOG"\n'
            "for target; do :; done\n"
            'test -f "$target/README.md" || exit 90\n'
            'test ! -e "$target/private-records.ged" || exit 91\n'
            'test ! -e "$target/.env" || exit 92\n'
            f"exit {trivy_status}\n"
        ),
    }.items():
        executable = tools / name
        executable.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        executable.chmod(0o755)
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith(("PRE_COMMIT_", "GIT_")):
            environment.pop(name)
    environment["PATH"] = str(tools) + os.pathsep + environment["PATH"]
    environment["GATE_LOG"] = str(root.parent / "gate.log")
    return environment


def _run(root: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_scan_uses_only_committed_files_and_enforces_finding_exit_code(repository: Path) -> None:
    (repository / "private-records.ged").write_text("fictional local data", encoding="utf-8")
    (repository / ".env").write_text("FICTIONAL_SETTING=local\n", encoding="utf-8")
    environment = _fake_tools(repository)
    result = _run(repository, environment)
    assert result.returncode == 0, result.stderr
    log = Path(environment["GATE_LOG"]).read_text(encoding="utf-8")
    assert "--scanners vuln,misconfig,secret" in log
    assert "--severity HIGH,CRITICAL --exit-code 1" in log
    assert "--ignorefile=" in log
    assert "security\n" in log


def test_trivy_failure_blocks_later_gates(repository: Path) -> None:
    environment = _fake_tools(repository, trivy_status=1)
    result = _run(repository, environment)
    assert result.returncode != 0
    assert "PUSHGATE_COMMAND_FAILED" in result.stderr
    assert "lock-check" not in Path(environment["GATE_LOG"]).read_text(encoding="utf-8")


def test_missing_required_tool_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run_pre_push.shutil, "which", lambda _name: None)
    with pytest.raises(PushGateError, match="PUSHGATE_TOOL_MISSING: install trivy"):
        run_pre_push._execute(["trivy", "filesystem", str(tmp_path)], tmp_path)


@pytest.mark.parametrize("failure", ["trivy", "lint"])
def test_failed_hook_prevents_remote_branch_creation(repository: Path, failure: str) -> None:
    remote = repository.parent / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")
    hooks = repository.parent / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT))}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    environment = _fake_tools(repository, trivy_status=1 if failure == "trivy" else 0)
    if failure == "lint":
        environment["GATE_FAIL_TARGET"] = "lint"
    result = subprocess.run(
        [
            "git",
            "-c",
            f"core.hooksPath={hooks}",
            "push",
            str(remote),
            "HEAD:refs/heads/feature/gate",
        ],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "PUSHGATE_COMMAND_FAILED" in result.stderr
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/") == ""
    log = Path(environment["GATE_LOG"]).read_text(encoding="utf-8")
    assert "security\n" not in log


def test_dirty_tracked_files_are_rejected_before_tools_run(repository: Path) -> None:
    environment = _fake_tools(repository)
    (repository / "README.md").write_text("Uncommitted change\n", encoding="utf-8")
    result = _run(repository, environment)
    assert result.returncode != 0
    assert "PUSHGATE_DIRTY_TREE" in result.stderr
    assert not Path(environment["GATE_LOG"]).exists()


def test_pushing_a_different_commit_is_rejected(repository: Path) -> None:
    environment = _fake_tools(repository)
    environment["PRE_COMMIT_TO_REF"] = "0" * 40
    result = _run(repository, environment)
    assert result.returncode != 0
    assert "PUSHGATE_HEAD_MISMATCH" in result.stderr


def test_branch_tests_count_across_incremental_pushes(repository: Path) -> None:
    for path in ("scripts/example.py", "tests/test_example.py"):
        destination = repository / path
        destination.parent.mkdir(exist_ok=True)
        destination.write_text("# Fictional implementation and test\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Add behavior with tests")
    previous = _git(repository, "rev-parse", "HEAD")
    (repository / "scripts/example.py").write_text("# Follow-up implementation\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Refine behavior")
    environment = _fake_tools(repository)
    environment["PRE_COMMIT_FROM_REF"] = previous
    environment["PRE_COMMIT_TO_REF"] = _git(repository, "rev-parse", "HEAD")
    result = _run(repository, environment)
    assert result.returncode == 0, result.stderr


def test_deleted_tests_do_not_satisfy_development_check(repository: Path) -> None:
    tests = repository / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("# Existing test\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Existing tests")
    _git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    (tests / "test_example.py").unlink()
    scripts = repository / "scripts"
    scripts.mkdir()
    (scripts / "example.py").write_text("# New behavior\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Remove tests and add behavior")
    result = _run(repository, _fake_tools(repository))
    assert result.returncode != 0
    assert "PUSHGATE_TESTS_REQUIRED" in result.stderr
