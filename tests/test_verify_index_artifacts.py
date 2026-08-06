"""Tests for verifying published index artifacts against checksums."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_index_artifacts.py"
_SPEC = importlib.util.spec_from_file_location("verify_index_artifacts", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = verifier
_SPEC.loader.exec_module(verifier)


class _Download:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def __enter__(self) -> _Download:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._content


def test_reads_distributions_from_full_release_checksum_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        "\n".join(
            [
                f"{'a' * 64}  ancestryllm-0.2.0-py3-none-any.whl",
                f"{'b' * 64}  ancestryllm-0.2.0.tar.gz",
                f"{'c' * 64}  release-evidence.md",
                f"{'d' * 64}  sbom.json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert verifier.read_checksums(manifest) == {
        "ancestryllm-0.2.0-py3-none-any.whl": "a" * 64,
        "ancestryllm-0.2.0.tar.gz": "b" * 64,
    }


def test_rejects_duplicate_non_distribution_checksum_entries(tmp_path: Path) -> None:
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        "\n".join(
            [
                f"{'a' * 64}  ancestryllm-0.2.0.tar.gz",
                f"{'b' * 64}  sbom.json",
                f"{'c' * 64}  sbom.json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate"):
        verifier.read_checksums(manifest)


def test_index_verification_rejects_unexpected_published_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        f"{'a' * 64}  ancestryllm-0.2.0.tar.gz\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verifier,
        "_request_json",
        lambda _url: {
            "urls": [
                {"filename": "ancestryllm-0.2.0.tar.gz"},
                {"filename": "unexpected.whl"},
            ]
        },
    )

    with pytest.raises(RuntimeError, match=r"unexpected=\['unexpected\.whl'\]"):
        verifier.verify_index(
            index="https://test.pypi.org",
            project="ancestryllm",
            version="0.2.0",
            checksums=manifest,
            output=tmp_path / "downloaded",
        )


def test_index_verification_rejects_duplicate_published_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        f"{'a' * 64}  ancestryllm-0.2.0.tar.gz\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verifier,
        "_request_json",
        lambda _url: {
            "urls": [
                {"filename": "ancestryllm-0.2.0.tar.gz"},
                {"filename": "ancestryllm-0.2.0.tar.gz"},
            ]
        },
    )

    with pytest.raises(RuntimeError, match="duplicate release file"):
        verifier.verify_index(
            index="https://pypi.org",
            project="ancestryllm",
            version="0.2.0",
            checksums=manifest,
            output=tmp_path / "downloaded",
        )


def test_successful_verification_writes_distribution_only_checksum_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel_name = "ancestryllm-0.2.0-py3-none-any.whl"
    sdist_name = "ancestryllm-0.2.0.tar.gz"
    downloads = {
        wheel_name: b"verified wheel",
        sdist_name: b"verified sdist",
    }
    digests = {name: hashlib.sha256(content).hexdigest() for name, content in downloads.items()}
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        "".join(
            [
                *(f"{digests[name]}  {name}\n" for name in sorted(downloads)),
                f"{'c' * 64}  sbom.json\n",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verifier,
        "_request_json",
        lambda _url: {
            "urls": [
                {
                    "filename": name,
                    "digests": {"sha256": digests[name]},
                    "url": f"https://files.pythonhosted.org/{name}",
                }
                for name in sorted(downloads)
            ]
        },
    )
    monkeypatch.setattr(
        verifier.urllib.request,
        "urlopen",
        lambda request, timeout: _Download(downloads[Path(request.full_url).name]),
    )

    output = tmp_path / "downloaded"
    verifier.verify_index(
        index="https://pypi.org",
        project="ancestryllm",
        version="0.2.0",
        checksums=manifest,
        output=output,
    )

    assert {path.name for path in output.iterdir()} == {
        "SHA256SUMS",
        wheel_name,
        sdist_name,
    }
    assert (output / "SHA256SUMS").read_text(encoding="utf-8") == "".join(
        f"{digests[name]}  {name}\n" for name in sorted(downloads)
    )
