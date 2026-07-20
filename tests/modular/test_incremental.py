from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ancestryllm.gedcom import engine, incremental
from ancestryllm.gedcom.sync import run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "gedcom_incremental"


def test_incremental_argument_errors_use_stable_typed_contract() -> None:
    parser = incremental.PlainEnglishArgumentParser(prog="ancestry gedcom update")
    with pytest.raises(incremental.SyncError) as raised:
        parser.error("missing --master")
    assert raised.value.code == "SYNC_CONFIGURATION"
    assert raised.value.exit_code == 2


def test_incremental_normalization_boundary_returns_strings() -> None:
    assert incremental._normal_value("DATE", "July 15, 1850", engine) == "15 JUL 1850"
    assert incremental._normal_value("CTRY", "USA", engine) == "united states"


def _snapshot(name: str, vendor: str, version: int) -> str:
    return f"{name}:{vendor}={FIXTURES / f'{vendor}-snapshot-v{version}.ged'}"


def _initialize_release(releases: Path) -> Path:
    assert (
        run_sync(
            [
                "update",
                "--master",
                str(FIXTURES / "baseline-master.ged"),
                "--initialize-manifest",
                "--snapshot",
                _snapshot("ancestry-main", "ancestry", 1),
                "--snapshot",
                _snapshot("myheritage-main", "myheritage", 1),
                "--exported-at",
                "ancestry-main=2025-01-15",
                "--exported-at",
                "myheritage-main=2025-02-03",
                "--release-root",
                str(releases),
                "--no-quality-report",
            ]
        )
        == 0
    )
    return next(releases.glob("g0001-*"))


def _update_args(releases: Path, bundle: Path, *snapshots: str) -> list[str]:
    return [
        "update",
        "--master",
        str(bundle / "master.ged"),
        "--manifest",
        str(bundle / "manifest.json"),
        "--release-root",
        str(releases),
        "--no-quality-report",
        *(item for snapshot in snapshots for item in ("--snapshot", snapshot)),
    ]


def test_incremental_initialization_is_offline_and_publishes_atomic_bundle(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    with (
        patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "must-not-be-read", "GEMINI_API_KEY": "must-not-be-read"},
        ),
        patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")),
    ):
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
    assert "AI backend: `none` (offline deterministic)" in (bundles[0] / "update.md").read_text(
        encoding="utf-8"
    )


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


def test_incremental_replaces_changed_snapshots_and_preserves_snapshot_history(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)

    assert (
        run_sync(
            _update_args(
                releases,
                first,
                _snapshot("ancestry-main", "ancestry", 2),
                _snapshot("myheritage-main", "myheritage", 2),
            )
        )
        == 0
    )

    second = next(releases.glob("g0002-*"))
    manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation"] == 2
    assert len(manifest["snapshots"]) == 4
    assert manifest["active_snapshots"]["ancestry-main"] in manifest["snapshots"]
    assert manifest["active_snapshots"]["myheritage-main"] in manifest["snapshots"]
    assert manifest["snapshots"][manifest["active_snapshots"]["ancestry-main"]]["path"].endswith(
        "ancestry-snapshot-v2.ged"
    )

    master = (second / "master.ged").read_text(encoding="utf-8")
    assert "0 @I100@ INDI" in master
    assert "Ilyan /Shore/" in master
    assert "@AX-15@" not in master
    assert "School librarian" not in master
    assert "Cedar Bay, Vermont, United States" in master


def test_rebase_requires_confirmation_then_tombstones_manual_deletions(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    edited_master = tmp_path / "fictional-manual-deletion.ged"
    edited_master.write_text(
        (first / "master.ged").read_text(encoding="utf-8").replace("1 OCCU Cartographer\n", ""),
        encoding="utf-8",
    )
    rebase_args = [
        "rebase",
        "--master",
        str(edited_master),
        "--manifest",
        str(first / "manifest.json"),
        "--release-root",
        str(releases),
        "--reason",
        "Fictional manual correction for regression coverage",
    ]

    assert run_sync(rebase_args) == 6
    assert len(list(releases.glob("g*-*"))) == 1

    assert run_sync([*rebase_args, "--accept-manual-deletions"]) == 0
    rebased = next(releases.glob("g0002-*"))
    rebase_manifest = json.loads((rebased / "manifest.json").read_text(encoding="utf-8"))
    assert rebase_manifest["manual_tombstones"]
    assert "Cartographer" not in (rebased / "master.ged").read_text(encoding="utf-8")

    assert run_sync(_update_args(releases, rebased, _snapshot("ancestry-main", "ancestry", 2))) == 0
    updated = next(releases.glob("g0003-*"))
    assert "Cartographer" not in (updated / "master.ged").read_text(encoding="utf-8")
    assert "intentional manual deletion" in (updated / "update.md").read_text(encoding="utf-8")


def test_update_writes_rollback_metadata_and_cleans_up_interrupted_publish(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)

    assert run_sync(_update_args(releases, first, _snapshot("ancestry-main", "ancestry", 2))) == 0
    second = next(releases.glob("g0002-*"))
    rollback = json.loads((second / "rollback.json").read_text(encoding="utf-8"))
    assert rollback["current_generation"] == 2
    assert rollback["previous"]["generation"] == 1
    assert rollback["previous"]["master"]["path"] == str(first / "master.ged")
    assert (first / "master.ged").is_file()

    with patch("ancestryllm.gedcom.incremental.os.replace", side_effect=OSError("disk full")):
        assert (
            run_sync(_update_args(releases, second, _snapshot("myheritage-main", "myheritage", 2)))
            == 7
        )

    assert sorted(path.name[:5] for path in releases.glob("g*-*")) == ["g0001", "g0002"]
    assert not list(releases.glob(".gedcom-sync-*"))
