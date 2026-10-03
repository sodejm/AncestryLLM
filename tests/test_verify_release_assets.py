"""Verify release assets match the signed manifest and supported-platform allowlist."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_release_assets.py"
_SPEC = importlib.util.spec_from_file_location("verify_release_assets", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = verifier
_SPEC.loader.exec_module(verifier)


def _write_assets(directory: Path) -> None:
    directory.mkdir()
    assets = {
        "ancestryllm-0.2.0-py3-none-any.whl": b"wheel",
        "ancestryllm-0.2.0.tar.gz": b"sdist",
        "release-evidence.md": b"evidence",
    }
    for name, payload in assets.items():
        (directory / name).write_bytes(payload)
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(payload).hexdigest()}  {name}\n"
            for name, payload in sorted(assets.items())
        ),
        encoding="utf-8",
    )


def test_accepts_exact_release_asset_copy(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    _write_assets(expected)
    _write_assets(actual)

    verifier.verify_release_assets(expected, actual)


def test_rejects_unexpected_attached_asset(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    _write_assets(expected)
    _write_assets(actual)
    (actual / "stale.txt").write_text("stale", encoding="utf-8")

    with pytest.raises(ValueError, match=r"unexpected=.*stale"):
        verifier.verify_release_assets(expected, actual)


def test_rejects_tampered_attached_asset(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    _write_assets(expected)
    _write_assets(actual)
    (actual / "release-evidence.md").write_bytes(b"changed")

    with pytest.raises(ValueError, match="attached release asset hash differs"):
        verifier.verify_release_assets(expected, actual)


def test_rejects_checksum_manifest_missing_an_asset(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    _write_assets(expected)
    _write_assets(actual)
    for directory in (expected, actual):
        checksum = directory / "SHA256SUMS"
        checksum.write_text(
            "\n".join(
                line
                for line in checksum.read_text(encoding="utf-8").splitlines()
                if "release-evidence.md" not in line
            )
            + "\n",
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="checksum inventory differs"):
        verifier.verify_release_assets(expected, actual)
