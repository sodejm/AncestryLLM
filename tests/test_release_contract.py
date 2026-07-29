from __future__ import annotations

import ast
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
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    versioning = (ROOT / "docs/VERSIONING.md").read_text(encoding="utf-8")
    releasing = (ROOT / "docs/RELEASING.md").read_text(encoding="utf-8")
    normalized_releasing = " ".join(releasing.split())
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "pipx install ancestryllm" in readme
    assert "Semantic Versioning 2.0.0" in versioning
    assert "must never be replaced, moved, deleted, or reused" in versioning
    assert "OIDC Trusted Publishing" in releasing
    assert "git branch -d" in releasing
    assert "branch/worktree cleanup input" in releasing
    assert "docs/release-notes/<version>.md" in releasing
    assert "PyPI: unavailable" in releasing
    assert "release-tracker" in releasing
    assert "`sodejm` as the required reviewer" in releasing
    assert "self-approval must remain permitted" in releasing
    assert "Hosted control verification checklist" in releasing
    assert "unique commits" in releasing
    assert "resolved before merge" in releasing
    assert "explicitly approves a GitHub-only release" in releasing
    assert "API-token fallback" in releasing
    assert "required production approval" in releasing
    assert "independent production approval" not in releasing
    assert "every release asset except the checksum file itself" in releasing
    assert "verifies only the exact TestPyPI artifact hashes" in normalized_releasing
    assert "After production PyPI publishing" in releasing
    assert "TestPyPI hashes and install smoke tests" not in releasing
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
    script = (ROOT / "scripts/run_pinned_semgrep.py").read_text(encoding="utf-8")
    script_lock = ROOT / "scripts/run_pinned_semgrep.py.lock"
    assert script_lock.is_file()
    lock = tomllib.loads(script_lock.read_text(encoding="utf-8"))
    locked_semgrep = [package for package in lock["package"] if package.get("name") == "semgrep"]
    runner = "uv run --locked --script scripts/run_pinned_semgrep.py src"
    sources = {
        ".github/workflows/ci.yml": runner,
        ".github/workflows/release-readiness.yml": runner,
        ".github/workflows/release.yml": runner,
        "Makefile": "$(VENV_DIR)/bin/uv run --locked --script scripts/run_pinned_semgrep.py src",
    }

    assert '#     "semgrep==1.170.0",' in script
    assert [package["version"] for package in locked_semgrep] == ["1.170.0"]
    for relative_path, expected_command in sources.items():
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert content.count(expected_command) == 1
        assert "uvx semgrep" not in content
        assert "--config p/python" not in content
        assert "--config p/secrets" not in content


def test_release_workflows_enforce_tracker_exception_and_paginate() -> None:
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for workflow in (readiness, release):
        assert "verify_release_milestone.py" in workflow
        assert "--tracker-number 133" in workflow
        assert "--tracker-label release-tracker" in workflow
        assert "--paginate --slurp" in workflow


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
        "scripts/verify_release_milestone.py",
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
