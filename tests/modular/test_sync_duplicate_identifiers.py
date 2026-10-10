"""Verify sync rejects structurally ambiguous GEDCOM inputs before publication."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ancestryllm.gedcom import sync_contracts
from ancestryllm.gedcom.sync import execute_sync

if TYPE_CHECKING:
    from pathlib import Path


def _write_people(path: Path, *people: tuple[str, str, str]) -> bytes:
    """Write a small fictional GEDCOM and return its original bytes."""

    lines = [
        "0 HEAD",
        "1 SOUR ANCESTRYLLM-FICTIONAL-TEST",
        "1 GEDC",
        "2 VERS 5.5.5",
        "2 FORM LINEAGE-LINKED",
        "1 CHAR UTF-8",
    ]
    for identifier, name, birth in people:
        lines.extend(
            (
                f"0 @{identifier}@ INDI",
                f"1 NAME {name}",
                "1 BIRT",
                f"2 DATE {birth}",
            )
        )
    lines.append("0 TRLR")
    payload = ("\n".join(lines) + "\n").encode()
    path.write_bytes(payload)
    return payload


@pytest.mark.parametrize("duplicate_input", ("master", "snapshot"))
def test_sync_rejects_duplicate_person_identifiers_before_publication(
    tmp_path: Path,
    duplicate_input: str,
) -> None:
    """A duplicate ID in either input must fail without changing any input."""

    master = tmp_path / "fictional-master.ged"
    snapshot = tmp_path / "fictional-snapshot.ged"
    duplicate_people = (
        ("I1", "Avery /North/", "1 JAN 1900"),
        ("I1", "Blair /South/", "2 FEB 1970"),
    )
    valid_people = (("I1", "Casey /West/", "3 MAR 1930"),)
    master_before = _write_people(
        master, *(duplicate_people if duplicate_input == "master" else valid_people)
    )
    snapshot_before = _write_people(
        snapshot, *(duplicate_people if duplicate_input == "snapshot" else valid_people)
    )
    release_root = tmp_path / "releases"

    result = execute_sync(
        [
            "update",
            "--master",
            str(master),
            "--initialize-manifest",
            "--snapshot",
            f"fictional-main:other={snapshot}",
            "--exported-at",
            "fictional-main=2026-01-15",
            "--release-root",
            str(release_root),
            "--no-quality-report",
        ]
    )

    assert result.exit_code == sync_contracts.EXIT_CODES["SYNC_PARSE"]
    assert "ERROR [SYNC_PARSE]" in result.error
    assert result.committed is False
    assert not release_root.exists()
    assert master.read_bytes() == master_before
    assert snapshot.read_bytes() == snapshot_before
