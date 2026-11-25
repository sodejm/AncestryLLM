"""Validate staged handoffs and exercise their acceptance tests in the push snapshot."""

# Git and the selected test runner receive argument vectors without a shell.
# ruff: noqa: S603, S607

from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path, PurePosixPath

PLAN_DIR = "docs/session-plans/"
PLACEHOLDER = re.compile(r"\b(TBD|TODO|FIXME|TBA)\b|^\s*\.\.\.\s*$", re.IGNORECASE)
SELECTOR = re.compile(r"(?P<file>[\w./-]+\.py)::(?:(?P<cls>\w+)::)?(?P<method>test_\w+)\Z")


def git(*args):
    """Read the active index, including the alternate index used for partial commits."""
    return subprocess.check_output(["git", *args])


def quick_base():
    """Choose the branch baseline for the staged handoff check."""
    for ref in ("refs/remotes/origin/main", "refs/heads/main", "HEAD"):
        base = subprocess.run(["git", "merge-base", "HEAD", ref], capture_output=True)
        if base.returncode == 0:
            return base.stdout.decode().strip()
    return (
        subprocess.check_output(["git", "hash-object", "-t", "tree", "--stdin"], input=b"")
        .decode()
        .strip()
    )


def meaningful(value, field):
    """Require concrete, non-placeholder text in a handoff field."""
    if not isinstance(value, str) or not value.strip() or PLACEHOLDER.search(value):
        raise ValueError(f"{field} needs concrete text")
    return value


def items(value, field):
    """Require at least one record in a repeated field."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} requires a nonempty list")
    return value


def repo_path(value):
    """Validate the spelling of a repository-relative file name."""
    meaningful(value, "path")
    path = PurePosixPath(value)
    if (
        value.startswith("-")
        or path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or not path.parts
        or "\\" in value
    ):
        raise ValueError(f"expected a repository-relative path, got {value!r}")
    return value


def validate(plan):
    """Validate a handoff before accepting its claims about behavior."""
    if not isinstance(plan, dict) or plan.get("stage") not in ("plan", "implementation"):
        raise ValueError("set stage to plan or implementation")
    for field in ("request", "scope", "setup", "constraints"):
        meaningful(plan.get(field), field)
    for step in items(plan.get("steps"), "ordered steps"):
        meaningful(step.get("action"), "step.action")
        verify = step.get("verify")
        if not isinstance(verify, dict):
            raise ValueError("step.verify needs a command and expected result")
        for field in ("command", "expected"):
            meaningful(verify.get(field), f"step.verify.{field}")
        for path in items(step.get("paths"), "step.paths"):
            repo_path(path)
    kinds = set()
    selectors = set()
    for case in items(plan.get("acceptance"), "acceptance"):
        for field in ("entrypoint", "given", "when", "then"):
            meaningful(case.get(field), f"acceptance.{field}")
        for path in items(case.get("paths"), "acceptance.paths"):
            repo_path(path)
        kinds.add(case.get("kind"))
        selectors.add(case.get("test"))
        test = SELECTOR.fullmatch(meaningful(case.get("test"), "acceptance.test"))
        if not test or case.get("runner") not in ("unittest", "pytest"):
            raise ValueError(
                "name an exact unittest or pytest case as file.py::[Class::]test_method"
            )
        repo_path(test["file"])
        if case["runner"] == "unittest" and not test["cls"]:
            raise ValueError("unittest requires a TestCase class and method")
    success = {c["test"] for c in plan["acceptance"] if c["kind"] == "success"}
    boundary = {c["test"] for c in plan["acceptance"] if c["kind"] == "boundary"}
    if success & boundary or len(selectors) < 2:
        raise ValueError("name distinct success and boundary tests")
    if kinds != {"success", "boundary"}:
        raise ValueError("include success and boundary acceptance cases")
    if not isinstance(plan.get("pending"), list):
        raise ValueError("pending must list outstanding decisions or manual checks (or be [])")
    for pending in plan["pending"]:
        meaningful(pending, "pending")


def behavior(path):
    """Return whether a change requires implementation acceptance coverage."""
    p = PurePosixPath(path)
    if path.startswith(PLAN_DIR):
        return False
    document = path.startswith("docs/") or p.name in {
        "AGENTS.md",
        "README.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "LICENSE.txt",
    }
    return not (document and p.suffix.lower() in {".md", ".rst", ".txt"})


def inspect_range(base, head=None):
    """Read handoffs and their implementation paths from the exact Git trees."""
    spec = [base, head] if head else ["--cached", base]
    changes = git("diff", "--name-status", "--no-renames", "-z", *spec, "--").split(b"\0")
    changed = {
        os.fsdecode(path): status.decode()
        for status, path in zip(changes[:-1:2], changes[1::2], strict=True)
    }
    prefix = f"{head}:" if head else ":"
    # Read staged or committed blobs. Unstaged edits cannot satisfy the handoff.
    records = {}
    for path, status in changed.items():
        if path.startswith(PLAN_DIR) and status != "D":
            if not path.endswith(".json"):
                raise ValueError(f"{path}: session handoffs must be JSON files")
            try:
                plan = json.loads(git("show", prefix + path))
                validate(plan)
            except (ValueError, AttributeError, TypeError) as exc:
                raise ValueError(f"{path}: {exc}") from exc
            records[path] = plan
    legacy = [
        p for p, s in changed.items() if s != "D" and re.search(r"^docs/(?:superpowers/)?plans/", p)
    ]
    if legacy and not records:
        raise ValueError("include a handoff JSON under docs/session-plans/ for the changed plan")
    product = {p for p in changed if behavior(p)}
    implementations = [p for p in records.values() if p["stage"] == "implementation"]
    # A previously merged plan can be activated by changing its stage in this branch.
    covered = {
        path for plan in implementations for case in plan["acceptance"] for path in case["paths"]
    }
    if product - covered:
        raise ValueError(
            "missing implementation acceptance coverage for " + ", ".join(sorted(product - covered))
        )
    if head:
        for plan in implementations:
            owned = {path for case in plan["acceptance"] for path in case["paths"]}
            planned = {path for step in plan["steps"] for path in step["paths"] if behavior(path)}
            if planned - product:
                raise ValueError(
                    "missing planned step files: " + ", ".join(sorted(planned - product))
                )
            if owned - changed.keys():
                raise ValueError(
                    "missing planned changes: " + ", ".join(sorted(owned - changed.keys()))
                )
            if not product.intersection(owned):
                raise ValueError("implementation handoff has no changed implementation paths")
            if plan["pending"]:
                raise ValueError(
                    "resolve pending implementation acceptance before push: "
                    + ", ".join(plan["pending"])
                )
    return implementations


def run_case(snapshot, runner, selector):
    """Run a named test against the pushed files in a fresh process."""
    test = SELECTOR.fullmatch(selector)
    file = (snapshot / test["file"]).resolve()
    if not file.is_relative_to(snapshot) or not file.is_file():
        raise ValueError(f"missing acceptance test in push snapshot: {selector}")
    # Catch empty stubs without restricting tests that delegate to real fixture helpers.
    suite = ast.parse(file.read_bytes(), filename=str(file))
    definitions = suite.body
    if test["cls"]:
        definitions = [
            member
            for cls in definitions
            if isinstance(cls, ast.ClassDef) and cls.name == test["cls"]
            for member in cls.body
        ]
    for fn in definitions:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name != test["method"]:
            continue
        if all(
            isinstance(stmt, ast.Pass)
            or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
            or (isinstance(stmt, ast.Return) and stmt.value is None)
            or (
                isinstance(stmt, ast.Assert)
                and isinstance(stmt.test, ast.Constant)
                and stmt.test.value is True
            )
            for stmt in fn.body
        ):
            raise ValueError(f"empty acceptance test: {selector}")
    env = os.environ.copy()
    # A required test can open its own repository. It must not inherit the hook's index.
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_PREFIX",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_SHALLOW_FILE",
    ):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(map(str, (snapshot, snapshot / "src")))
    env.pop("PYTEST_ADDOPTS", None)
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "_" + runner, selector],
        cwd=snapshot,
        env=env,
        check=True,
    )


def selected_pytest(selector):
    """Require the named case to reach the call phase and pass every parameter."""
    import pytest

    class Results:
        """Collect outcomes for each selected node through pytest's reporting hooks."""

        def __init__(self):
            self.passed = set()
            self.selected = set()
            self.incomplete = False

        def pytest_collection_finish(self, session):
            """Remember the actual collected node IDs."""
            self.selected.update(item.nodeid for item in session.items)

        def pytest_runtest_logreport(self, report):
            """Reject skips and expected failures, including setup and teardown."""
            if report.skipped or hasattr(report, "wasxfail"):
                self.incomplete = True
            if report.when == "call" and report.passed:
                self.passed.add(report.nodeid)

    results = Results()
    code = pytest.main(["-o", "addopts=", selector], plugins=[results])
    if (
        code
        or results.incomplete
        or not results.selected
        or results.selected != results.passed
        or any(n != selector and not n.startswith(selector + "[") for n in results.selected)
    ):
        raise ValueError(f"required pytest case did not pass: {selector}")


def selected_unittest(selector):
    """Load the exact named unittest and require a passing call."""
    match = SELECTOR.fullmatch(selector)
    path = Path(match["file"]).resolve()
    # Resolve packages as well as standalone modules in a non-package tests directory.
    parent = path.parent
    parts = [path.stem]
    while (parent / "__init__.py").is_file():
        parts.insert(0, parent.name)
        parent = parent.parent
    sys.path.insert(0, str(parent))
    module = importlib.import_module(".".join(parts))
    if Path(module.__file__).resolve() != path:
        raise ValueError(f"imported a different module for {selector}")
    cls = getattr(module, match["cls"])
    if not issubclass(cls, unittest.TestCase):
        raise ValueError(f"expected a unittest.TestCase for {selector}")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([cls(match["method"])]))
    if (
        result.testsRun != 1
        or not result.wasSuccessful()
        or result.expectedFailures
        or result.skipped
    ):
        raise ValueError(f"required test did not pass: {selector}")


def main():
    """Run the selected handoff check and propagate acceptance failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("quick")
    commands.add_parser("plan").add_argument("path", type=Path)
    push = commands.add_parser("push")
    push.add_argument("base")
    push.add_argument("head")
    push.add_argument("--snapshot", type=Path, default=Path.cwd())
    commands.add_parser("_pytest").add_argument("selector")
    commands.add_parser("_unittest").add_argument("selector")
    args = parser.parse_args()
    if args.command in ("_unittest", "_pytest"):
        sys.path[0] = str(Path.cwd())
    if args.command == "plan":
        try:
            validate(json.loads(args.path.read_bytes()))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"{args.path}: {exc}") from exc
    elif args.command == "_unittest":
        selected_unittest(args.selector)
    elif args.command == "_pytest":
        selected_pytest(args.selector)
    elif args.command == "quick":
        inspect_range(quick_base())
    else:
        plans = inspect_range(args.base, args.head)
        tests = dict.fromkeys((c["runner"], c["test"]) for p in plans for c in p["acceptance"])
        for runner, selector in tests:
            print(f"acceptance {runner} {selector}", flush=True)
            run_case(args.snapshot.resolve(), runner, selector)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"session delivery: {error}")
