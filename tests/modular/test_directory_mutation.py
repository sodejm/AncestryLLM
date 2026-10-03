"""Crash recovery of complete, exclusively published export directories."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from ancestryllm.core.errors import AncestryError
from ancestryllm.core.mutation import LocalMutationCoordinator


def test_sync_root_scope_respects_platform_name_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation

    monkeypatch.setattr(os.path, "normcase", lambda value: str(value).casefold())
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        assert DirectoryMutation._root_scope(
            tmp_path / "Releases", coordinator
        ) == DirectoryMutation._root_scope(tmp_path / "releases", coordinator)


def _terminate_directory(namespace: str, target: str, boundary: str) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation
    from ancestryllm.gedcom.sync_publication import _exclusive_rename_directory

    def terminate(self: DirectoryMutation, reached: str) -> None:
        if reached == boundary:
            os._exit(37)

    DirectoryMutation._checkpoint = terminate
    destination = Path(target)
    stage = destination.parent / (".gedcom-sync-" + "a" * 32)
    stage.mkdir(mode=0o700)
    (stage / "master.ged").write_bytes(b"fictional GEDCOM")
    (stage / "manifest.json").write_bytes(b"fictional manifest")
    marker = ".ancestryllm-staging-" + "b" * 32
    (stage / marker).write_bytes(b"private ownership marker")
    with LocalMutationCoordinator(Path(namespace)) as coordinator:
        mutation = DirectoryMutation(destination, coordinator)
        mutation.acquire()
        mutation.prepare(stage, marker)
        if boundary.startswith("cleanup"):
            mutation.finish()
            return
        mutation.committing()
        _exclusive_rename_directory(stage, destination)
        mutation._checkpoint("published")
        (destination / marker).unlink()
        mutation._checkpoint("finalized")
        mutation.finish()


@pytest.mark.parametrize(
    "boundary",
    [
        "prepared",
        "committing",
        "published",
        "finalized",
        "committed",
        "cleanup_started",
        "cleanup_file",
        "cleanup_directory",
    ],
)
def test_directory_restart_records_complete_outcome(tmp_path: Path, boundary: str) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation

    destination = tmp_path / "export"
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"preserved")
    namespace = tmp_path / "journal"
    process = multiprocessing.get_context("spawn").Process(
        target=_terminate_directory, args=(str(namespace), str(destination), boundary)
    )
    process.start()
    process.join(20)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Publisher did not reach its requested crash boundary")
    assert process.exitcode == 37
    with LocalMutationCoordinator(namespace) as coordinator:
        DirectoryMutation.reconcile(destination, coordinator)
        assert coordinator.interrupted((destination,)) == ()
        DirectoryMutation.reconcile(destination, coordinator)
    if boundary in {"published", "finalized", "committed"}:
        assert sorted(path.name for path in destination.iterdir()) == [
            "manifest.json",
            "master.ged",
        ]
        assert (destination / "master.ged").read_bytes() == b"fictional GEDCOM"
    else:
        assert not destination.exists()
    assert unrelated.read_bytes() == b"preserved"
    assert not list(tmp_path.glob(".gedcom-sync-*"))
    for path in namespace.rglob("*"):
        if path.is_file():
            contents = path.read_bytes()
            assert b"fictional" not in contents
            assert b"master.ged" not in contents
            assert str(tmp_path).encode() not in contents


def test_directory_recovery_preserves_changed_contents(tmp_path: Path) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation

    destination = tmp_path / "export"
    stage = tmp_path / (".gedcom-sync-" + "c" * 32)
    stage.mkdir()
    (stage / "master.ged").write_bytes(b"original")
    namespace = tmp_path / "journal"
    with LocalMutationCoordinator(namespace) as coordinator:
        mutation = DirectoryMutation(destination, coordinator)
        mutation.acquire()
        mutation.prepare(stage)
    (stage / "master.ged").write_bytes(b"unrelated replacement")
    with LocalMutationCoordinator(namespace) as coordinator:
        with pytest.raises(AncestryError, match="requires recovery"):
            DirectoryMutation.reconcile(destination, coordinator)
        assert coordinator.interrupted((destination,))
    assert (stage / "master.ged").read_bytes() == b"unrelated replacement"


def test_sync_root_is_reserved_before_staging_and_rebound_on_restart(tmp_path: Path) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation

    namespace = tmp_path / "journal"
    target = tmp_path / "g0001-20260920T120000Z"
    later = tmp_path / "g0001-20260920T120001Z"
    with (
        LocalMutationCoordinator(namespace) as first,
        LocalMutationCoordinator(namespace) as second,
    ):
        mutation = DirectoryMutation(target, first, sync_root=True)
        mutation.acquire()
        assert mutation.lease is not None
        interrupted_id = mutation.lease.operation_id
        with pytest.raises(AncestryError):
            DirectoryMutation(later, second, sync_root=True).acquire()
    with LocalMutationCoordinator(namespace) as coordinator:
        retry = DirectoryMutation(later, coordinator, sync_root=True)
        retry.acquire()
        with coordinator._transaction() as database:
            row = database.execute(
                "SELECT state,outcome FROM operations WHERE operation_id=?", (interrupted_id,)
            ).fetchone()
            assert row["state"] == "aborted"
            assert row["outcome"] is not None
        retry.finish()
    assert not target.exists()
    assert not later.exists()


def test_unfinished_staging_retains_recovery_barrier(tmp_path: Path) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation

    namespace = tmp_path / "journal"
    destination = tmp_path / "export"
    stage = tmp_path / (".gedcom-sync-" + "d" * 32)
    stage.mkdir()
    with LocalMutationCoordinator(namespace) as coordinator:
        mutation = DirectoryMutation(destination, coordinator)
        mutation.acquire()
        mutation.track_staging(stage)
        (stage / "unfinished.ged").write_bytes(b"fictional partial data")
    with LocalMutationCoordinator(namespace) as coordinator:
        with pytest.raises(AncestryError, match="requires recovery"):
            DirectoryMutation.reconcile(destination, coordinator)
        assert coordinator.interrupted((destination,))
        with pytest.raises(AncestryError):
            DirectoryMutation(destination, coordinator).acquire()
    assert (stage / "unfinished.ged").read_bytes() == b"fictional partial data"
    assert not destination.exists()


def test_restart_removes_empty_staging(tmp_path: Path) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation

    namespace = tmp_path / "journal"
    destination = tmp_path / "export"
    stage = tmp_path / (".gedcom-sync-" + "e" * 32)
    stage.mkdir()
    with LocalMutationCoordinator(namespace) as coordinator:
        mutation = DirectoryMutation(destination, coordinator)
        mutation.acquire()
        mutation.track_staging(stage)
        with pytest.raises(AncestryError, match="requires recovery"):
            mutation.committing()
    with LocalMutationCoordinator(namespace) as coordinator:
        DirectoryMutation.reconcile(destination, coordinator)
        assert coordinator.interrupted((destination,)) == ()
    assert not stage.exists()


@pytest.mark.parametrize("replace_member", [False, True])
def test_partial_owned_member_recovery(tmp_path: Path, replace_member: bool) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation

    destination = tmp_path / "export"
    stage = tmp_path / (".ancestry-export-" + "f" * 32)
    stage.mkdir()
    member = stage / "unfinished.ged"
    namespace = tmp_path / "journal"
    with LocalMutationCoordinator(namespace) as coordinator:
        mutation = DirectoryMutation(destination, coordinator)
        mutation.acquire()
        mutation.track_staging(stage)
        with member.open("xb") as handle:
            mutation.track_member(member, handle.fileno())
            handle.write(b"fictional partial bytes")
    if replace_member:
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"unrelated bytes")
        replacement.replace(member)
    with LocalMutationCoordinator(namespace) as coordinator:
        if replace_member:
            with pytest.raises(AncestryError, match="requires recovery"):
                DirectoryMutation.reconcile(destination, coordinator)
            assert member.read_bytes() == b"unrelated bytes"
            assert coordinator.interrupted((destination,))
        else:
            DirectoryMutation.reconcile(destination, coordinator)
            DirectoryMutation.reconcile(destination, coordinator)
            assert coordinator.interrupted((destination,)) == ()
            assert not stage.exists()
    assert not destination.exists()


@pytest.mark.parametrize("change", ["unknown", "replaced", "modified", "unfinished", "linked"])
def test_preparation_rejects_unowned_or_unsealed_members(tmp_path: Path, change: str) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation

    stage = tmp_path / (".ancestry-export-" + "a" * 32)
    stage.mkdir()
    member = stage / "master.ged"
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        mutation = DirectoryMutation(tmp_path / "export", coordinator)
        mutation.acquire()
        mutation.track_staging(stage)
        with member.open("xb") as handle:
            mutation.track_member(member, handle.fileno())
            handle.write(b"fictional original")
        if change != "unfinished":
            mutation.finish_member(member)
        if change == "unknown":
            (stage / "unrelated").write_bytes(b"unrelated")
        elif change == "replaced":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"unrelated replacement")
            replacement.replace(member)
        elif change == "modified":
            member.write_bytes(b"modified")
        elif change == "linked":
            os.link(member, tmp_path / "external-alias")
        with pytest.raises(AncestryError, match="requires recovery"):
            mutation.prepare(stage)
        assert not (tmp_path / "export").exists()
        assert member.exists()


@pytest.mark.parametrize("names", [("Releases", "releases"), ("Caf\u00e9", "Cafe\u0301")])
def test_missing_sync_root_aliases_contend(tmp_path: Path, names: tuple[str, str]) -> None:
    from ancestryllm.core.directory_mutation import DirectoryMutation

    namespace = tmp_path / "journal"
    with (
        LocalMutationCoordinator(namespace) as first,
        LocalMutationCoordinator(namespace) as second,
    ):
        mutation = DirectoryMutation(
            tmp_path / names[0] / "g0001-20260921T120000Z", first, sync_root=True
        )
        mutation.acquire()
        alias = DirectoryMutation(
            tmp_path / names[1] / "g0001-20260921T120000Z", second, sync_root=True
        )
        with pytest.raises(AncestryError) as failure:
            alias.acquire()
        assert failure.value.code == "MUTATION_CONFLICT"


def test_staging_flush_uses_shared_writable_descriptor(tmp_path, monkeypatch):
    from ancestryllm.core import directory_mutation

    stage = tmp_path / (".gedcom-sync-" + "d" * 32)
    stage.mkdir()
    marker = ".ancestryllm-staging-" + "e" * 32
    (stage / marker).write_bytes(b"private marker")
    opened = []

    def shared_descriptor(path):
        opened.append(path)
        return os.open(path, os.O_RDWR)

    monkeypatch.setattr(
        directory_mutation, "_open_flush_descriptor", shared_descriptor, raising=False
    )
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        mutation = directory_mutation.DirectoryMutation(tmp_path / "export", coordinator)
        mutation.acquire()
        mutation.prepare(stage, marker)
        assert opened == [stage / marker]
        mutation.finish()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_directory_rejects_permission_change_after_sealing(tmp_path):
    from ancestryllm.core.directory_mutation import DirectoryMutation

    stage = tmp_path / (".ancestry-export-" + "f" * 32)
    stage.mkdir()
    member = stage / "master.ged"
    member.write_bytes(b"fictional GEDCOM")
    member.chmod(0o600)
    target = tmp_path / "export"
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        mutation = DirectoryMutation(target, coordinator)
        mutation.acquire()
        mutation.prepare(stage)
        member.chmod(0o644)
        with pytest.raises(AncestryError, match="requires recovery"):
            mutation.committing()
        assert coordinator.interrupted((target,))
    assert not target.exists()
