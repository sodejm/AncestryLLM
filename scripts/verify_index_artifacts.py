#!/usr/bin/env python3
"""Download exact index artifacts and compare them with release checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ALLOWED_INDEX_HOSTS = {"pypi.org", "test.pypi.org"}
ALLOWED_FILE_HOSTS = {"files.pythonhosted.org", "test-files.pythonhosted.org"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_checksums(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not name.endswith((".whl", ".tar.gz"))
            or Path(name).name != name
        ):
            raise ValueError(f"invalid release checksum line: {line!r}")
        expected[name] = digest
    if not expected:
        raise ValueError("release checksum file contains no Python distributions")
    return expected


def _validated_https_url(url: str, allowed_hosts: set[str]) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"untrusted release URL: {url!r}")
    return url


def _request_json(url: str) -> dict[str, Any]:
    url = _validated_https_url(url, ALLOWED_INDEX_HOSTS)
    request = urllib.request.Request(  # noqa: S310 - URL and host validated above
        url, headers={"User-Agent": "ancestryllm-release-verifier"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return dict(json.load(response))


def verify_index(
    *,
    index: str,
    project: str,
    version: str,
    checksums: Path,
    output: Path,
) -> None:
    expected = read_checksums(checksums)
    payload = _request_json(f"{index.rstrip('/')}/pypi/{project}/{version}/json")
    published = {
        str(item["filename"]): item
        for item in payload.get("urls", [])
        if str(item.get("filename", "")) in expected
    }
    if set(published) != set(expected):
        raise RuntimeError(
            f"index files differ from the release: "
            f"missing={sorted(set(expected) - set(published))}, "
            f"unexpected={sorted(set(published) - set(expected))}"
        )

    output.mkdir(parents=True, exist_ok=True)
    for name, expected_digest in sorted(expected.items()):
        item = published[name]
        declared_digest = str(dict(item.get("digests", {})).get("sha256", ""))
        if declared_digest != expected_digest:
            raise RuntimeError(f"index-declared hash differs for {name}")
        artifact_url = _validated_https_url(str(item["url"]), ALLOWED_FILE_HOSTS)
        request = urllib.request.Request(  # noqa: S310 - URL and host validated above
            artifact_url, headers={"User-Agent": "ancestryllm-release-verifier"}
        )
        destination = output / name
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            destination.write_bytes(response.read())
        if _sha256(destination) != expected_digest:
            raise RuntimeError(f"downloaded hash differs for {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True)
    parser.add_argument("--project", default="ancestryllm")
    parser.add_argument("--version", required=True)
    parser.add_argument("--checksums", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    verify_index(
        index=args.index,
        project=args.project,
        version=args.version,
        checksums=args.checksums,
        output=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
