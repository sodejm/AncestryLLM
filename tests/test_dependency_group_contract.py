"""Contracts for purpose-specific PEP 735 development environments."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_GROUPS = {
    "lint": ["ruff>=0.15,<1", "pre-commit>=4.5,<5"],
    "typecheck": ["mypy>=1.19,<3", "types-python-dateutil>=2.9,<3", "ty==0.0.69"],
    "test": [
        "coverage[toml]>=7.12,<8",
        "pytest>=9,<10",
        "pytest-cov>=7,<8",
        "pytest-mock>=3.15,<4",
    ],
    "security": [
        "cyclonedx-bom>=7.2,<8",
        "pip-audit>=2.10,<3",
        "zizmor==1.29.0",
    ],
    "build": [
        "build>=1.3,<2",
        "check-wheel-contents>=0.6.1,<1",
        "packaging>=25,<27",
        "setuptools>=83,<84",
        "twine>=6.2,<8",
        "uv_build>=0.12.0,<0.13",
        "wheel>=0.45,<1",
    ],
    "release-verifier": ["pypi-attestations==0.0.30"],
}

EXPECTED_EXTRAS = {
    "ollama": ["ollama>=0.6,<1"],
    "openai": ["openai>=2.45,<3"],
    "anthropic": ["anthropic>=0.71,<1"],
    "gemini": ["google-genai>=2.12,<3"],
    "openrouter": ["openai>=2.45,<3"],
    "all-llm": [
        "anthropic>=0.71,<1",
        "google-genai>=2.12,<3",
        "ollama>=0.6,<1",
        "openai>=2.45,<3",
    ],
    "desktop-build": ["pyinstaller>=6.17,<7"],
}

OLD_DEV_DEPENDENCIES = {
    "build>=1.3,<2",
    "check-wheel-contents>=0.6.1,<1",
    "click>=8.3.3,<9",
    "cyclonedx-bom>=7.2,<8",
    "coverage[toml]>=7.12,<8",
    "mypy>=1.19,<3",
    "packaging>=25,<27",
    "pip-audit>=2.10,<3",
    "pre-commit>=4.5,<5",
    "pyinstaller>=6.17,<7",
    "pytest>=9,<10",
    "pytest-cov>=7,<8",
    "pytest-mock>=3.15,<4",
    "ruff>=0.15,<1",
    "setuptools>=83,<84",
    "twine>=6.2,<8",
    "types-python-dateutil>=2.9,<3",
    "uv==0.12.1",
    "wheel>=0.45,<1",
    "zizmor==1.29.0",
}
NEW_ADVISORY_DEPENDENCIES = {"ty==0.0.69"}
NEW_BUILD_EVALUATION_DEPENDENCIES = {"uv_build>=0.12.0,<0.13"}


def _project() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _workflow_job(relative_path: str, job_name: str) -> str:
    workflow = (ROOT / relative_path).read_text(encoding="utf-8")
    marker = f"\n  {job_name}:\n"
    assert marker in workflow
    body = workflow.split(marker, maxsplit=1)[1]
    next_job = re.search(r"(?m)^  [a-z][a-z0-9-]*:\n", body)
    return body[: next_job.start()] if next_job else body


def _make_target(target_name: str) -> str:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    declaration = re.compile(rf"(?m)^{re.escape(target_name)}:[^\n]*\n")
    matches = list(declaration.finditer(makefile))
    assert len(matches) == 1, (target_name, len(matches))

    body = makefile[matches[0].end() :]
    next_target = re.search(r"(?m)^[A-Za-z0-9_.$()/%-]+:[^\n]*\n", body)
    return body[: next_target.start()] if next_target else body


def test_dev_extra_is_replaced_by_exact_purpose_specific_groups() -> None:
    project = _project()

    assert project["dependency-groups"] == EXPECTED_GROUPS
    assert project["project"]["optional-dependencies"] == EXPECTED_EXTRAS


def test_every_old_dev_dependency_has_one_deliberate_destination() -> None:
    project = _project()
    groups = project["dependency-groups"]
    extras = project["project"]["optional-dependencies"]

    moved = {
        dependency
        for name in ("lint", "typecheck", "test", "security", "build")
        for dependency in groups[name]
    }
    deliberately_removed = {"click>=8.3.3,<9", "uv==0.12.1"}
    retained_as_extra = set(extras["desktop-build"])

    assert moved | deliberately_removed | retained_as_extra == (
        OLD_DEV_DEPENDENCIES | NEW_ADVISORY_DEPENDENCIES | NEW_BUILD_EVALUATION_DEPENDENCIES
    )
    assert moved >= NEW_ADVISORY_DEPENDENCIES
    assert moved >= NEW_BUILD_EVALUATION_DEPENDENCIES
    assert moved.isdisjoint(deliberately_removed | retained_as_extra)
    assert deliberately_removed.isdisjoint(retained_as_extra)


def test_click_has_no_source_import_or_entry_point_consumer() -> None:
    project = _project()
    assert project["project"]["scripts"] == {"ancestry": "ancestryllm.cli:main"}

    importing_files: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            (isinstance(node, ast.Import) and any(alias.name == "click" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "click")
            for node in ast.walk(tree)
        ):
            importing_files.append(path.relative_to(ROOT).as_posix())

    assert importing_files == []


def test_typecheck_profile_treats_provider_sdks_as_optional_imports() -> None:
    project = _project()

    assert project["tool"]["mypy"]["overrides"] == [
        {
            "module": [
                "anthropic",
                "gedcom.*",
                "google",
                "google.*",
                "jsonschema",
                "ollama",
                "openai",
                "sqlcipher3",
            ],
            "ignore_missing_imports": True,
        }
    ]


def test_make_profiles_select_only_their_declared_groups() -> None:
    profiles = {
        "test": {"test"},
        "lint": {"lint"},
        "typecheck": {"typecheck"},
        "typecheck-ty": {"typecheck"},
        "dependency-audit": {"security"},
        "security-static": set(),
        "sbom": {"security"},
        "package": {"build"},
        "evaluate-uv-build": {"build"},
        "workflow-audit": {"security"},
        "code-docs-check": {"lint"},
        "hooks": {"lint"},
    }

    for target_name, expected_groups in profiles.items():
        target = _make_target(target_name)
        assert set(re.findall(r"--group ([a-z-]+)", target)) == expected_groups
        assert "--all-extras" not in target
        assert "--all-groups" not in target

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    setup = _make_target("setup")

    assert "$(UV_BIN) sync --locked --all-extras --all-groups" in setup
    assert "--group release-verifier" not in makefile
    assert "--extra dev" not in makefile
    assert makefile.count("--all-groups") == 1


def test_workflows_install_only_the_groups_required_by_each_job() -> None:
    profiles = {
        (".github/workflows/ci.yml", "test"): (
            "uv sync --locked --no-default-groups --extra all-llm --group test",
        ),
        (".github/workflows/ci.yml", "quality"): (
            "uv sync --locked --no-default-groups --group lint --group typecheck",
        ),
        (".github/workflows/ci.yml", "security"): (
            "uv sync --locked --no-default-groups --group security",
        ),
        (".github/workflows/ci.yml", "package"): (
            "uv sync --locked --no-default-groups --group build",
        ),
        (".github/workflows/ci.yml", "workflow-audit"): (
            "uv sync --locked --no-default-groups --group security",
        ),
        (".github/workflows/release-readiness.yml", "quality"): (
            "uv sync --locked --no-default-groups --extra all-llm --group test",
            "uv sync --locked --no-default-groups --group lint --group typecheck",
        ),
        (".github/workflows/release-readiness.yml", "security"): (
            "uv sync --locked --no-default-groups --group security",
        ),
        (".github/workflows/release-readiness.yml", "package"): (
            "uv sync --locked --no-default-groups --group build",
        ),
        (".github/workflows/desktop-sidecar.yml", "desktop-security"): (
            "uv sync --locked --no-default-groups --group test",
        ),
        (".github/workflows/desktop-sidecar.yml", "native-package"): (
            "uv sync --locked --no-default-groups --extra desktop-build --no-install-project --no-build",
        ),
        (".github/workflows/release-project-gate-proof.yml", "validate"): (
            "uv sync --locked --no-default-groups --group test",
        ),
        (".github/workflows/release.yml", "build"): (
            "uv sync --locked --no-default-groups --group build --group security",
        ),
        (".github/workflows/release.yml", "desktop-installers"): (
            "uv sync --locked --no-default-groups --extra desktop-build --no-install-project --no-build",
        ),
        (".github/workflows/release.yml", "verify-pypi-hashes"): (
            "uv sync --locked --no-default-groups --group release-verifier",
        ),
    }

    for (relative_path, job_name), expected_profiles in profiles.items():
        job = _workflow_job(relative_path, job_name)
        sync_lines = [
            line.strip().removeprefix("run: ")
            for line in job.splitlines()
            if "uv sync --locked" in line
        ]
        assert sync_lines == list(expected_profiles), (relative_path, job_name, sync_lines)


def test_every_workflow_sync_disables_implicit_groups() -> None:
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        sync_lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if "uv sync --locked" in line
        ]
        assert all("--no-default-groups" in line for line in sync_lines), path
        assert all("--extra dev" not in line for line in sync_lines), path


def test_lockfile_job_checks_without_installing_any_group() -> None:
    lockfile = _workflow_job(".github/workflows/ci.yml", "lockfile")

    assert "make lock-check" in lockfile
    assert "uv sync" not in lockfile
    assert "--group" not in lockfile
