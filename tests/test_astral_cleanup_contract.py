"""Contracts for the final Astral tooling, editor, and documentation cleanup."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_vscode_uses_ruff_and_authoritative_mypy_without_env_autoload() -> None:
    settings = json.loads(_read(".vscode/settings.json"))
    extensions = json.loads(_read(".vscode/extensions.json"))["recommendations"]

    assert settings["ruff.enable"] is True
    assert settings["ruff.configuration"] == "${workspaceFolder}/pyproject.toml"
    assert settings["ruff.configurationPreference"] == "filesystemFirst"
    assert settings["ruff.lint.enable"] is True
    assert settings["ruff.fixAll"] is False
    assert settings["ruff.organizeImports"] is False
    assert settings["ruff.codeAction.fixViolation.enable"] is False
    assert settings["ruff.codeAction.disableRuleComment.enable"] is False

    assert settings["mypy-type-checker.importStrategy"] == "fromEnvironment"
    assert settings["mypy-type-checker.args"] == [
        "--config-file",
        "${workspaceFolder}/pyproject.toml",
    ]
    assert settings["mypy-type-checker.reportingScope"] == "workspace"
    assert settings["[python]"]["editor.defaultFormatter"] == "charliermarsh.ruff"
    assert settings["[python]"]["editor.formatOnSave"] is True

    assert "python.terminal.useEnvFile" not in settings
    assert "python.envFile" not in settings
    assert not any(key.startswith("ty.") for key in settings)
    assert "charliermarsh.ruff" in extensions
    assert "ms-python.mypy-type-checker" in extensions


def test_installation_docs_retain_all_supported_consumer_paths() -> None:
    readme = _read("README.md")

    assert "uv tool install ancestryllm" in readme
    assert "uv tool install 'ancestryllm[all-llm]'" in readme
    assert "pipx install ancestryllm" in readme
    assert "python -m pip install ancestryllm" in readme
    assert "Desktop installation does not require Python or pipx." in readme


def test_release_version_guidance_preserves_the_multifile_contract() -> None:
    runbook = _read("docs/RELEASING.md")

    assert "uv version --short" in runbook
    assert "Do not use `uv version <version>` to perform the release bump" in runbook
    for path in (
        ".github/release-config.json",
        "pyproject.toml",
        "desktop/package.json",
    ):
        assert path in runbook


def test_cleanup_guides_record_tool_ownership_and_audit_completeness() -> None:
    contributing = _read("CONTRIBUTING.md")
    ci = _read("docs/CI.md")
    dependency_maintenance = _read("docs/DEPENDENCY_MAINTENANCE.md")
    ty_evaluation = _read("docs/TY_ADVISORY_EVALUATION.md")

    assert "charliermarsh.ruff" in contributing
    assert "ms-python.mypy-type-checker" in contributing
    assert "astral-sh/ruff-pre-commit" in ci
    assert "astral-sh/uv-pre-commit" in ci
    assert "uv export --locked --all-extras --all-groups" in dependency_maintenance
    assert "config/dependency-audit-exclusions.json" in dependency_maintenance
    assert "scripts/run_dependency_audit.py" in dependency_maintenance
    assert ".vscode/settings.json" in ty_evaluation
    assert "mypy remains the authoritative editor checker" in ty_evaluation


def test_cleanup_impact_is_recorded_in_architecture_security_and_release_notes() -> None:
    architecture = _read("ARCHITECTURE.md")
    security = _read("SECURITY.md")
    threat_model = _read("docs/THREAT_MODEL.md")
    release_notes = _read("docs/release-notes/0.6.0.md")
    security_prose = " ".join(security.split())
    release_notes_prose = " ".join(release_notes.split())

    assert "scripts/run_dependency_audit.py" in architecture
    assert "repository tooling only" in architecture
    assert "config/dependency-audit-exclusions.json" in security
    assert "all extras and dependency groups" in security_prose
    assert "Issue #312 Astral cleanup evidence" in threat_model
    assert "uv tool install ancestryllm" in release_notes
    assert "mypy editor ownership" in release_notes_prose


def test_make_exposes_dependency_audit_and_all_markdown_validation() -> None:
    makefile = _read("Makefile")

    assert "dependency-audit" in makefile.split("help:", maxsplit=1)[1].splitlines()[1]
    assert "markdown-check" in makefile.split("help:", maxsplit=1)[1].splitlines()[1]
    assert "lint: verified-uv" in makefile
    assert "python scripts/check_gfm_markdown.py" in makefile
