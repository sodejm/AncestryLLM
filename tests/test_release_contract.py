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
    assert "prune tests" in manifest
    assert "prune family_trees" in manifest


@pytest.mark.parametrize(
    "script",
    (
        "scripts/build_release.py",
        "scripts/create_release_evidence.py",
        "scripts/verify_index_artifacts.py",
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
