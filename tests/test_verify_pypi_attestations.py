"""Tests for verifying PyPI attestations and release provenance."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_pypi_attestations.py"
_SPEC = importlib.util.spec_from_file_location("verify_pypi_attestations", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = verifier
_SPEC.loader.exec_module(verifier)


def _provenance(
    name: str,
    digest: str,
    *,
    repository: str = "sodejm/AncestryLLM",
    workflow: str = "release.yml",
    environment: str = "pypi",
    predicate_type: str = "https://docs.pypi.org/attestations/publish/v1",
) -> dict[str, object]:
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": name, "digest": {"sha256": digest}}],
        "predicateType": predicate_type,
        "predicate": None,
    }
    encoded = base64.b64encode(json.dumps(statement, separators=(",", ":")).encode("utf-8")).decode(
        "ascii"
    )
    return {
        "version": 1,
        "attestation_bundles": [
            {
                "publisher": {
                    "kind": "GitHub",
                    "repository": repository,
                    "workflow": workflow,
                    "environment": environment,
                    "claims": None,
                },
                "attestations": [
                    {
                        "version": 1,
                        "envelope": {
                            "statement": encoded,
                            "signature": "test-signature",
                        },
                        "verification_material": {"certificate": "test-certificate"},
                    }
                ],
            }
        ],
    }


def _release_files(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    contents = {
        "ancestryllm-0.2.0-py3-none-any.whl": b"release wheel",
        "ancestryllm-0.2.0.tar.gz": b"release sdist",
    }
    digests: dict[str, str] = {}
    for name, content in contents.items():
        artifact = artifacts / name
        artifact.write_bytes(content)
        digests[name] = hashlib.sha256(content).hexdigest()
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in digests.items()),
        encoding="utf-8",
    )
    return artifacts, checksums, digests


def _responses(
    digests: dict[str, str],
    *,
    provenance_digest: str | None = None,
    **overrides: str,
):
    def respond(url: str) -> dict[str, object]:
        if "/pypi/" in url:
            return {
                "urls": [
                    {
                        "filename": name,
                        "digests": {"sha256": digest},
                        "url": f"https://files.pythonhosted.org/packages/{name}",
                    }
                    for name, digest in digests.items()
                ]
            }
        name = next(name for name in digests if f"/{name}/" in url)
        return _provenance(name, provenance_digest or digests[name], **overrides)

    return respond


def test_verifies_every_file_and_writes_pep740_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, checksums, digests = _release_files(tmp_path)
    monkeypatch.setattr(verifier, "_request_json", _responses(digests))
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        verifier,
        "_run_verifier",
        lambda repository, artifact_url: calls.append((repository, artifact_url)) or "verified\n",
    )

    evidence = tmp_path / "evidence"
    verifier.verify_pypi_attestations(
        project="ancestryllm",
        version="0.2.0",
        repository="sodejm/AncestryLLM",
        workflow="release.yml",
        environment="pypi",
        checksums=checksums,
        artifacts=artifacts,
        evidence=evidence,
    )

    assert calls == [
        ("sodejm/AncestryLLM", f"https://files.pythonhosted.org/packages/{name}")
        for name in sorted(digests)
    ]
    for name in digests:
        assert (
            json.loads((evidence / f"{name}.provenance").read_text(encoding="utf-8"))["version"]
            == 1
        )
        assert (evidence / f"{name}.verification.txt").read_text(encoding="utf-8") == "verified\n"
    summary = json.loads((evidence / "verification.json").read_text(encoding="utf-8"))
    assert summary["verifier"] == "pypi-attestations==0.0.30"
    assert summary["artifacts"] == [
        {"filename": name, "sha256": digests[name]} for name in sorted(digests)
    ]


@pytest.mark.parametrize(
    "repository", ("sodejm", "sodejm/AncestryLLM/extra", "https://github.com/x/y")
)
def test_rejects_repository_that_is_not_owner_slash_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository: str,
) -> None:
    artifacts, checksums, digests = _release_files(tmp_path)
    monkeypatch.setattr(
        verifier,
        "_request_json",
        lambda _url: pytest.fail("invalid repository must fail before network access"),
    )

    with pytest.raises(ValueError, match="owner/repository"):
        verifier.verify_pypi_attestations(
            project="ancestryllm",
            version="0.2.0",
            repository=repository,
            workflow="release.yml",
            environment="pypi",
            checksums=checksums,
            artifacts=artifacts,
            evidence=tmp_path / "evidence",
        )


@pytest.mark.parametrize(
    "url",
    (
        "https://user@files.pythonhosted.org/packages/release.whl",
        "https://user:password@files.pythonhosted.org/packages/release.whl",
        "https://files.pythonhosted.org:444/packages/release.whl",
        "https://files.pythonhosted.org/packages/release.whl#fragment",
    ),
)
def test_rejects_untrusted_pypi_artifact_url(url: str) -> None:
    with pytest.raises(RuntimeError, match="untrusted PyPI URL"):
        verifier._validated_url(url, host="files.pythonhosted.org")


@pytest.mark.parametrize(
    ("case", "message"),
    (("missing", "missing="), ("unexpected", "unexpected=")),
)
def test_rejects_missing_or_unexpected_published_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
) -> None:
    artifacts, checksums, digests = _release_files(tmp_path)
    published = dict(digests)
    if case == "missing":
        published.pop("ancestryllm-0.2.0.tar.gz")
    else:
        published["unexpected.whl"] = "0" * 64
    monkeypatch.setattr(verifier, "_request_json", _responses(published))

    with pytest.raises(RuntimeError, match=message):
        verifier.verify_pypi_attestations(
            project="ancestryllm",
            version="0.2.0",
            repository="sodejm/AncestryLLM",
            workflow="release.yml",
            environment="pypi",
            checksums=checksums,
            artifacts=artifacts,
            evidence=tmp_path / "evidence",
        )


def test_rejects_checksums_without_exactly_one_wheel_and_sdist(tmp_path: Path) -> None:
    artifacts, checksums, digests = _release_files(tmp_path)
    wheel = "ancestryllm-0.2.0-py3-none-any.whl"
    checksums.write_text(f"{digests[wheel]}  {wheel}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one wheel and one sdist"):
        verifier.verify_pypi_attestations(
            project="ancestryllm",
            version="0.2.0",
            repository="sodejm/AncestryLLM",
            workflow="release.yml",
            environment="pypi",
            checksums=checksums,
            artifacts=artifacts,
            evidence=tmp_path / "evidence",
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"repository": "attacker/repository"}, "publisher"),
        ({"workflow": "other.yml"}, "publisher"),
        ({"environment": ""}, "publisher"),
        (
            {"predicate_type": "https://slsa.dev/provenance/v1"},
            "publish attestation",
        ),
    ),
)
def test_rejects_wrong_publisher_or_missing_publish_statement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    message: str,
) -> None:
    artifacts, checksums, digests = _release_files(tmp_path)
    monkeypatch.setattr(
        verifier,
        "_request_json",
        _responses(digests, **overrides),
    )
    monkeypatch.setattr(
        verifier,
        "_run_verifier",
        lambda *_args: pytest.fail("structurally invalid provenance must not be verified"),
    )

    with pytest.raises(RuntimeError, match=message):
        verifier.verify_pypi_attestations(
            project="ancestryllm",
            version="0.2.0",
            repository="sodejm/AncestryLLM",
            workflow="release.yml",
            environment="pypi",
            checksums=checksums,
            artifacts=artifacts,
            evidence=tmp_path / "evidence",
        )


def test_rejects_subject_digest_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifacts, checksums, digests = _release_files(tmp_path)
    monkeypatch.setattr(
        verifier,
        "_request_json",
        _responses(digests, provenance_digest="0" * 64),
    )

    with pytest.raises(RuntimeError, match="publish attestation"):
        verifier.verify_pypi_attestations(
            project="ancestryllm",
            version="0.2.0",
            repository="sodejm/AncestryLLM",
            workflow="release.yml",
            environment="pypi",
            checksums=checksums,
            artifacts=artifacts,
            evidence=tmp_path / "evidence",
        )


def test_fails_closed_when_cryptographic_verifier_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, checksums, digests = _release_files(tmp_path)
    monkeypatch.setattr(verifier, "_request_json", _responses(digests))
    monkeypatch.setattr(
        verifier,
        "_run_verifier",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("signature verification failed")),
    )

    with pytest.raises(RuntimeError, match="signature verification failed"):
        verifier.verify_pypi_attestations(
            project="ancestryllm",
            version="0.2.0",
            repository="sodejm/AncestryLLM",
            workflow="release.yml",
            environment="pypi",
            checksums=checksums,
            artifacts=artifacts,
            evidence=tmp_path / "evidence",
        )
