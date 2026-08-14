"""Verify repository safety checks reject credentials and private genealogy artifacts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAFETY_CHECK = REPOSITORY_ROOT / "scripts" / "check_repository_safety.sh"

SIGNING_ARTIFACTS = (
    "secure/apple-certificate.p12",
    "secure/windows-certificate.pfx",
    "secure/apple-api-key.p8",
    "secure/signing-key.pem",
    "secure/signing-key.key",
    "secure/linux-private.asc",
    "secure/linux-private.gpg",
    "secure/apple-certificate.b64",
    "secure/profile.mobileprovision",
)


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("relative_path", SIGNING_ARTIFACTS)
def test_signing_artifacts_are_ignored(relative_path: str) -> None:
    result = _git(REPOSITORY_ROOT, "check-ignore", "--no-index", relative_path)

    assert result.returncode == 0, f"expected .gitignore to cover {relative_path}"


def test_repository_safety_rejects_force_added_signing_artifacts(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    shutil.copy2(SAFETY_CHECK, repository / SAFETY_CHECK.name)
    assert _git(repository, "init", "--quiet").returncode == 0

    for relative_path in SIGNING_ARTIFACTS:
        artifact = repository / relative_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("fictional test artifact\n", encoding="utf-8")
    assert _git(repository, "add", "--force", ".").returncode == 0

    result = subprocess.run(
        [str(repository / SAFETY_CHECK.name)],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "private/runtime artifact is tracked" in result.stderr
    for relative_path in SIGNING_ARTIFACTS:
        assert relative_path in result.stdout
