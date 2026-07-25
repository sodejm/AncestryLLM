from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "generate_release_checksums.py"
_SPEC = importlib.util.spec_from_file_location("generate_release_checksums", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
checksums = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = checksums
_SPEC.loader.exec_module(checksums)


def test_generates_checksums_for_every_release_asset(tmp_path: Path) -> None:
    assets = {
        "ancestryllm-0.2.0-py3-none-any.whl": b"wheel",
        "ancestryllm-0.2.0.tar.gz": b"sdist",
        "release-evidence.md": b"evidence",
        "sbom.json": b"sbom",
    }
    for name, payload in assets.items():
        (tmp_path / name).write_bytes(payload)

    generated = checksums.generate_checksums(tmp_path)

    assert set(generated) == set(assets)
    rendered = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8")
    for name, payload in assets.items():
        assert f"{hashlib.sha256(payload).hexdigest()}  {name}\n" in rendered
    assert "SHA256SUMS  SHA256SUMS" not in rendered


def test_replaces_old_distribution_only_checksum_file(tmp_path: Path) -> None:
    (tmp_path / "ancestryllm-0.2.0.tar.gz").write_bytes(b"sdist")
    (tmp_path / "sbom.json").write_bytes(b"sbom")
    (tmp_path / "SHA256SUMS").write_text("stale\n", encoding="utf-8")

    checksums.generate_checksums(tmp_path)

    rendered = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8")
    assert "stale" not in rendered
    assert "sbom.json" in rendered


def test_rejects_non_regular_release_assets(tmp_path: Path) -> None:
    (tmp_path / "asset").write_bytes(b"asset")
    (tmp_path / "nested").mkdir()

    with pytest.raises(ValueError, match="non-regular"):
        checksums.generate_checksums(tmp_path)


def test_rejects_hidden_release_assets(tmp_path: Path) -> None:
    (tmp_path / "asset").write_bytes(b"asset")
    (tmp_path / ".stale-upload").write_bytes(b"partial")

    with pytest.raises(ValueError, match="unsafe release asset name"):
        checksums.generate_checksums(tmp_path)
