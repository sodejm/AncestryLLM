"""Durable ownership shared by otherwise independent application processes."""

from __future__ import annotations

import multiprocessing
import os
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from ancestryllm.application.mutations import (
    MutationLease,
    MutationOutcome,
    MutationRequest,
    MutationState,
    MutationTransition,
)
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.mutation import LocalMutationCoordinator


def test_namespace_key_preserves_binary_control_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = tmp_path / "journal"
    key = b"\r\n\x1a" + bytes(range(29))
    monkeypatch.setattr(os, "urandom", lambda size: key if size == 32 else bytes(size))
    with LocalMutationCoordinator(namespace) as coordinator:
        expected = coordinator._digest("fictional resource")
    assert (namespace / "namespace.key").read_bytes() == key
    with LocalMutationCoordinator(namespace) as reopened:
        assert reopened._digest("fictional resource") == expected


def request(coordinator: LocalMutationCoordinator, path: Path) -> MutationRequest:
    return MutationRequest(
        operation_id=uuid4().hex,
        resources=coordinator.bind((path,)),
        intent_digest="a" * 64,
        idempotency_key=uuid4().hex + uuid4().hex,
        owner_id=uuid4().hex,
        session_id=uuid4().hex,
        deadline_ms=int(time.time() * 1000) + 60_000,
        lease_ms=30_000,
        artifacts=(),
    )


def _contender(namespace: str, path: str, connection: object) -> None:
    # Spawn, rather than fork, proves that no inherited Python lock is involved.
    with LocalMutationCoordinator(Path(namespace)) as coordinator:
        try:
            result = coordinator.acquire(request(coordinator, Path(path)))
            coordinator.transition(result, MutationTransition(MutationState.ABORTED, ()))
            code = "acquired"
        except AncestryError as exc:
            code = exc.code
        connection.send(code)  # type: ignore[attr-defined]
        connection.close()  # type: ignore[attr-defined]


def contend(namespace: Path, path: Path) -> str:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_contender, args=(str(namespace), str(path), sender))
    process.start()
    sender.close()
    try:
        assert receiver.poll(15), "contender failed to answer"
        result = receiver.recv()
        process.join(15)
        assert process.exitcode == 0
        return str(result)
    finally:
        receiver.close()
        if process.is_alive():
            process.kill()
            process.join()


def test_process_conflicts_but_independent_resources_proceed(tmp_path: Path) -> None:
    namespace = tmp_path / "journal"
    with LocalMutationCoordinator(namespace) as coordinator:
        first = request(coordinator, tmp_path / "first")
        lease = coordinator.acquire(first)
        assert contend(namespace, tmp_path / "first") == "MUTATION_CONFLICT"
        assert contend(namespace, tmp_path / "second") == "acquired"
        coordinator.transition(lease, MutationTransition(MutationState.ABORTED, ()))
    assert contend(namespace, tmp_path / "first") == "acquired"


def test_aliases_share_ownership(tmp_path: Path) -> None:
    original = tmp_path / "source"
    original.write_bytes(b"fictional")
    alias = tmp_path / "alias"
    alias.hardlink_to(original)
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        lease = coordinator.acquire(request(coordinator, original))
        assert contend(tmp_path / "journal", alias) == "MUTATION_CONFLICT"
        coordinator.transition(lease, MutationTransition(MutationState.ABORTED, ()))


def test_revision_is_checked_before_preparing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"old")
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        operation = request(coordinator, source)
        source.write_bytes(b"changed")
        with pytest.raises(AncestryError, match="revision") as failure:
            coordinator.acquire(operation)
        assert failure.value.code == "MUTATION_REVISION_STALE"


def test_recorded_outcome_and_mismatched_retry(tmp_path: Path) -> None:
    namespace = tmp_path / "journal"
    with LocalMutationCoordinator(namespace) as coordinator:
        operation = request(coordinator, tmp_path / "target")
        lease = coordinator.acquire(operation)
        lease = coordinator.transition(lease, MutationTransition(MutationState.COMMITTING, ()))
        outcome = coordinator.transition(lease, MutationTransition(MutationState.COMMITTED, ()))
        assert isinstance(outcome, MutationOutcome)
    with LocalMutationCoordinator(namespace) as restarted:
        assert restarted.acquire(operation) == outcome
        with pytest.raises(AncestryError) as failure:
            restarted.acquire(replace(operation, intent_digest="b" * 64))
        assert failure.value.code == "MUTATION_IDEMPOTENCY_MISMATCH"
    assert MutationRequest.from_json(operation.to_json()) == operation


@pytest.mark.parametrize("terminal", [MutationState.COMMITTED, MutationState.ABORTED])
def test_matching_retry_completed_between_lookup_and_lock_returns_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: MutationState
) -> None:
    from ancestryllm.core import mutation

    namespace, target = tmp_path / "journal", tmp_path / "target"
    with LocalMutationCoordinator(namespace) as retry, LocalMutationCoordinator(namespace) as owner:
        operation = request(retry, target)
        owner.bind((target,))
        original_lock = mutation._lock
        outcome = None

        def complete_before_lock(path: Path) -> int:
            nonlocal outcome
            monkeypatch.setattr(mutation, "_lock", original_lock)
            lease = owner.acquire(operation)
            if terminal is MutationState.COMMITTED:
                lease = owner.transition(lease, MutationTransition(MutationState.COMMITTING, ()))
            outcome = owner.transition(lease, MutationTransition(terminal, ()))
            return original_lock(path)

        monkeypatch.setattr(mutation, "_lock", complete_before_lock)
        assert retry.acquire(operation) == outcome
        assert isinstance(outcome, MutationOutcome)
        # A terminal retry must release every lock without retaining a lease.
        assert not retry._held
        assert contend(namespace, target) == "acquired"


def test_expired_owner_remains_exclusive_and_is_fenced(tmp_path: Path) -> None:
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        operation = replace(request(coordinator, tmp_path / "target"), lease_ms=1)
        lease = coordinator.acquire(operation)
        time.sleep(0.02)
        assert contend(tmp_path / "journal", tmp_path / "target") == "MUTATION_CONFLICT"
        with pytest.raises(AncestryError) as failure:
            coordinator.validate(lease)
        assert failure.value.code == "MUTATION_LEASE_EXPIRED"


def test_unfinished_operation_blocks_until_authorized_recovery(tmp_path: Path) -> None:
    namespace = tmp_path / "journal"
    target = tmp_path / "target"
    with LocalMutationCoordinator(namespace) as coordinator:
        operation = request(coordinator, target)
        previous = coordinator.acquire(operation)
    with LocalMutationCoordinator(namespace) as restarted:
        unrelated = request(restarted, target)
        with pytest.raises(AncestryError) as failure:
            restarted.acquire(unrelated)
        assert failure.value.code == "MUTATION_RECOVERY_REQUIRED"
        lease = restarted.recover(operation)
        assert lease.fence > previous.fence
        with pytest.raises(AncestryError):
            restarted.validate(previous)
        restarted.transition(lease, MutationTransition(MutationState.ABORTED, ()))
    assert contend(namespace, target) == "acquired"


def test_journal_does_not_contain_private_paths(tmp_path: Path) -> None:
    namespace = tmp_path / "journal"
    private = tmp_path / "private-family-name.rmtree"
    with LocalMutationCoordinator(namespace) as coordinator:
        lease = coordinator.acquire(request(coordinator, private))
        coordinator.transition(lease, MutationTransition(MutationState.ABORTED, ()))
    for file in namespace.rglob("*"):
        if file.is_file():
            assert b"private-family-name" not in file.read_bytes()
            assert str(tmp_path).encode() not in file.read_bytes()


def _interrupted_owner(namespace: str, target: str, committing: bool, connection: object) -> None:
    coordinator = LocalMutationCoordinator(Path(namespace))
    operation = request(coordinator, Path(target))
    lease = coordinator.acquire(operation)
    if committing:
        lease = coordinator.transition(lease, MutationTransition(MutationState.COMMITTING, ()))
    connection.send(operation.to_json())  # type: ignore[attr-defined]
    connection.close()  # type: ignore[attr-defined]
    os._exit(23)


@pytest.mark.parametrize("committing", [False, True])
def test_killed_owner_requires_rebinding_before_recovery(tmp_path: Path, committing: bool) -> None:
    namespace, target = tmp_path / "journal", tmp_path / "target"
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_interrupted_owner, args=(str(namespace), str(target), committing, sender)
    )
    process.start()
    sender.close()
    try:
        assert receiver.poll(15)
        operation = MutationRequest.from_json(receiver.recv())
        process.join(15)
        assert process.exitcode == 23
    finally:
        receiver.close()
        if process.is_alive():
            process.kill()
            process.join()
    with LocalMutationCoordinator(namespace) as restarted:
        with pytest.raises(AncestryError) as failure:
            restarted.recover(operation)
        assert failure.value.code == "MUTATION_REAUTHORIZATION_REQUIRED"
        restarted.bind((target,))
        lease = restarted.recover(operation)
        assert lease.fence == 2
        outcome = restarted.transition(lease, MutationTransition(MutationState.ABORTED, ()))
        assert restarted.acquire(operation) == outcome


def test_changed_resource_is_rejected_at_commit_boundary(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("before")
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        lease = coordinator.acquire(request(coordinator, target))
        target.write_text("unexpected change")
        with pytest.raises(AncestryError) as failure:
            coordinator.transition(lease, MutationTransition(MutationState.COMMITTING, ()))
        assert failure.value.code == "MUTATION_REVISION_STALE"


def test_replaced_lock_file_fences_owner(tmp_path: Path) -> None:
    namespace = tmp_path / "journal"
    with LocalMutationCoordinator(namespace) as coordinator:
        operation = request(coordinator, tmp_path / "target")
        lease = coordinator.acquire(operation)
        lock = namespace / "locks" / operation.resources[0].resource_id
        if os.name == "nt":
            # Windows denies deletion while the lock descriptor is open.
            with pytest.raises(PermissionError):
                lock.unlink()
            coordinator.validate(lease)
            coordinator.transition(lease, MutationTransition(MutationState.ABORTED, ()))
            return
        lock.unlink()
        lock.write_bytes(b"")
        with pytest.raises(AncestryError) as failure:
            coordinator.validate(lease)
        assert failure.value.code == "MUTATION_OWNER_STALE"


def test_renew_fences_old_handle_and_deadline_is_bounded(tmp_path: Path) -> None:
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        operation = replace(request(coordinator, tmp_path / "target"), lease_ms=1000)
        lease = coordinator.acquire(operation)
        time.sleep(0.005)
        renewed = coordinator.renew(lease)
        assert renewed.expires_ms <= operation.deadline_ms
        with pytest.raises(AncestryError) as failure:
            coordinator.validate(lease)
        assert failure.value.code == "MUTATION_OWNER_STALE"
        coordinator.transition(renewed, MutationTransition(MutationState.ABORTED, ()))
        with pytest.raises(AncestryError) as failure:
            coordinator.acquire(replace(request(coordinator, tmp_path / "next"), deadline_ms=1))
        assert failure.value.code == "MUTATION_DEADLINE_EXCEEDED"


def test_outcome_rejects_nonterminal_state() -> None:
    with pytest.raises(ValueError):
        MutationOutcome(uuid4().hex, MutationState.PREPARED, ())


def test_lease_rejects_invalid_owner_and_bounds() -> None:
    with pytest.raises(ValueError):
        MutationLease(uuid4().hex, "private/path", 0, -1, MutationState.COMMITTING)


def test_rebind_recovers_historical_scopes_after_replacement(tmp_path: Path) -> None:
    namespace, target = tmp_path / "journal", tmp_path / "target"
    target.write_bytes(b"previous complete result")
    with LocalMutationCoordinator(namespace) as coordinator:
        operation = request(coordinator, target)
        lease = coordinator.acquire(operation)
        coordinator.transition(lease, MutationTransition(MutationState.COMMITTING, ()))
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"new complete result")
        replacement.replace(target)
    with LocalMutationCoordinator(namespace) as restarted:
        assert restarted.interrupted((target,)) == (operation,)
        restarted.rebind(operation, (target,))
        lease = restarted.recover(operation)
        outcome = restarted.transition(lease, MutationTransition(MutationState.COMMITTED, ()))
        assert restarted.acquire(operation) == outcome
        assert restarted.interrupted((target,)) == ()


def test_rebind_requires_every_original_destination_and_same_parent(tmp_path: Path) -> None:
    namespace = tmp_path / "journal"
    parent = tmp_path / "outputs"
    parent.mkdir()
    first, second = parent / "first", parent / "second"
    with LocalMutationCoordinator(namespace) as coordinator:
        operation = replace(
            request(coordinator, first), resources=coordinator.bind((first, second))
        )
        coordinator.acquire(operation)
    with LocalMutationCoordinator(namespace) as restarted:
        with pytest.raises(AncestryError) as failure:
            restarted.rebind(operation, (first,))
        assert failure.value.code == "MUTATION_REAUTHORIZATION_REQUIRED"
        # A directory with the same name is not the authorized original directory.
        parent.rename(tmp_path / "moved")
        parent.mkdir()
        with pytest.raises(AncestryError) as failure:
            restarted.rebind(operation, (first, second))
        assert failure.value.code == "MUTATION_REAUTHORIZATION_REQUIRED"
        with pytest.raises(AncestryError):
            restarted.recover(operation)


def test_rebound_changed_object_is_locked_during_recovery(tmp_path: Path) -> None:
    namespace, target = tmp_path / "journal", tmp_path / "target"
    target.write_bytes(b"previous")
    with LocalMutationCoordinator(namespace) as coordinator:
        operation = request(coordinator, target)
        coordinator.acquire(operation)
    target.unlink()
    target.write_bytes(b"replacement")
    alias = tmp_path / "alias"
    alias.hardlink_to(target)
    with LocalMutationCoordinator(namespace) as restarted:
        restarted.rebind(operation, (target,))
        lease = restarted.recover(operation)
        assert contend(namespace, alias) == "MUTATION_CONFLICT"
        restarted.transition(lease, MutationTransition(MutationState.ABORTED, ()))


@pytest.mark.parametrize("acquired", [False, True])
def test_missing_target_rejects_replaced_parent(tmp_path: Path, acquired: bool) -> None:
    parent = tmp_path / "destination"
    parent.mkdir()
    with LocalMutationCoordinator(tmp_path / "journal") as coordinator:
        operation = request(coordinator, parent / "target")
        lease = coordinator.acquire(operation) if acquired else None
        parent.rename(tmp_path / "original-parent")
        parent.mkdir()
        with pytest.raises(AncestryError) as failure:
            if lease is None:
                coordinator.acquire(operation)
            else:
                coordinator.transition(lease, MutationTransition(MutationState.COMMITTING, ()))
        assert failure.value.code == "MUTATION_REAUTHORIZATION_REQUIRED"


def test_recovery_uses_fresh_authorized_deadline(tmp_path: Path) -> None:
    namespace, target = tmp_path / "journal", tmp_path / "target"
    with LocalMutationCoordinator(namespace) as coordinator:
        operation = request(coordinator, target)
        coordinator.acquire(operation)
    with LocalMutationCoordinator(namespace) as restarted:
        expired = replace(operation, deadline_ms=1)
        restarted.rebind(expired, (target,))
        with pytest.raises(AncestryError) as failure:
            restarted.recover(expired)
        assert failure.value.code == "MUTATION_DEADLINE_EXCEEDED"
        fresh = replace(expired, deadline_ms=int(time.time() * 1000) + 60_000)
        lease = restarted.recover(fresh)
        outcome = restarted.transition(lease, MutationTransition(MutationState.ABORTED, ()))
        assert restarted.acquire(expired) == outcome


@pytest.mark.parametrize(
    "media_type", ["/Users/private/source.ged", "text/plain;name=private", "C:/private"]
)
def test_mutation_artifact_metadata_rejects_paths(tmp_path: Path, media_type: str) -> None:
    from ancestryllm.application.dto import ArtifactRef, ArtifactStatus

    artifact = ArtifactRef(
        artifact_id="art_" + "a" * 32,
        media_type=media_type,
        artifact_type="export",
        size_bytes=0,
        status=ArtifactStatus.READY,
    )
    with (
        LocalMutationCoordinator(tmp_path / "journal") as coordinator,
        pytest.raises(ValueError, match="MIME"),
    ):
        replace(request(coordinator, tmp_path / "target"), artifacts=(artifact,))


@pytest.mark.parametrize("names", [("Report", "report"), ("Caf\u00e9", "Cafe\u0301")])
def test_missing_name_aliases_share_contention_without_rebinding_authority(
    tmp_path: Path, names: tuple[str, str]
) -> None:
    namespace = tmp_path / "journal"
    original, alias = (tmp_path / name for name in names)
    with LocalMutationCoordinator(namespace) as coordinator:
        operation = request(coordinator, original)
        coordinator.acquire(operation)
        assert contend(namespace, alias) == "MUTATION_CONFLICT"
    with LocalMutationCoordinator(namespace) as restarted:
        # Conservative alias contention must never grant recovery over another
        # spelling on filesystems where those names are distinct.
        if os.path.normcase(str(original)) != os.path.normcase(str(alias)):
            with pytest.raises(AncestryError) as failure:
                restarted.rebind(operation, (alias,))
            assert failure.value.code == "MUTATION_REAUTHORIZATION_REQUIRED"
        restarted.rebind(operation, (original,))
        lease = restarted.recover(operation)
        restarted.transition(lease, MutationTransition(MutationState.ABORTED, ()))
