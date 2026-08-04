from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github/workflows/ci.yml"
PR_LABELER_PATH = ROOT / ".github/workflows/label.yml"
PR_LABELER_CONFIG_PATH = ROOT / ".github/labeler.yml"


def _job(workflow: str, job: str) -> str:
    marker = f"\n  {job}:\n"
    assert marker in workflow
    body = workflow.split(marker, maxsplit=1)[1]
    next_job = re.search(r"(?m)^  [a-z][a-z0-9-]*:\n", body)
    return body[: next_job.start()] if next_job else body


def test_pull_request_labeler_has_the_config_file_it_loads() -> None:
    workflow = PR_LABELER_PATH.read_text(encoding="utf-8")

    assert "configuration-path: .github/labeler.yml" in workflow
    assert PR_LABELER_CONFIG_PATH.is_file()

    config = PR_LABELER_CONFIG_PATH.read_text(encoding="utf-8")
    for repository_label in ("documentation:", "testing:", "contracts:"):
        assert repository_label in config
    assert "any-glob-to-any-file:" in config


def test_ci_separates_tests_from_single_run_quality_checks() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    test_job = _job(workflow, "test")
    quality_job = _job(workflow, "quality")

    assert test_job.count("uv run python -m pytest --verbose --cov --cov-report=term-missing") == 1
    assert "ruff check" not in test_job
    assert "mypy" not in test_job
    assert "check_architecture_contracts.py" not in test_job
    assert "check_repository_safety.sh" not in test_job

    for command in (
        "uv run ruff check src tests scripts",
        "uv run ruff format --check src tests scripts",
        "uv run mypy src/ancestryllm",
        "uv run python scripts/check_architecture_contracts.py",
        "./scripts/check_repository_safety.sh",
        "uv run python scripts/check_code_documentation.py",
    ):
        assert quality_job.count(command) == 1


def test_ci_scopes_dependency_and_workflow_checks_without_skipping_required_workflow() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    changes_job = _job(workflow, "changes")
    security_job = _job(workflow, "security")
    workflow_audit_job = _job(workflow, "workflow-audit")

    assert "\n  workflow_dispatch:\n" in workflow
    assert "\n  schedule:\n" in workflow
    assert "pyproject.toml|uv.lock)" in changes_job
    assert ".github/workflows/*)" in changes_job
    assert "paths:" not in workflow

    dependency_condition = "if: needs.changes.outputs.dependencies == 'true'"
    assert security_job.count(dependency_condition) == 4
    assert security_job.count("uv run pip-audit") == 1
    assert security_job.count("uv run --locked --script scripts/run_pinned_semgrep.py .") == 1
    assert "needs: [changes, lockfile]" in workflow_audit_job
    assert "if: needs.changes.outputs.workflows == 'true'" in workflow_audit_job


def test_ci_checks_lockfile_consistency_before_install_heavy_jobs() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    lockfile_job = _job(workflow, "lockfile")

    assert "name: lockfile consistency" in lockfile_job
    assert "uv lock --check" in lockfile_job
    for job in ("test", "quality", "security", "package", "workflow-audit"):
        assert "lockfile" in _job(workflow, job).split("runs-on:", maxsplit=1)[0]


def test_ci_uses_one_stable_aggregate_pull_request_gate() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    gate = _job(workflow, "pr-gate")

    assert "name: PR gate" in gate
    assert "if: ${{ always() && github.event_name == 'pull_request' }}" in gate
    for dependency in (
        "lockfile",
        "test",
        "quality",
        "security",
        "package",
        "install-smoke",
        "sdist-smoke",
        "workflow-audit",
    ):
        assert f"      - {dependency}\n" in gate
    assert 'WORKFLOW_AUDIT_RESULT" != "success"' in gate
    assert 'WORKFLOW_AUDIT_RESULT" != "skipped"' in gate


def test_ci_limits_pull_request_install_smoke_without_reducing_full_runs() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    install_job = _job(workflow, "install-smoke")

    assert install_job.count("github.event_name == 'pull_request'") == 2
    assert "'[\"ubuntu-latest\"]'" in install_job
    assert '\'["ubuntu-latest","macos-latest","windows-latest"]\'' in install_job
    assert "'[\"3.12\"]'" in install_job
    assert '\'["3.12","3.13","3.14"]\'' in install_job
    assert "runs-on: ${{ matrix.os }}" in install_job


def test_ci_pins_uv_bootstrap_version() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    installed_versions = re.findall(
        r"python -m pip install --disable-pip-version-check (uv\S*)",
        workflow,
    )

    assert installed_versions
    assert set(installed_versions) == {"uv==0.12.0"}


def test_git_hooks_keep_edit_loop_cheap_and_move_full_gates_to_pre_push() -> None:
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "default_stages: [pre-commit]" in hooks
    assert "entry: make pre-push" in hooks
    assert "entry: make workflow-audit" in hooks
    assert "entry: make lock-check" in hooks
    assert "files: ^(pyproject\\.toml|uv\\.lock)$" in hooks
    assert hooks.count("stages: [pre-push]") == 2
    assert "bootstrap: setup hooks" in makefile
    assert "lock-check:" in makefile
    assert "-m uv lock --check" in makefile
    assert "pre-push: test lint typecheck security" in makefile
    assert "install --hook-type pre-commit --hook-type pre-push" in makefile


def test_ci_docs_preserve_the_two_phase_ruleset_migration() -> None:
    guide = (ROOT / "docs/CI.md").read_text(encoding="utf-8")

    assert "### Phase A: establish the aggregate gate" in guide
    assert "### Phase B: reduce the pull-request matrix" in guide
    assert "Do not merge both commits at once" in guide
    assert "`PR gate`" in guide


def test_code_docs_check_is_required_in_ci_and_release_readiness() -> None:
    ci = CI_PATH.read_text(encoding="utf-8")
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    command = "uv run python scripts/check_code_documentation.py"
    assert ci.count(command) == 1, "CI quality job must run check_code_documentation.py exactly once"
    assert readiness.count(command) == 1, (
        "release-readiness quality gates must run check_code_documentation.py exactly once"
    )
    assert "code-docs-check:" in makefile, "Makefile must define code-docs-check target"
    assert "check_code_documentation.py" in makefile, (
        "Makefile code-docs-check target must invoke check_code_documentation.py"
    )
