from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from ancestryllm.gedcom.sync import run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "gedcom_incremental"


def test_incremental_initialization_is_offline_and_publishes_atomic_bundle(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
        status = run_sync(
            [
                "update",
                "--master",
                str(FIXTURES / "baseline-master.ged"),
                "--initialize-manifest",
                "--snapshot",
                f"ancestry-main:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
                "--exported-at",
                "ancestry-main=2025-01-15",
                "--release-root",
                str(releases),
                "--no-quality-report",
            ]
        )
    assert status == 0
    bundles = list(releases.glob("g0001-*"))
    assert len(bundles) == 1
    assert {path.name for path in bundles[0].iterdir()} == {
        "master.ged",
        "manifest.json",
        "update.md",
        "quality.md",
        "rollback.json",
    }
    manifest = json.loads((bundles[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation"] == 1


def test_incremental_active_snapshot_is_idempotent(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first_args = [
        "update",
        "--master",
        str(FIXTURES / "baseline-master.ged"),
        "--initialize-manifest",
        "--snapshot",
        f"ancestry-main:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
        "--release-root",
        str(releases),
        "--no-quality-report",
    ]
    assert run_sync(first_args) == 0
    bundle = next(releases.glob("g0001-*"))
    assert (
        run_sync(
            [
                "update",
                "--master",
                str(bundle / "master.ged"),
                "--manifest",
                str(bundle / "manifest.json"),
                "--snapshot",
                f"ancestry-main:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
                "--release-root",
                str(releases),
                "--no-quality-report",
            ]
        )
        == 0
    )
    assert len(list(releases.glob("g*-*"))) == 1
