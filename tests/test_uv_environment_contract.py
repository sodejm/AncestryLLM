"""Contracts for uv-owned repository environments and canonical commands."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from scripts.check_system_python import SystemPythonError, validate_python_version

ROOT = Path(__file__).resolve().parents[1]


def _make_target(target_name: str) -> str:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    declaration = re.compile(rf"(?m)^{re.escape(target_name)}:[^\n]*\n")
    matches = list(declaration.finditer(makefile))
    assert len(matches) == 1, (target_name, len(matches))

    body = makefile[matches[0].end() :]
    next_target = re.search(r"(?m)^[A-Za-z0-9_.$()/%-]+:[^\n]*\n", body)
    return body[: next_target.start()] if next_target else body


def _workflow_job(relative_path: str, job_name: str) -> str:
    workflow = (ROOT / relative_path).read_text(encoding="utf-8")
    marker = f"\n  {job_name}:\n"
    assert marker in workflow
    body = workflow.split(marker, maxsplit=1)[1]
    next_job = re.search(r"(?m)^  [a-z][a-z0-9-]*:\n", body)
    return body[: next_job.start()] if next_job else body


def test_uv_requires_the_reviewed_version_and_never_downloads_python() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert configuration["project"]["requires-python"] == ">=3.12,<3.15"
    assert configuration["tool"]["uv"] == {
        "required-version": "==0.12.1",
        "python-preference": "only-system",
        "python-downloads": "never",
    }
    assert configuration["tool"]["pytest"]["ini_options"]["pythonpath"] == ["."]
    assert (ROOT / ".python-version").read_text(encoding="utf-8") == "3.12\n"


def test_make_uses_verified_uv_as_the_only_environment_owner() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "verified-uv: system-python" in makefile
    assert "scripts/bootstrap_uv.py bootstrap" in _make_target("verified-uv")
    assert "python -m venv" not in makefile
    assert " -m venv" not in makefile
    assert "VIRTUAL_ENV=" not in makefile
    assert "pip install" not in makefile
    assert "uvx" not in makefile
    assert "uv run --with" not in makefile

    for target_name in (
        "setup",
        "lock",
        "lock-check",
        "test",
        "lint",
        "typecheck",
        "typecheck-ty",
        "dependency-audit",
        "security-static",
        "sbom",
        "package",
        "workflow-audit",
        "code-docs-check",
        "docs-screenshots",
        "docs-screenshots-check",
        "docs-terminal-screenshots",
        "hooks",
    ):
        declaration = re.search(
            rf"(?m)^{re.escape(target_name)}:(?P<prerequisites>[^\n]*)$",
            makefile,
        )
        assert declaration is not None, target_name
        assert "verified-uv" in declaration.group("prerequisites"), target_name


def test_make_exposes_the_exact_canonical_uv_commands() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    expected_commands = {
        "setup": "$(UV_BIN) sync --locked --all-extras --all-groups",
        "lock": "$(UV_BIN) lock",
        "lock-check": "$(UV_BIN) lock --check",
        "test": "$(UV_BIN) run --locked --group test pytest --verbose",
        "typecheck": "$(UV_BIN) run --locked --group typecheck mypy src/ancestryllm",
        "typecheck-ty": ("$(UV_BIN) run --locked --group typecheck ty check src/ancestryllm"),
        "dependency-audit": (
            "$(UV_BIN) run --locked --group security python "
            "scripts/run_dependency_audit.py --uv $(UV_BIN)"
        ),
        "security-static": ("$(UV_BIN) run --locked --script scripts/run_pinned_semgrep.py ."),
        "package": (
            "$(UV_BIN) run --locked --group build python "
            "scripts/build_release.py --output-dir $(DIST_DIR)"
        ),
        "workflow-audit": (
            "$(UV_BIN) run --locked --group security zizmor --persona=pedantic "
            ".github/workflows .github/actions"
        ),
        "docs-screenshots": (
            "$(UV_BIN) run --locked --group lint python scripts/docs_screenshots.py capture "
            "--manifest config/docs-screenshot-manifest.json --repository-root . "
            '"$${selection[@]}"'
        ),
        "docs-screenshots-check": (
            "$(UV_BIN) run --locked --group lint python scripts/docs_screenshots.py check "
            "--manifest config/docs-screenshot-manifest.json --repository-root ."
        ),
        "docs-terminal-screenshots": (
            "$(UV_BIN) run --locked --group lint python scripts/docs_screenshots.py capture "
            "--manifest config/docs-screenshot-manifest.json --repository-root . "
            "--surface terminal"
        ),
        "hooks": (
            "$(UV_BIN) run --locked --group lint pre-commit install "
            "--hook-type pre-commit --hook-type pre-push"
        ),
    }

    for target_name, expected_command in expected_commands.items():
        target = _make_target(target_name)
        command_lines = [line.strip().removeprefix("@") for line in target.splitlines()]
        assert expected_command in command_lines, target_name

    assert "export PYTEST_ADDOPTS ?= --cov --cov-report=term-missing" in makefile

    lint = _make_target("lint")
    for command in (
        "$(UV_BIN) run --locked --group lint ruff check src tests scripts",
        "$(UV_BIN) run --locked --group lint ruff format --check src tests scripts",
        "$(UV_BIN) run --locked --group lint python scripts/check_architecture_contracts.py",
        "./scripts/check_repository_safety.sh",
        "$(UV_BIN) run --locked --group lint python scripts/check_code_documentation.py",
    ):
        assert command in lint


def test_system_python_preflight_has_stable_fail_closed_errors() -> None:
    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "system-python",
            "PYTHON=/definitely/missing/ancestryllm-python",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "UVENV_PYTHON_NOT_FOUND" in completed.stderr

    for supported in ((3, 12, 0), (3, 13, 9), (3, 14, 0)):
        validate_python_version(supported)

    for unsupported in ((3, 11, 9), (3, 15, 0), (4, 0, 0)):
        with pytest.raises(SystemPythonError, match="UVENV_PYTHON_VERSION_UNSUPPORTED"):
            validate_python_version(unsupported)


def test_ci_calls_make_owned_commands_after_narrow_group_syncs() -> None:
    expected_commands = {
        (".github/workflows/ci.yml", "lockfile"): ("make lock-check",),
        (".github/workflows/ci.yml", "test"): ("make test",),
        (".github/workflows/ci.yml", "quality"): (
            "make lint",
            "make typecheck",
            "make typecheck-ty",
        ),
        (".github/workflows/ci.yml", "security"): (
            "make dependency-audit",
            "make security-static",
            "make sbom",
        ),
        (".github/workflows/ci.yml", "package"): ("make package",),
        (".github/workflows/ci.yml", "workflow-audit"): ("make workflow-audit",),
        (".github/workflows/release-readiness.yml", "quality"): (
            "make test",
            "make lint",
            "make typecheck",
        ),
        (".github/workflows/release-readiness.yml", "security"): (
            "make dependency-audit",
            "make security-static",
            "make workflow-audit",
            "make sbom",
        ),
        (".github/workflows/release-readiness.yml", "package"): ("make package",),
        (".github/workflows/release.yml", "build"): (
            "make package",
            "make sbom SBOM_OUTPUT=dist/sbom.json",
        ),
    }

    for (relative_path, job_name), commands in expected_commands.items():
        job = _workflow_job(relative_path, job_name)
        for command in commands:
            command_line = re.compile(rf"(?m)^\s*(?:run:\s*)?{re.escape(command)}\s*$")
            assert len(command_line.findall(job)) == 1, (relative_path, job_name, command)

    canonical_workflows = (
        ROOT / ".github/workflows/ci.yml",
        ROOT / ".github/workflows/release-readiness.yml",
        ROOT / ".github/workflows/release.yml",
    )
    forbidden = ("uvx", "uv run --with", "pip install uv", "pip install --upgrade uv")
    for workflow_path in canonical_workflows:
        workflow = workflow_path.read_text(encoding="utf-8")
        assert all(command not in workflow for command in forbidden), workflow_path


def test_python_matrix_remains_system_supplied_and_supported() -> None:
    setup_python = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")

    assert setup_python in ci
    assert setup_python in readiness
    assert '["3.12","3.13","3.14"]' in ci
    assert 'python: ["3.12", "3.13", "3.14"]' in readiness
