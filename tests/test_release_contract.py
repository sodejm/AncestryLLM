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


def test_release_configuration_names_the_project_native_v0_5_control_plane() -> None:
    configuration = _release_configuration()

    assert configuration == {
        "schema_version": 2,
        "release": "0.5.0",
        "project": {
            "owner": "sodejm",
            "number": 2,
            "title": "AncestryLLM Feature Releases",
            "iteration": "v0.5.0 — Foundation",
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
    versioning = (ROOT / "docs/VERSIONING.md").read_text(encoding="utf-8")
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
    assert "v0.5.0 — Foundation" in releasing
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
    assert "include docs/FILE_INGRESS.md" in manifest


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
        "docs/CLI.md",
        "docs/CONSOLE.md",
        "docs/FILE_INGRESS.md",
        "docs/GEDCOM_COMPATIBILITY.md",
        "docs/PROVIDERS.md",
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
    assert "needs: [validate, build, publish-build-provenance, draft-github-release]" in release
    assert "verified-pypi-distributions" in release
    assert "verified-pypi-attestations" in release
    assert "artifact: [wheel, sdist]" in release
    assert "verify_release_assets.py" in release
    assert "verify_pypi_attestations.py" in release
    assert "pypi-attestations==0.0.30" in release
    assert "security-events: read" in readiness_codeql
    assert "upload-database: false" in readiness_codeql
    assert "if: ${{ always() }}" in readiness_codeql
    assert "upload: always" in codeql
    assert "upload: never" not in codeql
    assert re.findall(
        r"python -m pip install --disable-pip-version-check (uv\S*)",
        release,
    ) == ["uv==0.12.0", "uv==0.12.0"]
    assert re.findall(
        r"python -m pip install --disable-pip-version-check (uv\S*)",
        readiness,
    ) == ["uv==0.12.0", "uv==0.12.0", "uv==0.12.0"]
    assert release.count("attestations: true") == 1
    assert release.count("attestations: false") == 1
    assert "--actual downloaded" in release
    assert "--json isDraft,name,body" in release
    assert "--clobber" not in release


def test_security_gates_use_lockfile_semgrep_and_content_pinned_rules() -> None:
    project = _project()
    script = (ROOT / "scripts/run_pinned_semgrep.py").read_text(encoding="utf-8")
    script_lock = ROOT / "scripts/run_pinned_semgrep.py.lock"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert script_lock.is_file()
    lock = tomllib.loads(script_lock.read_text(encoding="utf-8"))
    locked_semgrep = [package for package in lock["package"] if package.get("name") == "semgrep"]
    runner = "uv run --locked --script scripts/run_pinned_semgrep.py src"
    sources = {
        ".github/workflows/ci.yml": runner,
        ".github/workflows/release-readiness.yml": runner,
        "Makefile": "$(VENV_DIR)/bin/uv run --locked --script scripts/run_pinned_semgrep.py src",
    }
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert '#     "semgrep==1.170.0",' in script
    assert [package["version"] for package in locked_semgrep] == ["1.170.0"]
    assert "uv==0.12.0" in project["optional-dependencies"]["dev"]
    assert "pip install --upgrade pip uv==0.12.0" in makefile
    for relative_path, expected_command in sources.items():
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert content.count(expected_command) == 1
        assert "uvx semgrep" not in content
        assert "--config p/python" not in content
        assert "--config p/secrets" not in content
    assert runner not in release


def test_workflows_invoke_pytest_as_a_module_from_the_repository_root() -> None:
    """Keep repository-only test tooling importable in clean hosted environments."""

    command = "uv run python -m pytest --verbose --cov --cov-report=term-missing"
    for relative_path in (
        ".github/workflows/ci.yml",
        ".github/workflows/release-readiness.yml",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert content.count(command) == 1
        assert "uv run pytest " not in content
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
    assert "uv run python scripts/build_release.py --output-dir dist" in release
    assert "uv run cyclonedx-py environment --output-file dist/sbom.json" in release
    assert "cmp dist/SHA256SUMS approved/artifacts/SHA256SUMS" in release
    for duplicate_gate in duplicate_gates:
        assert duplicate_gate not in release


def test_release_workflows_enforce_project_native_gate_and_paginate() -> None:
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for workflow in (readiness, release):
        assert "verify_release_configuration.py" in workflow
        assert "--config .github/release-config.json" in workflow
        assert "verify_release_project.py" in workflow
        assert "projectV2(number: $number)" in workflow
        assert "blockedBy(first: 100)" in workflow
        assert '--project-owner "$project_owner"' in workflow
        assert "--paginate --slurp" in workflow
        assert "verify_release_milestone.py" not in workflow
        assert "/milestones/" not in workflow
        assert "release-tracker" not in workflow
        assert "$version CLI" not in workflow
        assert "$EXPECTED_VERSION CLI" not in workflow


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


@pytest.mark.parametrize(
    "script",
    (
        "scripts/build_release.py",
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
