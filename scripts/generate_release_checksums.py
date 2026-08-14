#!/usr/bin/env python3
"""Generate stable SHA-256 checksums for every regular release asset."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_checksums(directory: Path, output_name: str = "SHA256SUMS") -> dict[str, str]:
    """Generate deterministic SHA-256 entries for release artifacts."""
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"release asset directory does not exist: {directory}")
    if Path(output_name).name != output_name or "\n" in output_name or "\r" in output_name:
        raise ValueError("checksum output name must be a safe basename")

    checksum_path = directory / output_name
    entries = sorted(directory.iterdir(), key=lambda path: path.name)
    unsupported = [
        path.name
        for path in entries
        if path != checksum_path and (not path.is_file() or path.is_symlink())
    ]
    if unsupported:
        raise ValueError(f"release assets contain non-regular files: {unsupported}")

    assets = [path for path in entries if path != checksum_path]
    if not assets:
        raise ValueError("release asset directory is empty")
    for asset in assets:
        if (
            Path(asset.name).name != asset.name
            or asset.name.startswith(".")
            or "\n" in asset.name
            or "\r" in asset.name
        ):
            raise ValueError(f"unsafe release asset name: {asset.name!r}")

    hashes = {asset.name: _sha256(asset) for asset in assets}
    temporary = checksum_path.with_name(f".{output_name}.tmp")
    temporary.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())),
        encoding="utf-8",
    )
    temporary.replace(checksum_path)
    return hashes


def main() -> int:
    """Run the generate release checksums command and return its exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--output-name", default="SHA256SUMS")
    args = parser.parse_args()
    hashes = generate_checksums(args.directory, args.output_name)
    for name, digest in sorted(hashes.items()):
        print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
