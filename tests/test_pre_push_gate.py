"""Exercise the local push gate against disposable Git repositories and tools."""

from __future__ import annotations

import json
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
        {"pyproject.toml"},
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


def _fake_tools(
    root: Path, *, trivy_status: int = 0, trivy_version: str = "0.75.1"
) -> dict[str, str]:
    tools = root.parent / "tools"
    tools.mkdir()
    for name, body in {
        "make": ('printf "%s\\n" "$*" >> "$GATE_LOG"\n[ "$*" != "$GATE_FAIL_TARGET" ] || exit 1\n'),
        "trivy": (
            'printf "%s\\n" "$*" >> "$GATE_LOG"\n'
            'if [ "$1" = "--version" ]; then printf "Version: %s\\n" "$TRIVY_VERSION"; exit 0; fi\n'
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
    environment["TRIVY_VERSION"] = trivy_version
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
    assert "--file-patterns uv:scripts/run_pinned_semgrep\\.py\\.lock" in log
    assert "security\n" in log


def test_outdated_trivy_is_rejected_before_scanning(repository: Path) -> None:
    environment = _fake_tools(repository, trivy_version="0.74.0")
    result = _run(repository, environment)
    assert result.returncode != 0
    assert "PUSHGATE_TRIVY_VERSION" in result.stderr
    log = Path(environment["GATE_LOG"]).read_text(encoding="utf-8")
    assert "--scanners vuln,misconfig,secret" not in log


def test_renaming_behavior_out_of_a_classified_tree_still_requires_tests(repository: Path) -> None:
    source = repository / "src/ancestryllm"
    source.mkdir(parents=True)
    (source / "example.py").write_text("# Original behavior\n", encoding="utf-8")
    tests = repository / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("# Existing test\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Existing behavior and tests")
    _git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    tools = repository / "tools"
    tools.mkdir()
    _git(repository, "mv", "src/ancestryllm/example.py", "tools/example.py")
    _git(repository, "commit", "-m", "Move behavior out of the classified tree")
    result = _run(repository, _fake_tools(repository))
    assert result.returncode != 0
    assert "PUSHGATE_TESTS_REQUIRED" in result.stderr


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


def test_gate_commands_do_not_inherit_hook_repository_context(
    monkeypatch: pytest.MonkeyPatch, repository: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "other.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    result = run_pre_push._execute(
        ["git", "rev-parse", "--show-toplevel"], repository, capture=True
    )
    assert Path(result.decode().strip()).resolve() == repository.resolve()


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
    write_count(repository, "scripts/example.py")
    write_handoff(repository, handoff_record("scripts/example.py", "implementation"))
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Add behavior with tests")
    previous = _git(repository, "rev-parse", "HEAD")
    with (repository / "scripts/example.py").open("a", encoding="utf-8") as source:
        source.write("# Follow-up clarification\n")
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


def handoff_record(code, stage):
    """Describe an ordinary callable and its two observable outcomes."""
    paths = [code, "tests/test_behavior.py"]
    return {
        "stage": stage,
        "request": "Clamp a negative count to zero while preserving positive counts.",
        "scope": "Expose clamp_count in the count module and exercise its public input.",
        "setup": "Use Python 3 and the standard library unittest runner.",
        "constraints": "Return integers and accept zero as a valid count.",
        "steps": [
            {
                "action": "Define clamp_count(value) and add positive and negative input tests.",
                "paths": paths,
                "verify": {
                    "command": "python3 -m unittest discover -s tests -p test_behavior.py",
                    "expected": "Both tests pass for positive and negative inputs.",
                },
            }
        ],
        "acceptance": [
            {
                "kind": kind,
                "entrypoint": "clamp_count(value)",
                "given": given,
                "when": "Call clamp_count with the given integer.",
                "then": expected,
                "paths": paths,
                "runner": "unittest",
                "test": "tests/test_behavior.py::Counts::test_" + kind,
            }
            for kind, given, expected in [
                ("success", "A count of 4", "Return 4"),
                ("boundary", "A count of -1", "Return 0"),
            ]
        ],
        "pending": [],
    }


def write_handoff(root, record):
    """Write the feature's versioned plan in the fixture repository."""
    path = root / "docs/session-plans/clamp-count.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def write_count(root, code, boundary="self.assertEqual(clamp(-1), 0)"):
    """Install the public function and separate executable acceptance cases."""
    source = root / code
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def clamp_count(value):\n    return max(0, value)\n", encoding="utf-8")
    suite = root / "tests/test_behavior.py"
    suite.parent.mkdir(exist_ok=True)
    suite.write_text(
        "import runpy\nimport unittest\n"
        f"clamp = runpy.run_path({code!r})['clamp_count']\n"
        "class Counts(unittest.TestCase):\n"
        "    def test_success(self):\n        self.assertEqual(clamp(4), 4)\n"
        f"    def test_boundary(self):\n        {boundary}\n",
        encoding="utf-8",
    )


def exercise_handoff_success(root, git, code):
    """Commit a plan for future files and push it, then implement and push the feature."""
    before = git("rev-parse", "HEAD").stdout
    write_handoff(root, {"stage": "plan"})
    git("add", "docs")
    write_handoff(root, handoff_record(code, "plan"))
    result = git("commit", "-m", "Incomplete staged plan", success=False)
    assert result.returncode and b"request needs concrete text" in result.stderr, result.stderr
    assert git("rev-parse", "HEAD").stdout == before
    incomplete = handoff_record(code, "plan")
    incomplete["steps"][0]["verify"] = {"command": "python3 -m unittest"}
    write_handoff(root, incomplete)
    git("add", "docs")
    result = git("commit", "-m", "Omit the expected result", success=False)
    assert result.returncode and b"step.verify.expected" in result.stderr
    assert git("rev-parse", "HEAD").stdout == before
    write_handoff(root, handoff_record(code, "plan"))
    git("add", "docs")
    git("commit", "-m", "Plan count behavior")
    git("push", "origin", "feature/gate")
    write_count(root, code)
    git("add", code, "tests/test_behavior.py")
    result = git("commit", "-m", "Implementation without mapping", success=False)
    assert result.returncode and b"missing implementation acceptance coverage" in result.stderr
    write_handoff(root, handoff_record(code, "implementation"))
    git("add", "docs")
    git("commit", "-m", "Implement count behavior")
    git("push", "origin", "feature/gate")
    assert (
        git("rev-parse", "HEAD").stdout.strip() in git("ls-remote", "origin", "feature/gate").stdout
    )


def exercise_handoff_rejections(root, git, code):
    """Keep the remote branch absent for skipped, failing, and missing acceptance."""
    record = handoff_record(code, "implementation")
    write_handoff(root, record)
    for body, expected in [
        ("pass", b"empty acceptance test"),
        ("self.skipTest('unavailable')", b"required test did not pass"),
        ("self.assertEqual(clamp(-1), 7)", b"required test did not pass"),
    ]:
        write_count(root, code, body)
        git("add", "docs", code, "tests/test_behavior.py")
        git("commit", "-m", "Record a boundary case")
        result = git("push", "origin", "feature/gate", success=False)
        assert result.returncode and expected in result.stderr + result.stdout, result.stderr
        assert not git("ls-remote", "origin", "feature/gate").stdout
    write_count(root, code)
    (root / code).write_text("def clamp_count(value):\n    return value\n", encoding="utf-8")
    git("add", code, "tests/test_behavior.py")
    git("commit", "-m", "Remove the clamp while retaining its tests")
    result = git("push", "origin", "feature/gate", success=False)
    assert result.returncode and b"required test did not pass" in result.stdout + result.stderr
    assert not git("ls-remote", "origin", "feature/gate").stdout
    # Restore the implementation so the next failure isolates the missing selector.
    write_count(root, code)
    git("add", code)
    record["acceptance"][1]["test"] = "tests/test_missing.py::Counts::test_boundary"
    write_handoff(root, record)
    git("add", "docs")
    git("commit", "-m", "Point to missing selector")
    result = git("push", "origin", "feature/gate", success=False)
    assert result.returncode and b"missing acceptance test" in result.stderr + result.stdout, (
        result.stderr
    )
    assert not git("ls-remote", "origin", "feature/gate").stdout


@pytest.fixture
def hook_git(repository: Path):
    """Invoke the checked-in hook entry points with a real disposable Git remote."""
    remote = repository.parent / "remote.git"
    _git(repository, "init", "--bare", str(remote))
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "origin", "main")
    hooks = repository.parent / "hooks"
    hooks.mkdir()
    for name, argv in [
        ("pre-commit", [sys.executable, str(SCRIPT.with_name("session_delivery.py")), "quick"]),
        ("pre-push", [sys.executable, str(SCRIPT)]),
    ]:
        hook = hooks / name
        hook.write_text("#!/bin/sh\nexec " + shlex.join(argv) + "\n", encoding="utf-8")
        hook.chmod(0o755)
    env = _fake_tools(repository)

    def git(*args, success=True):
        result = subprocess.run(
            ["git", "-c", f"core.hooksPath={hooks}", "-c", "commit.gpgsign=false", *args],
            cwd=repository,
            env=env,
            capture_output=True,
            check=False,
        )
        if success:
            assert result.returncode == 0, result.stderr
        return result

    return git


def test_handoff_success(repository: Path, hook_git) -> None:
    """Exercise plan and implementation delivery via real commit and push events."""
    exercise_handoff_success(repository, hook_git, "scripts/example.py")


def test_handoff_rejections(repository: Path, hook_git) -> None:
    """Exercise missing, failing and skipped acceptance through the push hook."""
    exercise_handoff_rejections(repository, hook_git, "scripts/example.py")


def test_pytest_acceptance_outcomes(tmp_path: Path) -> None:
    """Every selected parameter must run and pass, including setup and teardown."""
    (tmp_path / "test_selected.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_values(n):\n    assert n > 0\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_partial(n):\n    if n == 2: pytest.skip('fixture')\n    assert n == 1\n"
        "@pytest.mark.xfail(reason='fixture')\n"
        "def test_expected():\n    assert 1 == 2\n",
        encoding="utf-8",
    )
    for name, expected in [
        ("test_values", True),
        ("test_partial", False),
        ("test_expected", False),
    ]:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT.with_name("session_delivery.py")),
                "_pytest",
                "test_selected.py::" + name,
            ],
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )
        assert (result.returncode == 0) == expected, result.stdout + result.stderr
