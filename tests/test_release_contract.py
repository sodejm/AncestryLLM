from __future__ import annotations

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
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "pipx install ancestryllm" in readme
    assert "Semantic Versioning 2.0.0" in versioning
    assert "must never be replaced, moved, deleted, or reused" in versioning
    assert "OIDC Trusted Publishing" in releasing
    assert "git branch -d" in releasing
    assert "branch/worktree cleanup input" in releasing
    assert "docs/release-notes/<version>.md" in releasing
    assert "PyPI: unavailable" in releasing
    assert "every release asset except the checksum file itself" in releasing
    assert "prune tests" in manifest
    assert "prune family_trees" in manifest


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
    assert "needs: [validate, build, attest, draft-github-release]" in release
    assert "verified-pypi-distributions" in release
    assert "artifact: [wheel, sdist]" in release
    assert "verify_release_assets.py" in release
    assert "--actual downloaded" in release
    assert "--json isDraft,name,body" in release
    assert "--clobber" not in release


@pytest.mark.parametrize(
    "script",
    (
        "scripts/build_release.py",
        "scripts/create_release_evidence.py",
        "scripts/generate_release_checksums.py",
        "scripts/verify_codeql_sarif.py",
        "scripts/verify_index_artifacts.py",
        "scripts/verify_release_assets.py",
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
