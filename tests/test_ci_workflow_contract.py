"""Enforce canonical commands and fail-closed security contracts in CI workflows."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github/workflows/ci.yml"
CODEQL_PATH = ROOT / ".github/workflows/codeql.yml"
RELEASE_READINESS_PATH = ROOT / ".github/workflows/release-readiness.yml"
RELEASE_PATH = ROOT / ".github/workflows/release.yml"
DESKTOP_SIDECAR_PATH = ROOT / ".github/workflows/desktop-sidecar.yml"
RELEASE_PROJECT_PROOF_PATH = ROOT / ".github/workflows/release-project-gate-proof.yml"
DEPENDENCY_REVIEW_PATH = ROOT / ".github/workflows/dependency-review.yml"
PR_LABELER_PATH = ROOT / ".github/workflows/label.yml"
PR_LABELER_CONFIG_PATH = ROOT / ".github/labeler.yml"
WORKFLOWS_DIR = ROOT / ".github/workflows"

GOVERNED_JOB_TIMEOUTS = {
    ".github/workflows/ci.yml": {
        "changes": 5,
        "lockfile": 15,
        "test": 20,
        "quality": 20,
        "docs-screenshots": 40,
        "security": 30,
        "package": 20,
        "install-smoke": 20,
        "sdist-smoke": 20,
        "container": 40,
        "workflow-audit": 20,
        "timeout-proof-exercise": 1,
        "timeout-proof-evidence": 5,
        "pr-gate": 5,
    },
    ".github/workflows/codeql.yml": {"analyze": 30},
    ".github/workflows/dependency-review.yml": {"dependency-review": 15},
    ".github/workflows/release-readiness.yml": {
        "validate": 10,
        "quality": 30,
        "security": 30,
        "codeql": 30,
        "package": 20,
        "install": 20,
        "evidence": 10,
    },
    ".github/workflows/desktop-sidecar.yml": {
        "changes": 5,
        "desktop-security": 30,
        "native-package": 45,
        "desktop-gate": 10,
    },
    ".github/workflows/release-project-gate-proof.yml": {"validate": 15},
    ".github/workflows/release.yml": {
        "validate": 15,
        "build": 30,
        "desktop-installers": 90,
        "desktop-installer-validation": 60,
        "desktop-release-aggregate": 30,
        "import-desktop-release-distributions": 30,
        "assemble-release-distributions": 30,
        "publish-build-provenance": 20,
        "draft-github-release": 20,
        "publish-testpypi": 20,
        "verify-testpypi": 30,
        "publish-pypi": 20,
        "verify-pypi-hashes": 30,
        "verify-pypi-install": 30,
        "verify-docs-publication": 15,
        "publish-github-release": 20,
    },
}


def _job(workflow: str, job: str) -> str:
    marker = f"\n  {job}:\n"
    assert marker in workflow
    body = workflow.split(marker, maxsplit=1)[1]
    next_job = re.search(r"(?m)^  [a-z][a-z0-9-]*:\n", body)
    return body[: next_job.start()] if next_job else body


def _job_names(workflow: str) -> set[str]:
    jobs = workflow.split("\njobs:\n", maxsplit=1)[1]
    return set(re.findall(r"(?m)^  ([a-z][a-z0-9-]*):\n", jobs))


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

    for command in ("make lint", "make typecheck", "make typecheck-ty"):
        command_line = re.compile(rf"(?m)^\s*(?:run:\s*)?{re.escape(command)}\s*$")
        assert len(command_line.findall(quality_job)) == 1


def test_ci_runs_ty_as_a_separate_nonblocking_advisory_check() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    quality_job = _job(workflow, "quality")
    readiness = RELEASE_READINESS_PATH.read_text(encoding="utf-8")
    evidence_builder = (ROOT / "scripts/create_release_evidence.py").read_text(encoding="utf-8")

    blocking_step = """      - name: Lint, type check, and repository contracts
        run: |
          make lint
          make typecheck
"""
    advisory_step = """      - name: Advisory ty evaluation
        continue-on-error: true
        run: make typecheck-ty
"""

    assert blocking_step in quality_job
    assert advisory_step in quality_job
    assert quality_job.index(blocking_step) < quality_job.index(advisory_step)
    assert "make typecheck-ty || true" not in quality_job
    assert "make typecheck-ty" not in readiness
    assert '"mypy",' in readiness
    assert '"mypy",' in evidence_builder
    assert '"type-check",' not in readiness
    assert '"type-check",' not in evidence_builder


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
    for job in (
        "test",
        "quality",
        "docs-screenshots",
        "security",
        "package",
        "container",
        "workflow-audit",
    ):
        assert "lockfile" in _job(workflow, job).split("runs-on:", maxsplit=1)[0]


def test_ci_uses_one_stable_aggregate_pull_request_gate() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    gate = _job(workflow, "pr-gate")

    assert "name: PR gate" in gate
    assert "if: ${{ always() && github.event_name == 'pull_request' }}" in gate
    for dependency in (
        "changes",
        "lockfile",
        "test",
        "quality",
        "docs-screenshots",
        "security",
        "package",
        "install-smoke",
        "sdist-smoke",
        "container",
        "workflow-audit",
    ):
        assert f"      - {dependency}\n" in gate
    assert "CHANGES_RESULT: ${{ needs.changes.result }}" in gate
    assert "DOCS_SCREENSHOTS_RESULT: ${{ needs.docs-screenshots.result }}" in gate
    assert 'require_success changes "$CHANGES_RESULT"' in gate
    assert 'require_success docs-screenshots "$DOCS_SCREENSHOTS_RESULT"' in gate
    assert 'WORKFLOW_AUDIT_RESULT" != "success"' in gate
    assert 'WORKFLOW_AUDIT_RESULT" != "skipped"' in gate


def test_ci_runs_pinned_deterministic_documentation_screenshot_drift_check() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    job = _job(workflow, "docs-screenshots")
    desktop_package = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))

    assert "runs-on: ubuntu-24.04" in job
    assert "timeout-minutes: 40" in job
    assert "LANG: en_US.UTF-8" in job
    assert "LC_ALL: en_US.UTF-8" in job
    assert "TZ: UTC" in job
    assert "ANCESTRYLLM_DOCS_SCREENSHOT_REPORT:" not in job.split("steps:\n", maxsplit=1)[0]
    assert "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0" in job
    assert 'node-version: "26.5.0"' in job
    assert "pnpm/action-setup@0977fd99725f1db4007ccb2928dbb4e90d06cc86 # v6.0.10" in job
    assert 'version: "11.9.0"' in job
    for package in (
        "locales=2.39-0ubuntu8.8",
        "xauth=1:1.1.2-1build1",
        "xvfb=2:21.1.12-1ubuntu1.6",
    ):
        assert package in job
    assert (
        'ANCESTRYLLM_DOCS_SCREENSHOT_REPORT="$RUNNER_TEMP/docs-screenshot-drift-v1.json" '
        "xvfb-run --auto-servernum make docs-screenshots-check"
    ) in job

    assert desktop_package["engines"] == {"node": "26.5.0", "pnpm": "11.9.0"}
    assert desktop_package["packageManager"] == "pnpm@11.9.0"
    assert desktop_package["devDependencies"]["electron"] == "39.8.10"
    assert desktop_package["devDependencies"]["@playwright/test"] == "1.62.0"
    assert desktop_package["devDependencies"]["playwright"] == "1.62.0"
    assert desktop_package["devDependencies"]["@fontsource/inter"] == "5.3.0"

    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1" in job
    assert "if: failure()" in job
    assert "path: ${{ runner.temp }}/docs-screenshot-drift-v1.json" in job
    assert "if-no-files-found: error" in job
    assert "retention-days: 7" in job
    assert "docs/assets" not in job
    assert ".png" not in job


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


def test_governed_ci_security_and_release_jobs_have_reviewed_timeouts() -> None:
    for relative_path, expected_timeouts in GOVERNED_JOB_TIMEOUTS.items():
        workflow = (ROOT / relative_path).read_text(encoding="utf-8")

        assert _job_names(workflow) == set(expected_timeouts), relative_path
        for job_name, expected_minutes in expected_timeouts.items():
            job = _job(workflow, job_name)
            timeout_values = re.findall(r"(?m)^    timeout-minutes: ([^\n]+)$", job)
            assert timeout_values == [str(expected_minutes)], f"{relative_path}:{job_name}"
            assert "${{" not in timeout_values[0], f"{relative_path}:{job_name}"

            steps_offset = job.find("\n    steps:\n")
            if steps_offset >= 0:
                timeout_offset = job.index(f"\n    timeout-minutes: {expected_minutes}\n")
                assert timeout_offset < steps_offset, f"{relative_path}:{job_name}"


def test_ci_timeout_proof_is_manual_deterministic_and_fail_closed() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")
    exercise = _job(workflow, "timeout-proof-exercise")
    evidence = _job(workflow, "timeout-proof-evidence")

    assert "timeout_proof:" in workflow
    assert "type: boolean" in workflow
    assert "default: false" in workflow
    proof_condition = "if: ${{ github.event_name == 'workflow_dispatch' && inputs.timeout_proof }}"
    assert proof_condition in exercise
    assert "timeout-minutes: 1" in exercise
    assert "python scripts/ci_timeout_proof.py arm" in exercise
    assert "time.sleep(300)" in exercise
    assert exercise.index("actions/upload-artifact@") < exercise.index("time.sleep(300)")

    assert "needs: timeout-proof-exercise" in evidence
    assert "if: ${{ always() && inputs.timeout_proof }}" in evidence
    assert "PROOF_RESULT: ${{ needs.timeout-proof-exercise.result }}" in evidence
    assert "python scripts/ci_timeout_proof.py confirm" in evidence
    assert "actions/download-artifact@" in evidence
    assert "actions/upload-artifact@" in evidence
    assert "CI_TIMEOUT_PROOF_EXPECTED_FAILURE" in evidence
    assert re.search(r"(?m)^\s+exit 1$", evidence)
    assert "secrets." not in exercise + evidence


def test_all_applicable_workflow_jobs_use_the_local_verified_uv_action() -> None:
    expected_counts = {
        ".github/workflows/ci.yml": 7,
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
            "docs-screenshots",
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
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    bootstrap_policy = json.loads(
        (ROOT / "config/uv-bootstrap-policy.json").read_text(encoding="utf-8")
    )

    assert "default_stages: [pre-commit]" in hooks
    assert "entry: make pre-push" in hooks
    assert "entry: make workflow-audit" in hooks
    assert "id: gitleaks" in hooks
    assert "repo: https://github.com/pre-commit/pre-commit-hooks" in hooks

    ruff_hook = """  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: 39d9ac5938dadb73df0564a45f163e25ff9fa6e2
    hooks:
      - id: ruff-check
        args: ["--no-fix"]
        files: ^(src|tests|scripts)/
      - id: ruff-format
        args: ["--check"]
        files: ^(src|tests|scripts)/
"""
    uv_hook = """  - repo: https://github.com/astral-sh/uv-pre-commit
    rev: 8ff2449591c8de025b17661ba76d60237a1ae62b
    hooks:
      - id: uv-lock
        args: ["--check"]
"""
    assert ruff_hook in hooks
    assert uv_hook in hooks
    assert "ancestryllm-lockfile-consistency" not in hooks
    assert "entry: make lock-check" not in hooks
    assert "--fix" not in ruff_hook
    assert "--unsafe-fixes" not in hooks

    lock_versions = {
        package["name"]: package["version"] for package in lock["package"] if "version" in package
    }
    assert lock_versions["ruff"] == "0.16.1"
    assert bootstrap_policy["uv"]["version"] == "0.12.1"
    assert bootstrap_policy["uv"]["release_tag"] == "0.12.1"

    workflow_filter = r"^\.github/(actions|workflows)/"
    assert f"files: {workflow_filter}" in hooks
    assert re.match(workflow_filter, ".github/actions/setup-verified-uv/action.yml")
    assert re.match(workflow_filter, ".github/workflows/ci.yml")
    assert not re.match(workflow_filter, "docs/reference/CI.md")
    assert hooks.count("stages: [pre-push]") == 2
    assert "bootstrap: setup hooks" in makefile
    assert "lock-check:" in makefile
    assert "$(UV_BIN) lock --check" in makefile
    for target in ("test", "lint", "typecheck", "security"):
        assert f"$(MAKE) {target}" in makefile
    assert "install --hook-type pre-commit --hook-type pre-push" in makefile


def test_ci_docs_preserve_the_two_phase_ruleset_migration() -> None:
    guide = (ROOT / "docs/reference/CI.md").read_text(encoding="utf-8")

    assert "### Phase A: establish the aggregate gate" in guide
    assert "### Phase B: reduce the pull-request matrix" in guide
    assert "Do not merge both commits at once" in guide
    assert "`PR gate`" in guide


def test_code_docs_check_is_required_in_ci_and_release_readiness() -> None:
    ci = CI_PATH.read_text(encoding="utf-8")
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    ci_quality = _job(ci, "quality")
    readiness_quality = _job(readiness, "quality")
    setup_node = "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020"
    setup_pnpm = "pnpm/action-setup@0977fd99725f1db4007ccb2928dbb4e90d06cc86"

    for quality_job in (ci_quality, readiness_quality):
        assert quality_job.count("make lint") == 1
        assert quality_job.count("make code-docs-check") == 1
        assert setup_node in quality_job
        assert 'node-version: "26.5.0"' in quality_job
        assert setup_pnpm in quality_job
        assert 'version: "11.9.0"' in quality_job
    assert "code-docs-check:" in makefile, "Makefile must define code-docs-check target"
    assert makefile.count("check_code_documentation.py") == 2, (
        "Makefile code-docs-check target must invoke check_code_documentation.py"
    )
    assert "pnpm --dir desktop docs:check" in makefile


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
    guide = (ROOT / "docs/reference/CI.md").read_text(encoding="utf-8")
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
        tracked_path = ROOT / relative
        if not tracked_path.is_file():
            continue
        try:
            lines = tracked_path.read_text(encoding="utf-8").splitlines()
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
