"""Durable ownership and recovery for legacy separate-file publications.

Only random temporary names, object identities and digests enter the journal.
Destinations must be supplied again by a newly authorized invocation. Separate
filenames have recoverable set semantics, not simultaneous atomic visibility.
"""

from __future__ import annotations

import re
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from ancestryllm.application.dto import BoundaryDTO
from ancestryllm.application.mutations import (
    MutationLease,
    MutationRequest,
    MutationState,
    MutationTransition,
)
from ancestryllm.core import publication as pub
from ancestryllm.core.atomic_file import _recovery_required, _sync_parent

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ancestryllm.core.mutation import LocalMutationCoordinator


@dataclass(frozen=True, slots=True)
class _Identity(BoundaryDTO):
    device: int
    inode: int
    file_type: int
    size: int
    modified_ns: int
    changed_ns: int
    created_ns: int | None

    @classmethod
    def save(cls, identity: pub._PathIdentity) -> _Identity:
        return cls(**{name: getattr(identity, name) for name in cls.__annotations__})

    def restore(self) -> pub._PathIdentity:
        return pub._PathIdentity(**{name: getattr(self, name) for name in self.__annotations__})


@dataclass(frozen=True, slots=True)
class _Owned(BoundaryDTO):
    # Empty components designate the authorized target itself.
    components: tuple[str, ...]
    identity: _Identity
    digest: str | None

    def __post_init__(self) -> None:
        if (
            len(self.components) > 2
            or (
                self.components
                and re.fullmatch(r"\.ancestry-publish-[a-zA-Z0-9_-]+", self.components[0]) is None
            )
            or (len(self.components) == 2 and self.components[1] != "owned")
        ):
            raise ValueError("Invalid publication ownership reference.")
        if self.digest is not None and re.fullmatch(r"[0-9a-f]{64}", self.digest) is None:
            raise ValueError("Invalid publication digest.")

    @classmethod
    def save(cls, owned: pub._OwnedPath | None, target: Path) -> _Owned | None:
        if owned is None:
            return None
        path = owned.path.absolute()
        components = () if path == target else path.relative_to(target.parent).parts
        return cls(
            components, _Identity.save(owned.identity), owned.digest.hex() if owned.digest else None
        )

    def restore(self, target: Path) -> pub._OwnedPath:
        path = target.parent.joinpath(*self.components) if self.components else target
        return pub._OwnedPath(
            path, self.identity.restore(), bytes.fromhex(self.digest) if self.digest else None
        )


@dataclass(frozen=True, slots=True)
class _Entry(BoundaryDTO):
    selector: str
    source: _Owned
    original: _Identity | None
    backup: _Owned | None = None
    displaced: _Owned | None = None
    reservation: _Owned | None = None
    candidate: _Owned | None = None
    published: _Owned | None = None
    restoration: _Owned | None = None
    restored: _Owned | None = None
    directories: tuple[_Owned, ...] = ()
    displacement_attempted: bool = False


@dataclass(frozen=True, slots=True)
class _Record(BoundaryDTO):
    entries: tuple[_Entry, ...]
    validated: bool = False


class BundleMutation:
    """Retain cross-process ownership through validation, rollback and cleanup."""

    def __init__(self, targets: Iterable[Path], coordinator: LocalMutationCoordinator) -> None:
        self.targets = tuple(path.absolute() for path in targets)
        self.coordinator = coordinator
        self.lease: MutationLease | None = None
        self.record: _Record | None = None
        self.artifacts: list[pub._Artifact] = []
        self._initialize(coordinator)

    @staticmethod
    def _initialize(coordinator: LocalMutationCoordinator) -> None:
        with coordinator._transaction() as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS bundle_publications ("
                "operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id), "
                "record TEXT NOT NULL)"
            )

    def acquire(self) -> None:
        """Recover authorized predecessors and reserve every destination."""
        self.reconcile(self.targets, self.coordinator)
        request = MutationRequest(
            operation_id=uuid4().hex,
            resources=self.coordinator.bind(self.targets),
            intent_digest=self.coordinator._digest("artifact-bundle-v1"),
            idempotency_key=uuid4().hex + uuid4().hex,
            owner_id=uuid4().hex,
            session_id=uuid4().hex,
            deadline_ms=time.time_ns() // 1_000_000 + 300_000,
            lease_ms=300_000,
            artifacts=(),
        )
        lease = self.coordinator.acquire(request)
        assert isinstance(lease, MutationLease)
        self.lease = lease

    def track(self, artifacts: list[pub._Artifact]) -> None:
        """Persist ownership before starting the first publication mutation."""
        self.artifacts = artifacts
        entries = []
        for artifact, target in zip(artifacts, self.targets, strict=True):
            source = _Owned.save(artifact.source, target)
            assert source is not None
            entries.append(
                _Entry(
                    self.coordinator._binding(target, "").selector,
                    source,
                    _Identity.save(artifact.original_target) if artifact.original_target else None,
                )
            )
            artifact.observer = self.observe
        self.record = _Record(tuple(entries))
        self._persist()
        self._checkpoint("prepared")

    def _persist(self) -> None:
        assert self.lease is not None and self.record is not None
        # Recovery/rollback may finish after the publishing lease expires, but
        # only while this process still owns the same fenced OS reservation.
        self.coordinator._validate_owner(self.lease)
        with self.coordinator._transaction() as database:
            database.execute(
                "INSERT INTO bundle_publications VALUES (?,?) ON CONFLICT(operation_id) "
                "DO UPDATE SET record=excluded.record",
                (self.lease.operation_id, self.record.to_json()),
            )

    def observe(
        self,
        event: str,
        artifact: pub._Artifact,
        prepared: pub._PreparedRegularInstall | None = None,
        reservation: pub._OwnedPath | None = None,
    ) -> None:
        """Journal the ownership boundary reached by the shared publisher."""
        assert self.record is not None
        index = next(i for i, value in enumerate(self.artifacts) if value is artifact)
        target = self.targets[index]
        entry = self.record.entries[index]
        entry = replace(
            entry,
            backup=_Owned.save(artifact.backup, target),
            displaced=_Owned.save(artifact.displaced, target),
            published=_Owned.save(artifact.published, target),
            restored=_Owned.save(artifact.restored, target),
            displacement_attempted=artifact.displacement_attempted,
        )
        if reservation is not None:
            entry = replace(entry, reservation=_Owned.save(reservation, target))
        if prepared is not None:
            candidate = _Owned.save(prepared.candidate, target)
            if event in {"restoring", "restored"}:
                entry = replace(entry, restoration=candidate)
            else:
                entry = replace(entry, candidate=candidate)
            if (
                prepared.quarantine_directory is not None
                and prepared.quarantine_identity is not None
            ):
                directory = _Owned.save(
                    pub._OwnedPath(prepared.quarantine_directory, prepared.quarantine_identity),
                    target,
                )
                assert directory is not None
                if directory not in entry.directories:
                    entry = replace(entry, directories=(*entry.directories, directory))
        entries = list(self.record.entries)
        entries[index] = entry
        self.record = replace(self.record, entries=tuple(entries))
        self._persist()
        self._checkpoint(event)

    def committing(self) -> None:
        """Verify revisions before allowing destination displacement."""
        assert self.lease is not None
        self._checkpoint("backed_up")
        lease = self.coordinator.transition(
            self.lease, MutationTransition(MutationState.COMMITTING, ())
        )
        assert isinstance(lease, MutationLease)
        self.lease = lease

    def validated(self) -> None:
        """Durably record successful validation of the complete new set."""
        assert self.record is not None
        self.record = replace(self.record, validated=True)
        self._persist()
        self._checkpoint("validated")

    def _checkpoint(self, boundary: str) -> None:
        """Fault-injection boundary for deterministic process-termination tests."""

    @staticmethod
    def _refresh(owned: pub._OwnedPath) -> pub._OwnedPath | None:
        actual = pub._identity_or_none(owned.path)
        if actual is None:
            return None
        if not owned.identity.unchanged(actual):
            raise _recovery_required()
        if owned.digest is not None:
            identity, digest = pub._token_candidate(owned.path)
            if digest != owned.digest or not actual.pristine(identity):
                raise _recovery_required()
        return pub._OwnedPath(owned.path, actual, owned.digest)

    def _verify_bindings(self) -> None:
        assert self.lease is not None
        self.coordinator._validate_owner(self.lease)
        for resource in self.coordinator._held[self.lease.token].resource_ids:
            self.coordinator._validate_binding(self.coordinator._bindings[resource])

    def finish(self, *, recovered: bool = False) -> None:
        """Record a terminal outcome only after verifying the complete set."""
        assert self.lease is not None
        self._verify_bindings()
        if self.record is None:
            state = MutationState.ABORTED
        else:
            for artifact, entry in zip(self.artifacts, self.record.entries, strict=True):
                actual = pub._identity_or_none(artifact.target)
                if self.record.validated:
                    if artifact.published is None or self._refresh(artifact.published) is None:
                        raise _recovery_required()
                elif entry.original is None:
                    if actual is not None:
                        raise _recovery_required()
                else:
                    expected = (
                        artifact.restored.identity
                        if artifact.restored
                        else entry.original.restore()
                    )
                    if actual is None or not expected.pristine(actual):
                        raise _recovery_required()
            if recovered:
                # Cleanup is not part of the authoritative outcome. Changed
                # temporary paths belong to somebody else and must survive.
                with suppress(OSError):
                    self._cleanup_private()
            state = MutationState.COMMITTED if self.record.validated else MutationState.ABORTED
        self.coordinator.transition(self.lease, MutationTransition(state, ()))
        self.lease = None

    def _cleanup_private(self) -> None:
        assert self.record is not None
        for target, entry in zip(self.targets, self.record.entries, strict=True):
            for saved in (
                entry.source,
                entry.backup,
                entry.displaced,
                entry.candidate,
                entry.restoration,
            ):
                if saved is None:
                    continue
                owned = saved.restore(target)
                actual = pub._identity_or_none(owned.path)
                if actual is None or not owned.identity.unchanged(actual):
                    continue
                refreshed = self._refresh(owned)
                if refreshed is not None:
                    pub._cleanup_owned(refreshed)
            if entry.reservation is not None:
                reservation = entry.reservation.restore(target)
                actual = pub._identity_or_none(reservation.path)
                if actual is not None:
                    # The placeholder can become the displaced original before
                    # its after-rename record reaches the durable journal.
                    expected = entry.original.restore() if entry.original else None
                    if not reservation.identity.unchanged(actual) and not (
                        expected is not None and expected.unchanged(actual)
                    ):
                        raise _recovery_required()
                    pub._cleanup_owned(pub._OwnedPath(reservation.path, actual))
            for directory in entry.directories:
                owned = directory.restore(target)
                actual = pub._identity_or_none(owned.path)
                if actual is not None:
                    if not owned.identity.same_file_id(actual):
                        raise _recovery_required()
                    if not pub._remove_directory_if_owned(owned.path, actual):
                        raise _recovery_required()
            _sync_parent(target)

    @classmethod
    def reconcile(cls, targets: Iterable[Path], coordinator: LocalMutationCoordinator) -> None:
        """Recover only operations rebound by this authorized destination set."""
        authorized = tuple(path.absolute() for path in targets)
        cls._initialize(coordinator)
        for interrupted in coordinator.interrupted(authorized):
            if interrupted.intent_digest != coordinator._digest("artifact-bundle-v1"):
                raise _recovery_required()
            with coordinator._transaction() as database:
                row = database.execute(
                    "SELECT record FROM bundle_publications WHERE operation_id=?",
                    (interrupted.operation_id,),
                ).fetchone()
            request = replace(
                interrupted,
                owner_id=uuid4().hex,
                session_id=uuid4().hex,
                deadline_ms=time.time_ns() // 1_000_000 + 300_000,
            )
            coordinator.rebind(request, authorized)
            mutation = cls(authorized, coordinator)
            mutation.lease = coordinator.recover(request)
            if row is not None:
                mutation.record = _Record.from_json(row["record"])
                by_selector = {coordinator._binding(path, "").selector: path for path in authorized}
                try:
                    mutation.targets = tuple(
                        by_selector[entry.selector] for entry in mutation.record.entries
                    )
                except KeyError:
                    raise _recovery_required() from None
                mutation._recover_artifacts()
                if mutation.record.validated:
                    pub._cleanup_committed_bundle(mutation.artifacts)
                else:
                    error = pub._rollback_bundle(mutation.artifacts)
                    if error is not None:
                        raise _recovery_required() from error
            mutation.finish(recovered=True)

    def _recover_artifacts(self) -> None:
        assert self.record is not None
        self._verify_bindings()
        for target, entry in zip(self.targets, self.record.entries, strict=True):
            artifact = pub._Artifact(
                entry.source.restore(target),
                target,
                entry.original.restore() if entry.original else None,
            )
            artifact.observer = self.observe
            artifact.displacement_attempted = entry.displacement_attempted
            for name in ("backup", "displaced", "published", "restored"):
                saved = getattr(entry, name)
                if saved is not None:
                    # A target can be the restored old file after an earlier
                    # interrupted rollback; classify it below before checking.
                    if name in {"published", "restored"}:
                        continue
                    setattr(artifact, name, self._refresh(saved.restore(target)))
            if entry.reservation is not None and artifact.displaced is None:
                reservation = entry.reservation.restore(target)
                actual = pub._identity_or_none(reservation.path)
                if (
                    actual is not None
                    and artifact.original_target is not None
                    and artifact.original_target.unchanged(actual)
                ):
                    artifact.displaced = pub._OwnedPath(reservation.path, actual)
            actual = pub._identity_or_none(target)
            if actual is not None:
                matched = False
                for saved, field in (
                    (entry.published, "published"),
                    (entry.candidate, "published"),
                    (entry.restored, "restored"),
                    (entry.restoration, "restored"),
                ):
                    if saved is not None and saved.identity.restore().unchanged(actual):
                        owned = saved.restore(target)
                        setattr(
                            artifact,
                            field,
                            self._refresh(pub._OwnedPath(target, owned.identity, owned.digest)),
                        )
                        matched = True
                        break
                if not matched and (
                    artifact.original_target is None
                    or not artifact.original_target.pristine(actual)
                ):
                    raise _recovery_required()
            self.artifacts.append(artifact)
        # Validate all destinations before beginning any recovery mutation.
        if self.record.validated and any(artifact.published is None for artifact in self.artifacts):
            raise _recovery_required()


__all__ = ["BundleMutation"]
