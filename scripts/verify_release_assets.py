#!/usr/bin/env python3
"""Verify an attached release directory against the exact built assets."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

CHECKSUM_NAME = "SHA256SUMS"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_files(directory: Path, label: str) -> dict[str, Path]:
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"{label} release asset directory does not exist: {directory}")
    files: dict[str, Path] = {}
    unsupported: list[str] = []
    for path in directory.iterdir():
        name = path.name
        if (
            path.is_symlink()
            or not path.is_file()
            or Path(name).name != name
            or name.startswith(".")
            or "\n" in name
            or "\r" in name
        ):
            unsupported.append(name)
            continue
        files[name] = path
    if unsupported:
        raise ValueError(f"{label} release assets contain unsupported entries: {unsupported}")
    if not files:
        raise ValueError(f"{label} release asset directory is empty")
    return files


def _read_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or Path(name).name != name
            or name.startswith(".")
            or "\n" in name
            or "\r" in name
        ):
            raise ValueError(f"invalid release checksum line: {line!r}")
        if name in checksums:
            raise ValueError(f"duplicate release checksum entry: {name!r}")
        checksums[name] = digest
    if not checksums:
        raise ValueError("release checksum manifest is empty")
    return checksums


def verify_release_assets(expected_directory: Path, actual_directory: Path) -> None:
    """Verify every release asset against its reviewed checksum."""
    expected = _regular_files(expected_directory, "expected")
    actual = _regular_files(actual_directory, "actual")
    if set(actual) != set(expected):
        raise ValueError(
            "release asset inventory differs from the build: "
            f"missing={sorted(set(expected) - set(actual))}, "
            f"unexpected={sorted(set(actual) - set(expected))}"
        )
    if CHECKSUM_NAME not in expected:
        raise ValueError(f"expected release assets are missing {CHECKSUM_NAME}")
    if actual[CHECKSUM_NAME].read_bytes() != expected[CHECKSUM_NAME].read_bytes():
        raise ValueError(f"attached {CHECKSUM_NAME} differs from the build")

    checksums = _read_checksums(expected[CHECKSUM_NAME])
    required = set(expected) - {CHECKSUM_NAME}
    if set(checksums) != required:
        raise ValueError(
            "release checksum inventory differs from the asset inventory: "
            f"missing={sorted(required - set(checksums))}, "
            f"unexpected={sorted(set(checksums) - required)}"
        )
    for name, digest in sorted(checksums.items()):
        if _sha256(expected[name]) != digest:
            raise ValueError(f"built release asset hash differs for {name}")
        if _sha256(actual[name]) != digest:
            raise ValueError(f"attached release asset hash differs for {name}")


def main() -> int:
    """Run the verify release assets command and return its exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--actual", required=True, type=Path)
    args = parser.parse_args()
    verify_release_assets(args.expected, args.actual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
