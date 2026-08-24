"""Enforce release workflow, artifact, and supported-platform contracts."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import ancestryllm
from ancestryllm.cli import main

ROOT = Path(__file__).resolve().parents[1]
STABLE_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _project() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def _release_configuration() -> dict:
    return json.loads((ROOT / ".github/release-config.json").read_text(encoding="utf-8"))


def _literal_string_set(path: Path, assignment_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == assignment_name for target in node.targets
        ):
            continue
        value = ast.literal_eval(node.value)
        assert isinstance(value, set)
        assert all(isinstance(item, str) for item in value)
        return value
    raise AssertionError(f"{assignment_name} was not defined in {path}")


def _jobs_with_permission(workflow: str, permission: str) -> set[str]:
    jobs: set[str] = set()
    matches = list(re.finditer(r"(?m)^  ([a-z][a-z0-9-]*):\n", workflow))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(workflow)
        if permission in workflow[match.end() : end]:
            jobs.add(match.group(1))
    return jobs


def test_package_version_is_one_stable_semver_value() -> None:
    version = str(_project()["version"])

    assert STABLE_SEMVER.fullmatch(version)
    assert ancestryllm.__version__ == version
    assert f'version = "{version}"' not in (ROOT / "src/ancestryllm/__init__.py").read_text(
        encoding="utf-8"
    )


def test_desktop_mock_bridge_displays_the_release_development_identity() -> None:
    version = str(_project()["version"])
    fixtures = (ROOT / "desktop/src/mock-bridge/fixtures.ts").read_text(encoding="utf-8")
    shell_e2e = (ROOT / "desktop/e2e/shell.wdio.ts").read_text(encoding="utf-8")

    assert f"appVersion: '{version}-dev'" in fixtures
    assert f"      '{version}-dev'," in shell_e2e


def test_release_configuration_names_the_project_native_v0_6_control_plane() -> None:
    configuration = _release_configuration()

    assert configuration == {
        "schema_version": 2,
        "release": "0.6.0",
        "project": {
            "owner": "sodejm",
            "number": 2,
            "title": "AncestryLLM Feature Releases",
            "iteration": "v0.6.0 — Usable desktop core",
            "priority": "P0",
            "status": "Done",
            "validation": "Verified",
        },
    }


@pytest.mark.parametrize("arguments", (["--version"],))
def test_global_version_bypasses_application_initialization(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "ancestryllm.core.context.AppContext.build",
        lambda *_args, **_kwargs: pytest.fail("version output must not build application state"),
    )

    with pytest.raises(SystemExit) as raised:
        main(arguments)

    assert raised.value.code == 0
    assert capsys.readouterr().out.strip() == f"ancestry {_project()['version']}"


def test_module_entry_point_reports_the_same_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "ancestryllm", "--version"],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == f"ancestry {_project()['version']}"
    assert completed.stderr == ""


def test_release_docs_and_manifest_define_immutable_cli_distribution() -> None:
    version = str(_project()["version"])
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    versioning = (ROOT / "docs/reference/VERSIONING.md").read_text(encoding="utf-8")
    releasing = (ROOT / "docs/RELEASING.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release_notes = ROOT / "docs/release-notes" / f"{version}.md"
    normalized_releasing = " ".join(releasing.split())
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "pipx install ancestryllm" in readme
    assert f"implemented product surfaces in {version}" in readme
    assert "Semantic Versioning 2.0.0" in versioning
    assert f"package version `{version}` is tagged `v{version}`" in versioning
    assert "must never be replaced, moved, deleted, or reused" in versioning
    assert "OIDC Trusted Publishing" in releasing
    assert "`.github/release-config.json`" in releasing
    assert "#133" not in releasing
    assert "git branch -d" in releasing
    assert "branch/worktree lifecycle audit input" in releasing
    assert "docs/release-notes/<version>.md" in releasing
    assert "PyPI: unavailable" in releasing
    assert "GitHub Project 2" in releasing
    assert "Release iteration" in releasing
    assert "Schema 3" in releasing
    assert "`project.priorities`" in releasing
    assert "exactly the same issues" in releasing
    assert "active v0.6.0 configuration remains on schema 2" in normalized_releasing
    assert "ANCESTRYLLM_PROJECT_READ_TOKEN" in releasing
    assert "read:project" in releasing
    assert "fork or Dependabot pull request cannot receive the secret" in releasing
    assert (
        "without claiming that the in-development iteration is ready to release"
        in normalized_releasing
    )
    assert (
        "`Release readiness` and the tag workflow continue to use the strict live gate"
        in normalized_releasing
    )
    assert "v0.6.0 — Usable desktop core" in releasing
    assert "P0 is reserved for work that must complete before publication" in releasing
    assert "verifier has no issue-number exception" in releasing
    assert "macOS 15/26" in releasing
    assert "Windows 11" in releasing
    assert "Ubuntu 24.04" in releasing
    assert "`sodejm` as the required reviewer" in releasing
    assert "self-approval must remain permitted" in releasing
    assert "Hosted control verification checklist" in releasing
    assert "unique commits" in releasing
    assert "squash merge can leave graph-unique branch commits" in releasing
    assert "not deletion of preserved history" in releasing
    assert "resolved before merge" in releasing
    assert "explicitly approves a GitHub-only release" in releasing
    assert "API-token fallback" in releasing
    assert "required production approval" in releasing
    assert "independent production approval" not in releasing
    assert "every release asset except the checksum file itself" in releasing
    assert "verifies only the exact TestPyPI artifact hashes" in normalized_releasing
    assert "After production PyPI publishing" in releasing
    assert "TestPyPI hashes and install smoke tests" not in releasing
    assert f"## [{version}] -" in changelog
    assert f"[Unreleased]: https://github.com/sodejm/AncestryLLM/compare/v{version}...HEAD" in (
        changelog
    )
    assert release_notes.is_file()
    assert release_notes.read_text(encoding="utf-8").startswith(f"# AncestryLLM {version}\n")
    assert (ROOT / "docs/release-notes/0.2.0.md").is_file()
    assert (ROOT / "docs/release-evidence/0.2.0/findings.json").is_file()
    assert (ROOT / "docs/release-evidence/0.2.0/interoperability.json").is_file()
    assert "prune tests" in manifest
    assert "prune family_trees" in manifest
    assert "include docs/reference/FILE_INGRESS.md" in manifest


def test_readme_orients_new_readers_to_the_released_product_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    prose = " ".join(readme.split())

    assert readme.startswith("# AncestryLLM\n")
    assert "people researching family history" in prose
    assert "Python 3.12 through 3.14" in prose
    assert "working OS credential store" in prose
    assert "headless/CI environment-injection fallback" in prose
    assert "ancestry --json database diagnose" in prose
    assert "### Use the CLI or interactive prompt" in readme
    assert "pipx install ancestryllm" in readme
    assert "pipx install 'ancestryllm[all-llm]'" in readme
    assert "### Use the desktop control shell" in readme
    assert "Desktop installation does not require Python or pipx" in prose
    assert "interactive prompt" in prose
    assert "Home, Diagnostics, Settings, and capability onboarding" in prose
    assert "not a desktop genealogy application" in prose
    assert "packages separately labeled provider and consent configuration" in prose
    assert "Tasks and Chat source-level surfaces" in prose
    assert "remain unsupported until their named target-matched gates pass" in prose
    assert (
        "does not include desktop genealogy or domain routes, files, jobs, providers" not in prose
    )
    assert "Desktop genealogy workflows are not available yet" in prose
    assert "target-matched full installer and `SHA256SUMS`" in prose
    assert "declared `binarySigningMode`" in prose
    assert "Provider `none` is network-free" in prose
    assert "explicit provider selection and your consent" in prose
    assert "OS keyring" in prose
    links = set(re.findall(r"\]\((https://[^)]+)\)", readme))
    assert {
        "https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/CLI.md",
        "https://github.com/sodejm/AncestryLLM/blob/main/docs/CONSOLE.md",
        "https://github.com/sodejm/AncestryLLM/blob/main/docs/SETUP_DIAGNOSTICS.md",
        "https://github.com/sodejm/AncestryLLM/blob/main/docs/explanation/DESKTOP_SHELL.md",
        "https://github.com/sodejm/AncestryLLM/blob/main/docs/explanation/PRIVACY_AND_CONSENT.md",
        "https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/PROVIDERS.md",
        "https://github.com/sodejm/AncestryLLM/blob/main/CONTRIBUTING.md",
    } <= links
    assert "(docs/" not in readme
    assert "](CONTRIBUTING.md)" not in readme


def test_release_sdist_closes_shipped_cli_document_links() -> None:
    manifest_includes = {
        line.removeprefix("include ").strip()
        for line in (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
        if line.startswith("include ")
    }
    build_script = ROOT / "scripts/build_release.py"
    allowed = _literal_string_set(build_script, "ALLOWED_SDIST_FILES")
    required = _literal_string_set(build_script, "REQUIRED_SDIST_PATHS")
    shipped_cli_docs = {
        "docs/reference/CLI.md",
        "docs/CONSOLE.md",
        "docs/reference/FILE_INGRESS.md",
        "docs/reference/GEDCOM_COMPATIBILITY.md",
        "docs/reference/PROVIDERS.md",
    }

    pending = list(shipped_cli_docs)
    closed_docs: set[str] = set()
    while pending:
        document = pending.pop()
        if document in closed_docs:
            continue
        document_path = ROOT / document
        assert document_path.is_file()
        closed_docs.add(document)
        for target in re.findall(
            r"\]\((?![a-z]+://|#)([^)#?]+\.md)(?:#[^)]+)?\)",
            document_path.read_text(encoding="utf-8"),
        ):
            linked = (document_path.parent / target).resolve()
            assert linked.is_relative_to(ROOT)
            relative = linked.relative_to(ROOT).as_posix()
            assert linked.is_file()
            pending.append(relative)

    assert "docs/release-evidence/issue-10-import-smoke-tests.md" in closed_docs
    assert closed_docs <= manifest_includes
    assert closed_docs <= allowed
    assert closed_docs <= required


def test_release_workflows_bind_exact_evidence_notes_and_full_checksums() -> None:
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    codeql = (ROOT / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    readiness_codeql = readiness.split("\n  codeql:\n", maxsplit=1)[1].split(
        "\n  package:\n",
        maxsplit=1,
    )[0]

    assert (
        "Confirm lifecycle audit and every safe branch/worktree cleanup are recorded" in readiness
    )
    assert "Confirm merged release branches and worktrees were safely removed" not in readiness
    assert "--gates evidence/gates.json" in readiness
    assert "approved/gates.json" in release
    assert "cmp dist/SHA256SUMS approved/artifacts/SHA256SUMS" in release
    assert "--gates dist/gates.json" in release
    assert "generate_release_checksums.py --directory dist" in release
    assert "--notes-file dist/release-notes.md" in release
    assert "subject-path: dist/*" in release
    assert "verify_codeql_sarif.py --directory codeql-sarif" in readiness
    assert (
        "needs: [validate, assemble-release-distributions, publish-build-provenance, "
        "draft-github-release]" in release
    )
    assert "import-desktop-release-distributions:" in release
    assert "Desktop-Release-Artifact-ID:" in release
    assert "Desktop-Release-Artifact-Digest:" in release
    assert "verified-pypi-distributions" in release
    assert "verified-pypi-attestations" in release
    assert "artifact: [wheel, sdist]" in release
    assert "verify_release_assets.py" in release

    assert "verify_pypi_attestations.py" in release
    assert "pypi-attestations==0.0.30" in release
    assert "uv sync --locked --no-default-groups --group release-verifier" in release
    assert "uv sync --locked --extra dev" not in release
    assert (
        "uv run --locked --no-default-groups --group release-verifier "
        "python scripts/verify_pypi_attestations.py"
    ) in release
    assert "uvx" not in release
    assert "security-events: read" in readiness_codeql
    assert "upload-database: false" in readiness_codeql
    assert "if: ${{ always() }}" in readiness_codeql
    assert "upload: always" in codeql
    assert "upload: never" not in codeql
    assert release.count("uses: ./.github/actions/setup-verified-uv") == 3
    assert readiness.count("uses: ./.github/actions/setup-verified-uv") == 3
    assert "pip install --disable-pip-version-check uv" not in release
    assert "pip install --disable-pip-version-check uv" not in readiness
    assert release.count("attestations: true") == 1
    assert release.count("attestations: false") == 1
    assert "--actual downloaded" in release
    assert "--json isDraft,name,body" in release
    assert "--clobber" not in release


def test_release_build_emits_and_retains_one_exact_head_quality_approval() -> None:
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert release.count("scripts/verify_release_quality.py") >= 2
    assert "--policy config/release-quality-policy-v1.json" in release
    assert "--desktop-evidence approved-desktop/desktop-evidence.json" in release
    assert "--output dist/release-quality-approval.json" in release
    assert "release-quality-approval.json" in release
    assert '(python_dir, {"SHA256SUMS", "release-evidence.md"}),' in release
    assert (
        '(python_dir, {"SHA256SUMS", "release-evidence.md", '
        '"release-quality-approval.json"}),' not in release
    )


def test_security_gates_use_lockfile_semgrep_and_content_pinned_rules() -> None:
    project = _project()
    project_configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    script = (ROOT / "scripts/run_pinned_semgrep.py").read_text(encoding="utf-8")
    script_lock = ROOT / "scripts/run_pinned_semgrep.py.lock"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert script_lock.is_file()
    lock = tomllib.loads(script_lock.read_text(encoding="utf-8"))
    locked_semgrep = [package for package in lock["package"] if package.get("name") == "semgrep"]
    sources = {
        ".github/workflows/ci.yml": "make security-static",
        ".github/workflows/release-readiness.yml": "make security-static",
        "Makefile": "$(UV_BIN) run --locked --script scripts/run_pinned_semgrep.py .",
    }
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert '#     "semgrep==1.170.0",' in script
    assert [package["version"] for package in locked_semgrep] == ["1.170.0"]
    assert all(
        (match := re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", dependency)) is None
        or match.group(1).lower().replace("_", "-") != "uv"
        for dependencies in project_configuration["dependency-groups"].values()
        for dependency in dependencies
    )
    assert "dev" not in project["optional-dependencies"]
    assert "pip install --upgrade pip uv==0.12.1" not in makefile
    assert "scripts/bootstrap_uv.py bootstrap" in makefile
    for relative_path, expected_command in sources.items():
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert content.count(expected_command) == 1
        assert "uvx semgrep" not in content
        assert "--config p/python" not in content
        assert "--config p/secrets" not in content
    assert "make security-static" not in release


def test_synthetic_credentialed_url_fixtures_do_not_target_live_services() -> None:
    """Keep secret verification from probing public hosts with fake credentials."""

    credentialed_public_url = re.compile(r"https://[^\s\"']+:[^@\s\"']{3,}@")
    for relative_path in (
        "desktop/src/main/external-links.test.ts",
        "tests/test_verify_pypi_attestations.py",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert credentialed_public_url.search(content) is None, relative_path


def test_workflows_invoke_the_make_owned_test_command() -> None:
    """Keep the test flags identical between local and hosted environments."""

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    command = "make test"
    for relative_path in (
        ".github/workflows/ci.yml",
        ".github/workflows/release-readiness.yml",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert content.count(command) == 1
        assert "uv run --no-sync python -m pytest" not in content
    assert "$(UV_BIN) run --locked --group test pytest --verbose" in makefile
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert command not in release


def test_tag_release_reuses_approved_quality_and_security_evidence() -> None:
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    duplicate_gates = (
        "uv run python -m pytest",
        "uv run ruff check",
        "uv run ruff format",
        "uv run mypy",
        "scripts/check_architecture_contracts.py",
        "scripts/check_repository_safety.sh",
        "uv run pip-audit",
        "scripts/run_pinned_semgrep.py",
    )

    assert "Rebuild deterministic artifacts and SBOM" in release
    assert "make package" in release
    assert "make sbom SBOM_OUTPUT=dist/sbom.json" in release
    assert "cmp dist/SHA256SUMS approved/artifacts/SHA256SUMS" in release
    for duplicate_gate in duplicate_gates:
        assert duplicate_gate not in release


def test_release_workflows_enforce_project_native_gate_and_paginate() -> None:
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    proof = (ROOT / ".github/workflows/release-project-gate-proof.yml").read_text(encoding="utf-8")
    project_query = (ROOT / "config/release-project-query-v1.graphql").read_text(encoding="utf-8")

    for workflow in (readiness, release, proof):
        assert "verify_release_configuration.py" in workflow
        assert "--config .github/release-config.json" in workflow
        assert "verify_release_project.py" in workflow
        assert 'project_query="$(< config/release-project-query-v1.graphql)"' in workflow
        assert '--project-owner "$project_owner"' in workflow
        assert "(.project.priorities // [.project.priority])[]" in workflow
        assert 'project_priority_args+=(--priority "$project_priority")' in workflow
        assert '"${project_priority_args[@]}"' in workflow
        assert ".project.milestone.number // 1" in workflow
        assert 'project_include_milestone="false"' in workflow
        assert 'project_include_milestone="true"' in workflow
        assert '-f repositoryOwner="$GITHUB_REPOSITORY_OWNER"' in workflow
        assert '-f repository="$project_repository"' in workflow
        assert '-F milestoneNumber="$project_milestone_number"' in workflow
        assert '-F includeMilestone="$project_include_milestone"' in workflow
        assert '"${project_milestone_args[@]}"' in workflow
        assert "--paginate --slurp" in workflow
        assert "verify_release_milestone.py" not in workflow
        assert "/milestones/" not in workflow
        assert "release-tracker" not in workflow
        assert "$version CLI" not in workflow
        assert "$EXPECTED_VERSION CLI" not in workflow

    assert "projectV2(number: $number)" in project_query
    assert "blockedBy(first: 100)" in project_query
    assert "$repositoryOwner: String!" in project_query
    assert "repository(owner: $repositoryOwner, name: $repository)" in project_query
    assert "@include(if: $includeMilestone)" in project_query
    assert "milestone(number: $milestoneNumber)" in project_query
    assert "issues(first: 100)" in project_query
    assert "pullRequests(first: 100)" in project_query


def test_release_project_queries_require_a_dedicated_read_token_and_safe_hosted_proof() -> None:
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    proof = (ROOT / ".github/workflows/release-project-gate-proof.yml").read_text(encoding="utf-8")

    for workflow in (readiness, release, proof):
        assert "PROJECT_READ_TOKEN: ${{ secrets.ANCESTRYLLM_PROJECT_READ_TOKEN }}" in workflow
        assert 'test -n "$PROJECT_READ_TOKEN"' in workflow
        assert 'GH_TOKEN="$PROJECT_READ_TOKEN" gh api graphql --paginate --slurp' in workflow
        assert "GH_TOKEN: ${{ github.token }}" in workflow
        assert "verify_release_project.py" in workflow

    for workflow in (readiness, release):
        assert "--schema-only" not in workflow

    assert "push:" in proof
    assert "branches: [main]" in proof
    assert "pull_request:" not in proof
    assert "workflow_dispatch:" not in proof
    assert 'test "$(git rev-parse origin/main)" = "$EXPECTED_COMMIT"' in proof
    assert "project_release=\"$(jq -er '.release' .github/release-config.json)\"" in proof
    assert '--version "$project_release"' in proof
    assert "--schema-only" in proof
    assert "import tomllib" not in proof
    assert "uv run --no-sync python -m pytest tests/test_verify_release_project.py -q" in proof


def test_release_project_proof_runs_for_every_main_commit_only() -> None:
    proof = (ROOT / ".github/workflows/release-project-gate-proof.yml").read_text(encoding="utf-8")
    trigger = proof.split("permissions:", maxsplit=1)[0]

    assert "branches: [main]" in trigger
    assert "paths:" not in trigger
    assert "workflow_dispatch:" not in trigger
    assert "github.sha" in proof


def test_release_workflow_permissions_are_job_scoped_and_least_privilege() -> None:
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert _jobs_with_permission(release, "id-token: write") == {
        "publish-build-provenance",
        "publish-testpypi",
        "publish-pypi",
    }
    assert _jobs_with_permission(release, "contents: write") == {
        "draft-github-release",
        "publish-github-release",
    }
    assert "id-token: write" not in readiness
    assert "contents: write" not in readiness


def test_release_evidence_requires_retained_bootstrap_receipts() -> None:
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    policy = json.loads(
        (ROOT / "config/release-quality-policy-v1.json").read_text(encoding="utf-8")
    )

    assert "name: uv-bootstrap-readiness-package" in readiness
    assert "--bootstrap-receipt evidence/bootstrap/uv-bootstrap.json" in readiness
    assert "--slurpfile quality_policy config/release-quality-policy-v1.json" in readiness
    assert "$quality_policy[0].qa.readinessGates" in readiness
    assert "bootstrap-verification" in policy["qa"]["readinessGates"]
    assert '"bootstrap-verification",' in readiness
    assert "$quality_policy[0].qa.readinessGates == $manifest_gates" in readiness
    assert "release readiness gates do not match the quality policy" in readiness

    assert "--bootstrap-receipt .tools/receipts/uv-bootstrap.json" in release
    assert "name: uv-bootstrap-release-build" in release
    assert "--bootstrap-receipt bootstrap-receipt/uv-bootstrap.json" in release


@pytest.mark.parametrize(
    "script",
    (
        "scripts/build_release.py",
        "scripts/evaluate_uv_build.py",
        "scripts/create_release_evidence.py",
        "scripts/generate_release_checksums.py",
        "scripts/run_pinned_semgrep.py",
        "scripts/verify_codeql_sarif.py",
        "scripts/verify_index_artifacts.py",
        "scripts/verify_pypi_attestations.py",
        "scripts/verify_release_assets.py",
        "scripts/verify_release_configuration.py",
        "scripts/verify_release_milestone.py",
        "scripts/verify_release_project.py",
        "scripts/verify_release_quality.py",
    ),
)
def test_release_helpers_expose_non_mutating_help(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, script, "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert completed.returncode == 0
    assert "usage:" in completed.stdout
