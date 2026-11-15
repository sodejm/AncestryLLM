"""Verify sync rejects structurally ambiguous GEDCOM inputs before publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ancestryllm.core.cancellation import CancellationError
from ancestryllm.core.ingress import FileFingerprint, FileIngressPolicy, FileKind
from ancestryllm.gedcom import sync_contracts
from ancestryllm.gedcom.sync import execute_sync


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


def _release_files(release_root: Path) -> dict[str, bytes]:
    """Capture the immutable publication surface for comparison after a failure."""

    return {
        path.relative_to(release_root).as_posix(): path.read_bytes()
        for path in release_root.rglob("*")
        if path.is_file()
    }


def _initial_update_args(master: Path, snapshot: Path, release_root: Path) -> list[str]:
    """Build CLI arguments for one fictional initialization update."""

    return [
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


def _rebase_args(master: Path, generation: Path, release_root: Path) -> list[str]:
    """Build CLI arguments for one fictional manual rebase."""

    return [
        "rebase",
        "--master",
        str(master),
        "--manifest",
        str(generation / "manifest.json"),
        "--release-root",
        str(release_root),
        "--reason",
        "Fictional duplicate-identifier regression",
        "--accept-manual-deletions",
    ]


def _initialize_release(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Publish one valid fictional generation and return its inputs and path."""

    master = tmp_path / "fictional-master.ged"
    snapshot = tmp_path / "fictional-snapshot.ged"
    _write_people(master, ("I1", "Avery /North/", "1 JAN 1900"))
    _write_people(snapshot, ("I1", "Blair /South/", "2 FEB 1970"))
    release_root = tmp_path / "releases"
    initialized = execute_sync(_initial_update_args(master, snapshot, release_root))
    assert initialized.exit_code == 0
    return snapshot, release_root, next(release_root.glob("g0001-*"))


class _CancelAfterFinalManifestHash(FileIngressPolicy):
    """Request cancellation only after the idempotent manifest rehash."""

    def __init__(self, manifest: Path) -> None:
        super().__init__()
        self.manifest = manifest.absolute()
        self.armed = False
        self.cancellation_requested = False

    def verify(
        self,
        path: str | Path,
        kind: FileKind,
        expected: FileFingerprint,
    ) -> None:
        super().verify(path, kind, expected)
        if self.armed and Path(path).absolute() == self.manifest:
            self.cancellation_requested = True


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

    result = execute_sync(_initial_update_args(master, snapshot, release_root))

    assert result.exit_code == sync_contracts.EXIT_CODES["SYNC_PARSE"]
    assert "ERROR [SYNC_PARSE]" in result.error
    assert result.committed is False
    assert not release_root.exists()
    assert master.read_bytes() == master_before
    assert snapshot.read_bytes() == snapshot_before


def test_valid_sync_keeps_distinct_people_and_idempotent_retry_is_read_only(
    tmp_path: Path,
) -> None:
    """Repeated source-local IDs must not merge distinct people or republish on retry."""

    master = tmp_path / "fictional-master.ged"
    snapshot = tmp_path / "fictional-snapshot.ged"
    master_before = _write_people(master, ("I1", "Avery /North/", "1 JAN 1900"))
    snapshot_before = _write_people(snapshot, ("I1", "Blair /South/", "2 FEB 1970"))
    release_root = tmp_path / "releases"

    first = execute_sync(_initial_update_args(master, snapshot, release_root))

    assert first.exit_code == 0
    assert first.committed is True
    generation = next(release_root.glob("g0001-*"))
    published_master = (generation / "master.ged").read_text(encoding="utf-8")
    assert published_master.count(" INDI\n") == 2
    assert "1 NAME Avery /North/\n" in published_master
    assert "1 NAME Blair /South/\n" in published_master
    published_before_retry = _release_files(release_root)

    retry = execute_sync(
        [
            "update",
            "--master",
            str(generation / "master.ged"),
            "--manifest",
            str(generation / "manifest.json"),
            "--snapshot",
            f"fictional-main:other={snapshot}",
            "--exported-at",
            "fictional-main=2026-01-15",
            "--release-root",
            str(release_root),
            "--no-quality-report",
        ]
    )

    assert retry.exit_code == 0
    assert retry.committed is False
    assert "No update was needed" in retry.output
    assert len(list(release_root.glob("g*-*"))) == 1
    assert _release_files(release_root) == published_before_retry
    assert master.read_bytes() == master_before
    assert snapshot.read_bytes() == snapshot_before


def test_rebase_rejects_duplicate_identifiers_in_current_master_before_publication(
    tmp_path: Path,
) -> None:
    """An ambiguous edited master must not create a manual-rebase generation."""

    _, release_root, generation = _initialize_release(tmp_path)
    edited = tmp_path / "fictional-edited-master.ged"
    edited_before = _write_people(
        edited,
        ("I1", "Casey /West/", "3 MAR 1930"),
        ("I1", "Drew /East/", "4 APR 1940"),
    )
    published_before = _release_files(release_root)

    rejected = execute_sync(_rebase_args(edited, generation, release_root))

    assert rejected.exit_code == sync_contracts.EXIT_CODES["SYNC_PARSE"]
    assert "ERROR [SYNC_PARSE]" in rejected.error
    assert rejected.committed is False
    assert len(list(release_root.glob("g*-*"))) == 1
    assert _release_files(release_root) == published_before
    assert edited.read_bytes() == edited_before


def test_rebase_rejects_duplicate_identifiers_in_previous_master_before_publication(
    tmp_path: Path,
) -> None:
    """A coherent legacy bundle with ambiguous IDs must not become a rebase parent."""

    _, release_root, generation = _initialize_release(tmp_path)
    previous_master = generation / "master.ged"
    previous_bytes = _write_people(
        previous_master,
        ("I1", "Emery /Lake/", "5 MAY 1950"),
        ("I1", "Finley /Hill/", "6 JUN 1960"),
    )
    previous_sha = hashlib.sha256(previous_bytes).hexdigest()
    manifest_path = generation / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["master"]["sha256"] = previous_sha
    manifest["releases"][-1]["master_sha256"] = previous_sha
    manifest["artifact_checksums"]["master.ged"] = previous_sha
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    edited = tmp_path / "fictional-valid-edited-master.ged"
    edited_before = _write_people(edited, ("I9", "Gray /Field/", "7 JUL 1970"))
    published_before = _release_files(release_root)

    rejected = execute_sync(_rebase_args(edited, generation, release_root))

    assert rejected.exit_code == sync_contracts.EXIT_CODES["SYNC_PARSE"]
    assert "ERROR [SYNC_PARSE]" in rejected.error
    assert rejected.committed is False
    assert len(list(release_root.glob("g*-*"))) == 1
    assert _release_files(release_root) == published_before
    assert edited.read_bytes() == edited_before


def test_rebase_accepts_distinct_identifiers_and_preserves_parent_generation(
    tmp_path: Path,
) -> None:
    """Valid manual edits still publish while the immutable parent stays unchanged."""

    _, release_root, generation = _initialize_release(tmp_path)
    edited = tmp_path / "fictional-valid-edited-master.ged"
    edited.write_text(
        (generation / "master.ged")
        .read_text(encoding="utf-8")
        .replace(
            "0 TRLR\n",
            "0 @I99@ INDI\n1 NAME Harper /Vale/\n1 BIRT\n2 DATE 8 AUG 1980\n0 TRLR\n",
        ),
        encoding="utf-8",
    )
    parent_before = _release_files(generation)

    rebased = execute_sync(_rebase_args(edited, generation, release_root))

    assert rebased.exit_code == 0
    assert rebased.committed is True
    assert len(list(release_root.glob("g*-*"))) == 2
    assert _release_files(generation) == parent_before
    next_generation = next(release_root.glob("g0002-*"))
    assert "0 @I99@ INDI\n" in (next_generation / "master.ged").read_text(encoding="utf-8")


def test_idempotent_retry_observes_cancellation_requested_during_final_hash(
    tmp_path: Path,
) -> None:
    """The read-only result must not hide cancellation requested by final verification."""

    master = tmp_path / "fictional-master.ged"
    snapshot = tmp_path / "fictional-snapshot.ged"
    _write_people(master, ("I1", "Avery /North/", "1 JAN 1900"))
    snapshot_before = _write_people(snapshot, ("I1", "Blair /South/", "2 FEB 1970"))
    release_root = tmp_path / "releases"
    first = execute_sync(_initial_update_args(master, snapshot, release_root))
    assert first.exit_code == 0
    generation = next(release_root.glob("g0001-*"))
    manifest = generation / "manifest.json"
    published_before_retry = _release_files(release_root)
    ingress = _CancelAfterFinalManifestHash(manifest)
    checkpoint_calls = 0

    def cancellation_check() -> None:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if ingress.cancellation_requested:
            raise CancellationError("fictional cancellation during final input verification")
        if checkpoint_calls == 2:
            ingress.armed = True

    with pytest.raises(CancellationError, match="final input verification"):
        execute_sync(
            [
                "update",
                "--master",
                str(generation / "master.ged"),
                "--manifest",
                str(manifest),
                "--snapshot",
                f"fictional-main:other={snapshot}",
                "--exported-at",
                "fictional-main=2026-01-15",
                "--release-root",
                str(release_root),
                "--no-quality-report",
            ],
            ingress=ingress,
            cancellation_check=cancellation_check,
            raise_errors=True,
        )

    assert ingress.cancellation_requested is True
    assert checkpoint_calls == 3
    assert len(list(release_root.glob("g*-*"))) == 1
    assert _release_files(release_root) == published_before_retry
    assert snapshot.read_bytes() == snapshot_before


@pytest.mark.parametrize("duplicate_input", ("master", "snapshot"))
def test_duplicate_input_preserves_previous_generation_and_manifest(
    tmp_path: Path,
    duplicate_input: str,
) -> None:
    """An invalid input must leave the last complete generation byte-identical."""

    master = tmp_path / "fictional-master.ged"
    valid_snapshot = tmp_path / "fictional-snapshot-v1.ged"
    duplicate_people = tmp_path / "fictional-duplicate-input.ged"
    _write_people(master, ("I1", "Avery /North/", "1 JAN 1900"))
    _write_people(valid_snapshot, ("I1", "Blair /South/", "2 FEB 1970"))
    duplicate_before = _write_people(
        duplicate_people,
        ("I1", "Casey /West/", "3 MAR 1930"),
        ("I1", "Drew /East/", "4 APR 1940"),
    )
    release_root = tmp_path / "releases"
    first = execute_sync(_initial_update_args(master, valid_snapshot, release_root))
    assert first.exit_code == 0
    generation = next(release_root.glob("g0001-*"))
    published_before_failure = _release_files(release_root)

    if duplicate_input == "master":
        rejected_args = _initial_update_args(
            duplicate_people,
            valid_snapshot,
            release_root,
        )
    else:
        rejected_args = [
            "update",
            "--master",
            str(generation / "master.ged"),
            "--manifest",
            str(generation / "manifest.json"),
            "--snapshot",
            f"fictional-main:other={duplicate_people}",
            "--exported-at",
            "fictional-main=2026-02-15",
            "--release-root",
            str(release_root),
            "--no-quality-report",
        ]

    rejected = execute_sync(rejected_args)

    assert rejected.exit_code == sync_contracts.EXIT_CODES["SYNC_PARSE"]
    assert rejected.committed is False
    assert len(list(release_root.glob("g*-*"))) == 1
    assert _release_files(release_root) == published_before_failure
    assert duplicate_people.read_bytes() == duplicate_before
