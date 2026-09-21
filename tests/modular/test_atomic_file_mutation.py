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
