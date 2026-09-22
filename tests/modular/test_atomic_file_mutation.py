"""Crash recovery of one atomic replacement through the shared coordinator."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from ancestryllm.application.mutations import MutationState
from ancestryllm.core.atomic_file import AtomicFileMutation
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.mutation import LocalMutationCoordinator


@pytest.mark.parametrize("boundary", ["created", "staged", "committing"])
def test_atomic_publication_rejects_new_stage_hard_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    target = tmp_path / "settings"
    target.write_bytes(b"old settings")
    alias = tmp_path / "external-alias"

    def link_stage(self: AtomicFileMutation, reached: str) -> None:
        if reached == boundary:
            os.link(self._stage, alias)

    monkeypatch.setattr(AtomicFileMutation, "_checkpoint", link_stage)
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        with (
            pytest.raises(AncestryError, match="requires recovery"),
            AtomicFileMutation(target, coordinator) as mutation,
        ):
            mutation.publish(b"new settings")
        assert target.read_bytes() == b"old settings"
        assert alias.exists()


@pytest.mark.parametrize("boundary", ["opened", "read"])
def test_fingerprint_rejects_hard_link_created_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    from ancestryllm.core import atomic_file

    target = tmp_path / "stage"
    target.write_bytes(b"fictional stage")
    alias = tmp_path / "alias"
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        original = atomic_file._open_fingerprint_descriptor if boundary == "opened" else os.read

        def add_link(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)  # type: ignore[arg-type]
            if not alias.exists():
                os.link(target, alias)
            return result

        monkeypatch.setattr(
            atomic_file if boundary == "opened" else os,
            "_open_fingerprint_descriptor" if boundary == "opened" else "read",
            add_link,
        )
        with pytest.raises(AncestryError, match="requires recovery"):
            atomic_file._fingerprint(coordinator, target)


def test_atomic_publication_without_posix_fchmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(os, "fchmod", raising=False)
    target = tmp_path / "settings"
    content = b"fictional\r\nsettings\x1a\n"
    with (
        LocalMutationCoordinator(tmp_path / "journal") as coordinator,
        AtomicFileMutation(target, coordinator) as mutation,
    ):
        assert mutation.publish(content).state is MutationState.COMMITTED
    assert target.read_bytes() == content


def _terminate_at(namespace: str, target: str, boundary: str) -> None:
    def terminate(self: AtomicFileMutation, reached: str) -> None:
        if reached == boundary:
            os._exit(29)

    AtomicFileMutation._checkpoint = terminate
    with (
        LocalMutationCoordinator(Path(namespace)) as coordinator,
        AtomicFileMutation(Path(target), coordinator) as mutation,
    ):
        mutation.publish(b"new fictional settings")


@pytest.mark.parametrize(
    "boundary",
    ["acquired", "prepared", "created", "staged", "committing", "published", "committed"],
)
@pytest.mark.parametrize("existing", [False, True])
def test_restart_reconciles_one_complete_file(
    tmp_path: Path, boundary: str, existing: bool
) -> None:
    target = tmp_path / "settings.toml"
    if existing:
        target.write_bytes(b"previous fictional settings")
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"preserve me")
    namespace = tmp_path / "journal"
    process = multiprocessing.get_context("spawn").Process(
        target=_terminate_at, args=(str(namespace), str(target), boundary)
    )
    process.start()
    process.join(15)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("writer did not reach its termination boundary")
    assert process.exitcode == 29
    with LocalMutationCoordinator(namespace) as coordinator:
        AtomicFileMutation.reconcile(target, coordinator)
        assert coordinator.interrupted((target,)) == ()
    if boundary in {"published", "committed"}:
        assert target.read_bytes() == b"new fictional settings"
    elif existing:
        assert target.read_bytes() == b"previous fictional settings"
    else:
        assert not target.exists()
    assert unrelated.read_bytes() == b"preserve me"
    assert not list(tmp_path.glob(".ancestry-mutation-*"))
    for file in namespace.rglob("*"):
        if file.is_file():
            contents = file.read_bytes()
            assert b"settings.toml" not in contents
            assert b"fictional settings" not in contents
            assert str(tmp_path).encode() not in contents


def test_exception_before_publication_preserves_old_file(tmp_path: Path) -> None:
    target = tmp_path / "settings"
    target.write_bytes(b"old")
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        with pytest.raises(RuntimeError), AtomicFileMutation(target, coordinator):
            raise RuntimeError("validation failed")
        assert coordinator.interrupted((target,)) == ()
    assert target.read_bytes() == b"old"


def test_recovery_preserves_unrelated_target_replacement(tmp_path: Path) -> None:
    namespace = tmp_path / "journal"
    target = tmp_path / "settings"
    target.write_bytes(b"old")
    with LocalMutationCoordinator(namespace) as coordinator:
        mutation = AtomicFileMutation(target, coordinator)
        mutation.__enter__()
    target.unlink()
    target.write_bytes(b"unrelated replacement")
    with LocalMutationCoordinator(namespace) as coordinator:
        with pytest.raises(AncestryError) as failure:
            AtomicFileMutation.reconcile(target, coordinator)
        assert failure.value.code == "MUTATION_RECOVERY_REQUIRED"
        assert coordinator.interrupted((target,))
    assert target.read_bytes() == b"unrelated replacement"


def test_completed_rename_records_commit_after_lease_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ancestryllm.core import mutation as coordinator_module

    def expire_after_rename(self: AtomicFileMutation, boundary: str) -> None:
        if boundary == "published":
            assert self.lease is not None
            expired = self.lease.expires_ms + 1
            monkeypatch.setattr(coordinator_module, "_now_ms", lambda: expired)

    monkeypatch.setattr(AtomicFileMutation, "_checkpoint", expire_after_rename)
    target = tmp_path / "settings"
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        with AtomicFileMutation(target, coordinator) as mutation:
            outcome = mutation.publish(b"complete")
        assert outcome.state is MutationState.COMMITTED
        assert coordinator.interrupted((target,)) == ()
    assert target.read_bytes() == b"complete"


def test_windows_identity_survives_creation_time_tunneling(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ancestryllm.core import atomic_file

    monkeypatch.setattr(atomic_file, "os", SimpleNamespace(name="nt"))
    before = SimpleNamespace(st_dev=7, st_ino=19, st_birthtime_ns=100)
    after = SimpleNamespace(st_dev=7, st_ino=19, st_birthtime_ns=200)
    replaced = SimpleNamespace(st_dev=7, st_ino=20, st_birthtime_ns=200)
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        assert atomic_file._identity(coordinator, before) == atomic_file._identity(
            coordinator, after
        )
        assert atomic_file._identity(coordinator, before) != atomic_file._identity(
            coordinator, replaced
        )


def _fingerprint_fifo_race(namespace: str, filename: str) -> None:
    from ancestryllm.core import atomic_file

    path = Path(filename)
    original = atomic_file._open_fingerprint_descriptor

    def replace_with_fifo(selected: Path) -> int:
        selected.unlink()
        os.mkfifo(selected)
        return original(selected)

    atomic_file._open_fingerprint_descriptor = replace_with_fifo
    with LocalMutationCoordinator(Path(namespace)) as coordinator:
        try:
            atomic_file._fingerprint(coordinator, path)
        except AncestryError as error:
            assert error.code == "MUTATION_RECOVERY_REQUIRED"
        else:
            raise AssertionError("A FIFO cannot be fingerprinted as a regular file")


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO race")
def test_fingerprint_fifo_replacement_does_not_block(tmp_path: Path) -> None:
    import stat

    target = tmp_path / "stage"
    target.write_bytes(b"fictional stage")
    process = multiprocessing.get_context("spawn").Process(
        target=_fingerprint_fifo_race, args=(str(tmp_path / "journal"), str(target))
    )
    process.start()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Fingerprint open blocked on a replacement FIFO")
    assert process.exitcode == 0
    assert stat.S_ISFIFO(target.lstat().st_mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("boundary", ["staged", "committing"])
def test_atomic_publication_rejects_sealed_permission_change(tmp_path, monkeypatch, boundary):
    target = tmp_path / "settings"
    target.write_bytes(b"old")

    def change_mode(self, reached):
        if reached == boundary:
            self._stage.chmod(0o644)

    monkeypatch.setattr(AtomicFileMutation, "_checkpoint", change_mode)
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        with (
            pytest.raises(AncestryError, match="requires recovery"),
            AtomicFileMutation(target, coordinator) as mutation,
        ):
            mutation.publish(b"new")
        assert coordinator.interrupted((target,))
    assert target.read_bytes() == b"old"


def test_atomic_abort_preserves_stage_replaced_after_fingerprint(tmp_path, monkeypatch):
    from ancestryllm.core import atomic_file

    target = tmp_path / "settings"
    original = atomic_file._fingerprint
    stage = None

    def stop(self, boundary):
        nonlocal stage
        if boundary == "staged":
            stage = self._stage
            raise RuntimeError("abort before publication")

    def swap_after_check(coordinator, path):
        result = original(coordinator, path)
        if path == stage:
            replacement = tmp_path / "foreign"
            replacement.write_bytes(b"foreign bytes")
            replacement.replace(path)
        return result

    monkeypatch.setattr(AtomicFileMutation, "_checkpoint", stop)
    monkeypatch.setattr(atomic_file, "_fingerprint", swap_after_check)
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        with (
            pytest.raises(AncestryError, match="requires recovery"),
            AtomicFileMutation(target, coordinator) as mutation,
        ):
            mutation.publish(b"new")
        assert stage is not None
        assert stage.read_bytes() == b"foreign bytes"
        assert coordinator.interrupted((target,))
    assert not target.exists()
