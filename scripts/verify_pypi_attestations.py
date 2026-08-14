#!/usr/bin/env python3
"""Verify and preserve PyPI PEP 740 attestations for release artifacts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

IN_TOTO_STATEMENT = "https://in-toto.io/Statement/v1"
PUBLISH_PREDICATE = "https://docs.pypi.org/attestations/publish/v1"
PYPI_ATTESTATIONS = "pypi-attestations==0.0.30"
PYPI_HOST = "pypi.org"
PYPI_FILE_HOST = "files.pythonhosted.org"
GITHUB_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_checksums(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or Path(name).name != name
            or not name.endswith((".whl", ".tar.gz"))
        ):
            raise ValueError(f"invalid distribution checksum line: {line!r}")
        if name in seen:
            raise ValueError(f"duplicate distribution checksum entry: {name!r}")
        seen.add(name)
        expected[name] = digest
    if not expected:
        raise ValueError("distribution checksum file is empty")
    wheels = [name for name in expected if name.endswith(".whl")]
    sdists = [name for name in expected if name.endswith(".tar.gz")]
    if len(expected) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("distribution checksums must contain exactly one wheel and one sdist")
    return expected


def _validated_repository(repository: str) -> str:
    components = repository.split("/")
    if (
        len(components) != 2
        or GITHUB_OWNER.fullmatch(components[0]) is None
        or GITHUB_REPOSITORY.fullmatch(components[1]) is None
    ):
        raise ValueError("repository must be a GitHub owner/repository name")
    return repository


def _validated_url(url: str, *, host: str) -> str:
    parsed = urlparse(url)
    try:
        trusted = (
            parsed.scheme == "https"
            and parsed.hostname == host
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
    except ValueError:
        trusted = False
    if not trusted:
        raise RuntimeError(f"untrusted PyPI URL: {url!r}")
    return url


def _request_json(url: str) -> dict[str, Any]:
    accept = (
        "application/vnd.pypi.integrity.v1+json" if "/integrity/" in url else "application/json"
    )
    request = urllib.request.Request(  # noqa: S310 - host is validated first
        _validated_url(url, host=PYPI_HOST),
        headers={
            "Accept": accept,
            "User-Agent": "ancestryllm-release-attestation-verifier",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("PyPI returned a non-object JSON response")
    return payload


def _published_urls(
    payload: dict[str, Any],
    expected: dict[str, str],
) -> dict[str, str]:
    raw_urls = payload.get("urls")
    if not isinstance(raw_urls, list):
        raise RuntimeError("PyPI response does not contain a release file list")
    published: dict[str, tuple[str, object]] = {}
    for raw_item in raw_urls:
        if not isinstance(raw_item, dict):
            raise RuntimeError("PyPI response contains a malformed release file")
        name = raw_item.get("filename")
        if not isinstance(name, str) or Path(name).name != name:
            raise RuntimeError("PyPI response contains an unsafe release filename")
        if name in published:
            raise RuntimeError(f"PyPI response contains duplicate release file {name!r}")
        digests = raw_item.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        published[name] = (
            _validated_url(str(raw_item.get("url", "")), host=PYPI_FILE_HOST),
            digest,
        )
    if set(published) != set(expected):
        raise RuntimeError(
            "PyPI files differ from release checksums: "
            f"missing={sorted(set(expected) - set(published))}, "
            f"unexpected={sorted(set(published) - set(expected))}"
        )
    for name, (_, digest) in published.items():
        if digest != expected[name]:
            raise RuntimeError(f"PyPI hash does not match release checksum for {name}")
    return {name: url for name, (url, _) in published.items()}


def _statement_matches(
    raw_attestation: object,
    *,
    name: str,
    digest: str,
) -> bool:
    if not isinstance(raw_attestation, dict) or raw_attestation.get("version") != 1:
        return False
    raw_envelope = raw_attestation.get("envelope")
    if not isinstance(raw_envelope, dict):
        return False
    encoded = raw_envelope.get("statement")
    if not isinstance(encoded, str):
        return False
    try:
        statement = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, json.JSONDecodeError):
        return False
    if not isinstance(statement, dict):
        return False
    if (
        statement.get("_type") != IN_TOTO_STATEMENT
        or statement.get("predicateType") != PUBLISH_PREDICATE
    ):
        return False
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 1:
        return False
    subject = subjects[0]
    if not isinstance(subject, dict) or subject.get("name") != name:
        return False
    subject_digest = subject.get("digest")
    return isinstance(subject_digest, dict) and subject_digest.get("sha256") == digest


def _validate_provenance(
    payload: dict[str, Any],
    *,
    name: str,
    digest: str,
    repository: str,
    workflow: str,
    environment: str,
) -> None:
    if payload.get("version") != 1:
        raise RuntimeError(f"unsupported PyPI provenance version for {name}")
    raw_bundles = payload.get("attestation_bundles")
    if not isinstance(raw_bundles, list) or not raw_bundles:
        raise RuntimeError(f"PyPI provenance contains no attestation bundles for {name}")

    publisher_found = False
    for raw_bundle in raw_bundles:
        if not isinstance(raw_bundle, dict):
            continue
        publisher = raw_bundle.get("publisher")
        if not isinstance(publisher, dict):
            continue
        if (
            publisher.get("kind") != "GitHub"
            or publisher.get("repository") != repository
            or publisher.get("workflow") != workflow
            or publisher.get("environment") != environment
        ):
            continue
        publisher_found = True
        attestations = raw_bundle.get("attestations")
        if isinstance(attestations, list) and any(
            _statement_matches(attestation, name=name, digest=digest)
            for attestation in attestations
        ):
            return

    if not publisher_found:
        raise RuntimeError(f"PyPI provenance publisher does not match policy for {name}")
    raise RuntimeError(f"PyPI provenance has no matching PEP 740 publish attestation for {name}")


def _run_verifier(repository: str, artifact_url: str) -> str:
    repository = _validated_repository(repository)
    result = subprocess.run(  # noqa: S603 - executable path and arguments are fixed/validated
        [
            sys.executable,
            "-c",
            "from pypi_attestations._cli import main; raise SystemExit(main())",
            "verify",
            "pypi",
            "--repository",
            f"https://github.com/{repository}",
            artifact_url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(
            f"{PYPI_ATTESTATIONS} rejected {artifact_url} "
            f"(exit {result.returncode}): {output.strip()}"
        )
    return output or f"{PYPI_ATTESTATIONS} verification succeeded\n"


def verify_pypi_attestations(
    *,
    project: str,
    version: str,
    repository: str,
    workflow: str,
    environment: str,
    checksums: Path,
    artifacts: Path,
    evidence: Path,
) -> None:
    """Verify release attestations against the trusted PyPI identity."""
    repository = _validated_repository(repository)
    expected = _read_checksums(checksums)
    for name, digest in expected.items():
        artifact = artifacts / name
        if not artifact.is_file() or _sha256(artifact) != digest:
            raise RuntimeError(f"local verified artifact hash differs for {name}")

    project_url = quote(project, safe="._-")
    version_url = quote(version, safe="._-+")
    release = _request_json(f"https://pypi.org/pypi/{project_url}/{version_url}/json")
    published_urls = _published_urls(release, expected)

    evidence.mkdir(parents=True, exist_ok=False)
    verified: list[dict[str, str]] = []
    for name, digest in sorted(expected.items()):
        name_url = quote(name, safe="._-+")
        provenance = _request_json(
            f"https://pypi.org/integrity/{project_url}/{version_url}/{name_url}/provenance"
        )
        _validate_provenance(
            provenance,
            name=name,
            digest=digest,
            repository=repository,
            workflow=workflow,
            environment=environment,
        )
        (evidence / f"{name}.provenance").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        verification = _run_verifier(repository, published_urls[name])
        (evidence / f"{name}.verification.txt").write_text(
            verification,
            encoding="utf-8",
        )
        verified.append({"filename": name, "sha256": digest})

    (evidence / "verification.json").write_text(
        json.dumps(
            {
                "artifacts": verified,
                "environment": environment,
                "project": project,
                "repository": repository,
                "verifier": PYPI_ATTESTATIONS,
                "version": version,
                "workflow": workflow,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    """Run the verify PyPI attestations command and return its exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="ancestryllm")
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", default="release.yml")
    parser.add_argument("--environment", default="pypi")
    parser.add_argument("--checksums", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument(
        "--verifier",
        required=True,
        help=f"must be the policy-pinned verifier {PYPI_ATTESTATIONS}",
    )
    args = parser.parse_args()
    if args.verifier != PYPI_ATTESTATIONS:
        parser.error(f"--verifier must be exactly {PYPI_ATTESTATIONS}")
    verify_pypi_attestations(
        project=args.project,
        version=args.version,
        repository=args.repository,
        workflow=args.workflow,
        environment=args.environment,
        checksums=args.checksums,
        artifacts=args.artifacts,
        evidence=args.evidence,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
