#!/usr/bin/env python3
"""Build, validate, and compare deterministic Python release artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
BLOCKED_ARCHIVE_PARTS = {
    ".env",
    ".git",
    ".github",
    "family_trees",
    "scripts",
    "tests",
}
ALLOWED_SDIST_FILES = {
    "CHANGELOG.md",
    "LICENSE",
    "MANIFEST.in",
    "PKG-INFO",
    "README.md",
    "docs/reference/CLI.md",
    "docs/CONSOLE.md",
    "docs/reference/FILE_INGRESS.md",
    "docs/reference/GEDCOM_COMPATIBILITY.md",
    "docs/reference/PROVIDERS.md",
    "docs/RELEASING.md",
    "docs/SETUP_DIAGNOSTICS.md",
    "docs/reference/VERSIONING.md",
    "docs/release-evidence/README.md",
    "docs/release-evidence/issue-10-import-smoke-tests.md",
    "pyproject.toml",
    "setup.cfg",
}
ALLOWED_SDIST_PREFIXES = ("src/ancestryllm/", "src/ancestryllm.egg-info/")
REQUIRED_SDIST_PATHS = {
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "docs/reference/CLI.md",
    "docs/CONSOLE.md",
    "docs/reference/FILE_INGRESS.md",
    "docs/reference/GEDCOM_COMPATIBILITY.md",
    "docs/reference/PROVIDERS.md",
    "docs/RELEASING.md",
    "docs/SETUP_DIAGNOSTICS.md",
    "docs/reference/VERSIONING.md",
    "docs/release-evidence/README.md",
    "docs/release-evidence/issue-10-import-smoke-tests.md",
    "pyproject.toml",
    "src/ancestryllm/__init__.py",
    "src/ancestryllm/cli.py",
    "src/ancestryllm/storage/migrations/versions/0001_initial.py",
    "src/ancestryllm/storage/migrations/versions/0002_job_persistence.py",
}


def _run(*command: str, env: dict[str, str] | None = None) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed release tooling; never invokes a shell
            command,
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        detail = "\n".join(part.strip() for part in (error.stdout, error.stderr) if part.strip())
        raise RuntimeError(f"release command failed: {command[0]}\n{detail}") from error
    return completed.stdout.strip()


def project_version() -> str:
    """Read the project version from the canonical package metadata."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        value = str(tomllib.load(handle)["project"]["version"])
    if not SEMVER.fullmatch(value):
        raise ValueError(f"project.version is not a stable SemVer value: {value!r}")
    return value


def require_clean_checkout() -> None:
    """Reject a release build when the checkout contains uncommitted changes."""
    status = _run("git", "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RuntimeError(f"release builds require a clean checkout:\n{status}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_sdist_paths(path: Path, expected_root: str) -> set[str]:
    with tarfile.open(path, "r:gz") as archive:
        archive_members = archive.getmembers()
        unsupported = [
            item.name for item in archive_members if not item.isfile() and not item.isdir()
        ]
        if unsupported:
            raise RuntimeError(f"sdist contains links or special files: {sorted(unsupported)}")
        members = [item for item in archive_members if item.isfile()]
    prefix = f"{expected_root}/"
    relative: set[str] = set()
    for member in members:
        if not member.name.startswith(prefix):
            raise RuntimeError(f"sdist member is outside {expected_root}: {member.name}")
        item = member.name.removeprefix(prefix)
        if BLOCKED_ARCHIVE_PARTS.intersection(Path(item).parts):
            raise RuntimeError(f"blocked path entered the sdist: {item}")
        if item not in ALLOWED_SDIST_FILES and not item.startswith(ALLOWED_SDIST_PREFIXES):
            raise RuntimeError(f"path is outside the sdist allowlist: {item}")
        relative.add(item)
    return relative


def _wheel_metadata(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        blocked = [name for name in names if BLOCKED_ARCHIVE_PARTS.intersection(Path(name).parts)]
        if blocked:
            raise RuntimeError(f"blocked paths entered the wheel: {sorted(blocked)}")
        distribution_prefix = f"ancestryllm-{project_version()}.dist-info/"
        outside_allowlist = [
            name for name in names if not name.startswith(("ancestryllm/", distribution_prefix))
        ]
        if outside_allowlist:
            raise RuntimeError(
                f"paths are outside the wheel allowlist: {sorted(outside_allowlist)}"
            )
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entrypoint_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata_names) != 1 or len(entrypoint_names) != 1:
            raise RuntimeError("wheel must contain exactly one metadata and entry-point file")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        entrypoints = archive.read(entrypoint_names[0]).decode("utf-8")
    return str(metadata["Version"]), entrypoints


def validate_artifacts(directory: Path, version: str) -> dict[str, str]:
    """Validate release artifacts against the accepted package contract."""
    wheel = directory / f"ancestryllm-{version}-py3-none-any.whl"
    sdist = directory / f"ancestryllm-{version}.tar.gz"
    actual = {item.name for item in directory.iterdir() if item.is_file()}
    expected = {wheel.name, sdist.name}
    if actual != expected:
        raise RuntimeError(f"unexpected release artifacts: {sorted(actual ^ expected)}")

    sdist_root = f"ancestryllm-{version}"
    paths = _relative_sdist_paths(sdist, sdist_root)
    missing = REQUIRED_SDIST_PATHS - paths
    if missing:
        raise RuntimeError(f"required files are missing from the sdist: {sorted(missing)}")

    metadata_version, entrypoints = _wheel_metadata(wheel)
    if metadata_version != version:
        raise RuntimeError(
            f"wheel metadata version {metadata_version!r} does not match {version!r}"
        )
    if "ancestry = ancestryllm.cli:main" not in entrypoints:
        raise RuntimeError("wheel does not declare the ancestry console entry point")

    _run(sys.executable, "-m", "twine", "check", str(wheel), str(sdist))
    # Alembic migrations intentionally begin with a numeric revision and are
    # loaded by file path, so W004 is inapplicable. The exact wheel allowlist
    # above still rejects every unexpected file.
    _run(
        sys.executable,
        "-m",
        "check_wheel_contents",
        "--ignore",
        "W004",
        str(wheel),
    )
    return {path.name: _sha256(path) for path in (wheel, sdist)}


def _build(directory: Path, epoch: str) -> None:
    environment = {**os.environ, "SOURCE_DATE_EPOCH": epoch, "PYTHONHASHSEED": "0"}
    _run(
        sys.executable,
        "-m",
        "build",
        "--no-isolation",
        "--outdir",
        str(directory),
        env=environment,
    )
    _normalize_sdist(
        directory / f"ancestryllm-{project_version()}.tar.gz",
        epoch=int(epoch),
    )


def _normalize_sdist(path: Path, *, epoch: int) -> None:
    """Rewrite setuptools' sdist with stable gzip and tar metadata."""
    with tarfile.open(path, "r:gz") as source:
        members: list[tuple[tarfile.TarInfo, bytes | None]] = []
        for member in source.getmembers():
            data: bytes | None = None
            if member.isfile():
                extracted = source.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"could not read sdist member: {member.name}")
                data = extracted.read()
            members.append((member, data))

    normalized = io.BytesIO()
    with (
        gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=normalized,
            mtime=epoch,
        ) as compressed,
        tarfile.open(
            fileobj=compressed,
            mode="w",
            format=tarfile.PAX_FORMAT,
        ) as output,
    ):
        for member, data in members:
            member.mtime = epoch
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.pax_headers = {}
            output.addfile(member, io.BytesIO(data) if data is not None else None)
    path.write_bytes(normalized.getvalue())


def build_release(output: Path) -> dict[str, str]:
    """Build and verify the complete Python release artifact set."""
    require_clean_checkout()
    version = project_version()
    epoch = _run("git", "show", "-s", "--format=%ct", "HEAD")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError(f"release output directory must be empty: {output}")

    with (
        tempfile.TemporaryDirectory(prefix="ancestryllm-build-a-") as first_name,
        tempfile.TemporaryDirectory(prefix="ancestryllm-build-b-") as second_name,
    ):
        first = Path(first_name)
        second = Path(second_name)
        _build(first, epoch)
        first_hashes = validate_artifacts(first, version)
        _build(second, epoch)
        second_hashes = validate_artifacts(second, version)
        if first_hashes != second_hashes:
            raise RuntimeError(
                "release builds are not reproducible:\n"
                f"first={first_hashes}\nsecond={second_hashes}"
            )
        for name in sorted(first_hashes):
            shutil.copy2(first / name, output / name)

    checksum_path = output / "SHA256SUMS"
    checksum_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(first_hashes.items())),
        encoding="utf-8",
    )
    return first_hashes


def main() -> int:
    """Run the build release command and return its exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    hashes = build_release(args.output_dir)
    for name, digest in sorted(hashes.items()):
        print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
