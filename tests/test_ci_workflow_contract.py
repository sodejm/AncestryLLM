from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github/workflows/ci.yml"
RELEASE_READINESS_PATH = ROOT / ".github/workflows/release-readiness.yml"
DEPENDENCY_REVIEW_PATH = ROOT / ".github/workflows/dependency-review.yml"
PR_LABELER_PATH = ROOT / ".github/workflows/label.yml"
PR_LABELER_CONFIG_PATH = ROOT / ".github/labeler.yml"
WORKFLOWS_DIR = ROOT / ".github/workflows"


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


def test_command_workflows_use_headless_bash_and_make_does_not_inherit_zsh() -> None:
    command_workflows = sorted(
        path for path in WORKFLOWS_DIR.glob("*.yml") if "run:" in path.read_text(encoding="utf-8")
    )

    assert command_workflows
    for path in command_workflows:
        workflow = path.read_text(encoding="utf-8")
        assert "zsh" not in workflow.lower(), path
        assert re.search(r"(?m)^defaults:\n  run:\n    shell: bash\n", workflow), path

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "SHELL := /bin/bash" in makefile


def test_ci_separates_tests_from_single_run_quality_checks() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    test_job = _job(workflow, "test")
    quality_job = _job(workflow, "quality")

    assert test_job.count("make test") == 1
    assert "ruff check" not in test_job
    assert "mypy" not in test_job
    assert "check_architecture_contracts.py" not in test_job
    assert "check_repository_safety.sh" not in test_job

    for command in ("make lint", "make typecheck"):
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
    assert ".github/actions/*)" in changes_job
    assert "paths:" not in workflow

    dependency_condition = "if: needs.changes.outputs.dependencies == 'true'"
    assert security_job.count(dependency_condition) == 4
    assert security_job.count("make dependency-audit") == 1
    assert security_job.count("make security-static") == 1
    assert "needs: [changes, lockfile]" in workflow_audit_job
    assert "if: needs.changes.outputs.workflows == 'true'" in workflow_audit_job


def test_ci_checks_lockfile_consistency_before_install_heavy_jobs() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    lockfile_job = _job(workflow, "lockfile")

    assert "name: lockfile consistency" in lockfile_job
    assert "make lock-check" in lockfile_job
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

    assert install_job.count("github.event_name == 'pull_request'") == 3
    assert "'[\"ubuntu-latest\"]'" in install_job
    assert '\'["ubuntu-latest","macos-latest","windows-latest"]\'' in install_job
    assert "'[\"3.12\"]'" in install_job
    assert '\'["3.12","3.13","3.14"]\'' in install_job
    assert "runs-on: ${{ matrix.os }}" in install_job


def test_ci_limits_pull_request_test_matrix_and_runner_parallelism() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    test_job = _job(workflow, "test")
    install_job = _job(workflow, "install-smoke")

    assert "max-parallel: ${{ fromJSON(github.event_name == 'pull_request' && '1' || '3') }}" in (
        test_job
    )
    assert (
        "python: ${{ fromJSON(github.event_name == 'pull_request' "
        '&& \'["3.12"]\' || \'["3.12","3.13","3.14"]\') }}'
    ) in test_job
    assert "max-parallel: ${{ fromJSON(github.event_name == 'pull_request' && '1' || '3') }}" in (
        install_job
    )


def test_dependency_review_runs_on_hosted_ubuntu_with_bounded_duration() -> None:
    workflow = DEPENDENCY_REVIEW_PATH.read_text(encoding="utf-8")

    assert "runs-on: ubuntu-24.04" in workflow
    assert "timeout-minutes: 15" in workflow
    assert "self-hosted" not in workflow


def test_all_applicable_workflow_jobs_use_the_local_verified_uv_action() -> None:
    expected_counts = {
        ".github/workflows/ci.yml": 6,
        ".github/workflows/release-readiness.yml": 3,
        ".github/workflows/release.yml": 3,
        ".github/workflows/desktop-sidecar.yml": 2,
        ".github/workflows/release-project-gate-proof.yml": 1,
    }

    for relative_path, expected_count in expected_counts.items():
        workflow = (ROOT / relative_path).read_text(encoding="utf-8")
        assert workflow.count("uses: ./.github/actions/setup-verified-uv") == expected_count
        assert "pip install --disable-pip-version-check uv" not in workflow
        assert "astral-sh/setup-uv@" not in workflow


def test_verified_uv_calling_jobs_grant_attestation_read_permission() -> None:
    expected_jobs = {
        ".github/workflows/ci.yml": (
            "lockfile",
            "test",
            "quality",
            "security",
            "package",
            "workflow-audit",
        ),
        ".github/workflows/release-readiness.yml": ("quality", "security", "package"),
        ".github/workflows/release.yml": (
            "build",
            "desktop-installers",
            "verify-pypi-hashes",
        ),
        ".github/workflows/desktop-sidecar.yml": ("desktop-security", "native-package"),
        ".github/workflows/release-project-gate-proof.yml": ("validate",),
    }

    for relative_path, job_names in expected_jobs.items():
        workflow = (ROOT / relative_path).read_text(encoding="utf-8")
        for job_name in job_names:
            job = _job(workflow, job_name)
            permissions = re.search(
                r"(?m)^    permissions:\n(?P<body>(?:      [^\n]+\n)+)",
                job,
            )
            assert permissions is not None, f"{relative_path}:{job_name}"
            assert re.search(
                r"(?m)^      attestations: read(?:\s+#.*)?$",
                permissions.group("body"),
            ), f"{relative_path}:{job_name}"


def test_uv_environment_and_workflow_audit_targets_are_explicit() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    ci = CI_PATH.read_text(encoding="utf-8")
    readiness = RELEASE_READINESS_PATH.read_text(encoding="utf-8")

    assert "$(UV_BIN) sync --locked --all-extras --all-groups" in makefile
    audit_command = "zizmor --persona=pedantic .github/workflows .github/actions"
    assert f"$(UV_BIN) run --locked --group security {audit_command}" in makefile
    assert "make workflow-audit" in _job(ci, "workflow-audit")
    assert "make workflow-audit" in _job(readiness, "security")


def test_setup_uv_cache_supersedes_the_three_manual_uv_caches() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")

    assert "actions/cache@" not in workflow
    assert "~/.cache/uv" not in workflow
    assert "${{ runner.os }}-uv-" not in workflow


def test_stock_pip_consumer_smoke_jobs_remain_explicit_exceptions() -> None:
    ci = CI_PATH.read_text(encoding="utf-8")
    readiness = RELEASE_READINESS_PATH.read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for job in (_job(ci, "install-smoke"), _job(ci, "sdist-smoke")):
        assert "pip install --disable-pip-version-check" in job
        assert "setup-verified-uv" not in job
    readiness_install = _job(readiness, "install")
    release_install = _job(release, "verify-pypi-install")
    for job in (readiness_install, release_install):
        assert "pip install --disable-pip-version-check" in job
        assert "setup-verified-uv" not in job


def test_git_hooks_keep_edit_loop_cheap_and_move_full_gates_to_pre_push() -> None:
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "default_stages: [pre-commit]" in hooks
    assert "entry: make pre-push" in hooks
    assert "entry: make workflow-audit" in hooks
    assert "entry: make lock-check" in hooks
    assert "files: ^(pyproject\\.toml|uv\\.lock)$" in hooks
    workflow_filter = r"^\.github/(actions|workflows)/"
    assert f"files: {workflow_filter}" in hooks
    assert re.match(workflow_filter, ".github/actions/setup-verified-uv/action.yml")
    assert re.match(workflow_filter, ".github/workflows/ci.yml")
    assert not re.match(workflow_filter, "docs/CI.md")
    assert hooks.count("stages: [pre-push]") == 2
    assert "bootstrap: setup hooks" in makefile
    assert "lock-check:" in makefile
    assert "$(UV_BIN) lock --check" in makefile
    for target in ("test", "lint", "typecheck", "security"):
        assert f"$(MAKE) {target}" in makefile
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

    assert _job(ci, "quality").count("make lint") == 1
    assert _job(readiness, "quality").count("make lint") == 1
    assert "code-docs-check:" in makefile, "Makefile must define code-docs-check target"
    assert makefile.count("check_code_documentation.py") == 2, (
        "Makefile code-docs-check target must invoke check_code_documentation.py"
    )


def test_secret_scans_use_commit_ranges_or_exact_candidate_trees() -> None:
    ci = CI_PATH.read_text(encoding="utf-8")
    readiness = RELEASE_READINESS_PATH.read_text(encoding="utf-8")
    ci_security = _job(ci, "security")
    readiness_security = _job(readiness, "security")

    assert (
        "fetch-depth: ${{ (github.event_name == 'workflow_dispatch' || "
        "github.event_name == 'schedule') && 1 || 0 }}"
    ) in ci_security
    assert "name: Scan commit range or current candidate tree for secrets" in ci_security

    assert "ref: ${{ needs.validate.outputs.commit }}" in readiness_security
    assert "fetch-depth: 1" in readiness_security
    assert "name: Scan frozen candidate tree for secrets" in readiness_security

    for security_job in (ci_security, readiness_security):
        assert "--results=verified,unknown --fail-on-scan-errors" in security_job
        assert "--exclude-detectors" not in security_job
        assert "--exclude-paths" not in security_job


def test_secret_scan_contract_is_documented_with_native_repository_controls() -> None:
    guide = (ROOT / "docs/CI.md").read_text(encoding="utf-8")
    normalized_guide = " ".join(guide.split())

    assert "current `main` candidate tree" in normalized_guide
    assert "exact frozen candidate tree" in normalized_guide
    assert "GitHub secret scanning and push protection" in normalized_guide
    assert "immutable Git history" in normalized_guide


def test_tracked_text_avoids_provider_key_shaped_identifiers() -> None:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    detector_shape = re.compile(r"\b(?:live|test)_" + r"[A-Za-z0-9_]{35}\b")
    matches: list[str] = []

    for relative_bytes in completed.stdout.split(b"\0"):
        if not relative_bytes:
            continue
        relative = relative_bytes.decode("utf-8")
        try:
            lines = (ROOT / relative).read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        matches.extend(
            f"{relative}:{line_number}"
            for line_number, line in enumerate(lines, start=1)
            if detector_shape.search(line)
        )

    assert matches == [], (
        "tracked text must not contain values shaped like provider credentials: "
        + ", ".join(matches)
    )
