"""Recover exclusively published directories using newly authorized destinations.

The journal stores random staging identifiers and keyed fingerprints, never file
names from the payload or host paths. A complete directory is verified before
commit; an interrupted rename can then be classified without modifying its data.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
import unicodedata
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
from ancestryllm.core.atomic_file import _fingerprint, _identity, _recovery_required, _sync_parent
from ancestryllm.core.cancellation import cancellation_checkpoint

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.core.mutation import LocalMutationCoordinator


@dataclass(frozen=True, slots=True)
class _Member(BoundaryDTO):
    name: str
    identity: str
    fingerprint: str | None


@dataclass(frozen=True, slots=True)
class _Record(BoundaryDTO):
    stage: str
    identity: str | None
    content: str | None
    marker: str | None = None
    marker_fingerprint: str | None = None
    members: tuple[_Member, ...] = ()
    cleanup_started: bool = False
    complete: bool = False

    def __post_init__(self) -> None:
        if (
            re.fullmatch(
                r"\.(?:gedcom-sync|gedcom-rebase|ancestry-export)-[0-9a-f]{32}", self.stage
            )
            is None
        ):
            raise ValueError("Invalid directory staging identifier.")
        if (
            self.marker is not None
            and re.fullmatch(r"\.ancestryllm-staging-[0-9a-f]{32}", self.marker) is None
        ):
            raise ValueError("Invalid directory ownership identifier.")
        for value in (self.identity, self.content, self.marker_fingerprint):
            if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError("Invalid directory fingerprint.")
        for member in self.members:
            if any(
                re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (member.name, member.identity, member.fingerprint)
                if value is not None
            ):
                raise ValueError("Invalid directory member fingerprint.")
        if len({member.name for member in self.members}) != len(self.members):
            raise ValueError("Duplicate directory member fingerprint.")
        if (self.marker is None) != (self.marker_fingerprint is None):
            raise ValueError("Incomplete directory ownership reference.")


class DirectoryMutation:
    """Coordinate the existing exclusive rename of a complete flat directory."""

    def __init__(
        self, target: Path, coordinator: LocalMutationCoordinator, *, sync_root: bool = False
    ) -> None:
        self.target = target.absolute()
        self.coordinator = coordinator
        self.sync_root = sync_root
        self.lease: MutationLease | None = None
        self.record: _Record | None = None
        self.root_identity: str | None = None
        self._initialize(coordinator)

    @staticmethod
    def _initialize(coordinator: LocalMutationCoordinator) -> None:
        with coordinator._transaction() as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS directory_publications ("
                "operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id), "
                "record TEXT NOT NULL)"
            )

            database.execute(
                "CREATE TABLE IF NOT EXISTS sync_destinations ("
                "operation_id TEXT PRIMARY KEY, destination TEXT NOT NULL, root_identity TEXT)"
            )

    def _paths(self) -> tuple[Path, ...]:
        if self.sync_root:
            root = self.target.parent
            alias_scope = root.parent / (
                ".ancestryllm-sync-alias-"
                + self.coordinator._digest(unicodedata.normalize("NFD", root.name).casefold())
            )
            return (self._root_scope(root, self.coordinator), alias_scope)
        return (self.target,)

    @staticmethod
    def _root_scope(root: Path, coordinator: LocalMutationCoordinator) -> Path:
        # The parent already exists, even when this invocation creates the root.
        # This virtual slot is never created and its revision stays unchanged.
        return root.parent / (
            ".ancestryllm-sync-reservation-" + coordinator._digest(os.path.normcase(root.name))
        )

    def bind_root(self) -> None:
        """Retain root identity after exclusive creation and before staging."""
        assert self.lease is not None and self.sync_root
        self.coordinator.validate(self.lease)
        info = self.target.parent.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise _recovery_required()
        identity = _identity(self.coordinator, info)
        if self.root_identity is not None and identity != self.root_identity:
            raise _recovery_required()
        self.root_identity = identity
        with self.coordinator._transaction() as database:
            database.execute(
                "UPDATE sync_destinations SET root_identity=? WHERE operation_id=?",
                (identity, self.lease.operation_id),
            )

    def acquire(self) -> None:
        """Reserve the destination before staging and reconcile authorized retries."""
        if self.sync_root:
            if re.fullmatch(r"g[0-9]{4,}-[0-9]{8}T[0-9]{6}Z", self.target.name) is None:
                raise ValueError("Invalid generated release identifier.")
            self.reconcile_root(self.target.parent, self.coordinator)
        else:
            self.reconcile(self.target, self.coordinator)
        cancellation_checkpoint()
        request = MutationRequest(
            operation_id=uuid4().hex,
            resources=self.coordinator.bind(self._paths()),
            intent_digest=self.coordinator._digest("directory-publication-v1"),
            idempotency_key=uuid4().hex + uuid4().hex,
            owner_id=uuid4().hex,
            session_id=uuid4().hex,
            deadline_ms=time.time_ns() // 1_000_000 + 300_000,
            lease_ms=300_000,
            artifacts=(),
        )
        if self.sync_root:
            # Generated release names carry no host paths or genealogy payload.
            # Persist before acquisition so every acquired reservation is resolvable
            # through a later authorized invocation for the same release root.
            with self.coordinator._transaction() as database:
                database.execute(
                    "INSERT INTO sync_destinations(operation_id,destination) VALUES (?,?)",
                    (request.operation_id, self.target.name),
                )
        lease = self.coordinator.acquire(request)
        assert isinstance(lease, MutationLease)
        self.lease = lease
        if self.sync_root and self.target.parent.exists():
            self.bind_root()
        if pub._identity_or_none(self.target) is not None:
            self.coordinator.transition(lease, MutationTransition(MutationState.ABORTED, ()))
            self.lease = None
            raise _recovery_required()

    def _verify_bindings(self) -> None:
        assert self.lease is not None
        self.coordinator._validate_owner(self.lease)
        for resource in self.coordinator._held[self.lease.token].resource_ids:
            self.coordinator._validate_binding(self.coordinator._bindings[resource])
        if self.root_identity is not None:
            info = self.target.parent.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or _identity(self.coordinator, info) != self.root_identity
            ):
                raise _recovery_required()

    def _snapshot(self, path: Path, marker: str | None) -> tuple[str, str, str | None]:
        before = path.lstat()
        if not stat.S_ISDIR(before.st_mode):
            raise _recovery_required()
        fingerprints = []
        marker_fingerprint = None
        for child in sorted(path.iterdir()):
            fingerprint = _fingerprint(self.coordinator, child)
            if fingerprint is None:
                raise _recovery_required()
            if child.name == marker:
                marker_fingerprint = fingerprint
            else:
                fingerprints.append((child.name, fingerprint))
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise _recovery_required()
        return (
            _identity(self.coordinator, after),
            self.coordinator._digest(json.dumps(fingerprints, ensure_ascii=True)),
            marker_fingerprint,
        )

    def _persist(self) -> None:
        assert self.lease is not None and self.record is not None
        with self.coordinator._transaction() as database:
            database.execute(
                "INSERT INTO directory_publications VALUES (?,?) "
                "ON CONFLICT(operation_id) DO UPDATE SET record=excluded.record",
                (self.lease.operation_id, self.record.to_json()),
            )

    def plan_staging(self, stage: Path) -> None:
        """Reserve a random staging name before creating any filesystem entry."""
        assert self.lease is not None and self.record is None
        self.coordinator.validate(self.lease)
        self._verify_bindings()
        if (
            stage.absolute().parent != self.target.parent
            or pub._identity_or_none(stage) is not None
        ):
            raise _recovery_required()
        self.record = _Record(stage.name, None, None)
        self._persist()
        self._checkpoint("staging_planned")

    def track_staging(self, stage: Path, marker: str | None = None) -> None:
        """Record initial ownership before payload writers touch the directory."""
        self._record_staging(stage, marker, complete=False)
        self._checkpoint("staging_created")

    def prepare(self, stage: Path, marker: str | None = None) -> None:
        """Make the verified complete replacement durable before publication."""
        self._record_staging(stage, marker, complete=True)
        self._checkpoint("prepared")

    def track_member(self, path: Path, descriptor: int) -> None:
        """Persist exclusive file ownership before the writer emits any bytes."""
        assert self.lease is not None and self.record is not None
        self.coordinator.validate(self.lease)
        self._verify_bindings()
        self._verify_member_parent(path)
        opened = os.fstat(descriptor)
        selected = path.lstat()
        identity = _identity(self.coordinator, opened)
        name = self.coordinator._digest(path.name)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(selected.st_mode)
            or opened.st_nlink != 1
            or identity != _identity(self.coordinator, selected)
            or any(member.name == name for member in self.record.members)
        ):
            raise _recovery_required()
        self.record = replace(
            self.record, members=(*self.record.members, _Member(name, identity, None))
        )
        self._persist()
        self._checkpoint("member_created")

    def finish_member(self, path: Path) -> None:
        """Seal a completed staged file after the writer has flushed its bytes."""
        assert self.lease is not None and self.record is not None
        self.coordinator.validate(self.lease)
        self._verify_bindings()
        self._verify_member_parent(path)
        self._verify_member(path)
        name = self.coordinator._digest(path.name)
        fingerprint = _fingerprint(self.coordinator, path)
        if fingerprint is None:
            raise _recovery_required()
        self.record = replace(
            self.record,
            members=tuple(
                replace(member, fingerprint=fingerprint) if member.name == name else member
                for member in self.record.members
            ),
        )
        self._persist()
        self._checkpoint("member_written")

    def _verify_member_parent(self, path: Path) -> None:
        assert self.record is not None
        stage = self.target.parent / self.record.stage
        info = stage.lstat()
        if (
            self.record.complete
            or self.record.cleanup_started
            or path.absolute().parent != stage
            or not stat.S_ISDIR(info.st_mode)
            or _identity(self.coordinator, info) != self.record.identity
        ):
            raise _recovery_required()

    def _verify_member(self, path: Path) -> None:
        assert self.record is not None
        name = self.coordinator._digest(path.name)
        member = next((member for member in self.record.members if member.name == name), None)
        info = path.lstat()
        if (
            member is None
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or _identity(self.coordinator, info) != member.identity
            or (
                member.fingerprint is not None
                and _fingerprint(self.coordinator, path) != member.fingerprint
            )
        ):
            raise _recovery_required()

    def _record_staging(self, stage: Path, marker: str | None, *, complete: bool) -> None:
        assert self.lease is not None
        self.coordinator.validate(self.lease)
        self._verify_bindings()
        stage = stage.absolute()
        if stage.parent != self.target.parent:
            raise _recovery_required()
        identity, content, marker_fingerprint = self._snapshot(stage, marker)
        if self.record is not None and (
            self.record.stage != stage.name
            or (self.record.identity is not None and self.record.identity != identity)
        ):
            raise _recovery_required()
        members = tuple(
            _Member(
                self.coordinator._digest(child.name),
                _identity(self.coordinator, child.lstat()),
                _fingerprint(self.coordinator, child),
            )
            for child in sorted(stage.iterdir())
        )
        if any(member.fingerprint is None for member in members):
            raise _recovery_required()
        if self.record is not None and self.record.identity is not None:
            expected = {member.name: member for member in self.record.members}
            if any(expected.get(member.name) != member for member in members) or len(
                members
            ) != len(expected):
                raise _recovery_required()
        self.record = _Record(
            stage.name,
            identity,
            content,
            marker,
            marker_fingerprint,
            members=members,
            complete=complete,
        )
        for child in stage.iterdir():
            # Windows FlushFileBuffers requires a handle opened for writing.
            descriptor = os.open(child, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        _sync_parent(stage / "unused")
        _sync_parent(stage)
        if self._snapshot(stage, marker) != (identity, content, marker_fingerprint):
            raise _recovery_required()
        self._persist()

    def committing(self) -> None:
        """Enter commit only after verifying the complete staged replacement."""
        assert self.lease is not None and self.record is not None
        if not self.record.complete:
            raise _recovery_required()
        cancellation_checkpoint()
        self._verify_bindings()
        self._verify_directory(self.target.parent / self.record.stage, marker_required=True)
        lease = self.coordinator.transition(
            self.lease, MutationTransition(MutationState.COMMITTING, ())
        )
        assert isinstance(lease, MutationLease)
        self.lease = lease
        self._checkpoint("committing")

    def _verify_directory(self, path: Path, *, marker_required: bool = False) -> None:
        assert self.record is not None
        identity, content, marker = self._snapshot(path, self.record.marker)
        if (
            identity != self.record.identity
            or content != self.record.content
            or (
                marker != self.record.marker_fingerprint and (marker_required or marker is not None)
            )
        ):
            raise _recovery_required()

    def _cleanup(self, path: Path, *, published: bool) -> None:
        assert self.record is not None
        directory = pub._identity_or_none(path)
        if directory is None:
            return
        if published or (self.record.complete and not self.record.cleanup_started):
            self._verify_directory(path)
        elif (
            not stat.S_ISDIR(path.lstat().st_mode)
            or _identity(self.coordinator, path.lstat()) != self.record.identity
        ):
            raise _recovery_required()
        # A restarted cleanup may see a subset of the original files, but never
        # an unknown or changed member. Names remain keyed opaque identifiers.
        children = tuple(path.iterdir())
        for child in children:
            self._verify_member(child)
        if not published and not self.record.cleanup_started:
            assert self.lease is not None
            self.record = replace(self.record, cleanup_started=True)
            with self.coordinator._transaction() as database:
                database.execute(
                    "UPDATE directory_publications SET record=? WHERE operation_id=?",
                    (self.record.to_json(), self.lease.operation_id),
                )
            self._checkpoint("cleanup_started")
        for child in children:
            if published and child.name != self.record.marker:
                continue
            identity, digest = pub._token_candidate(child)
            self._verify_member(child)
            pub._cleanup_owned(pub._OwnedPath(child, identity, digest))
            self._checkpoint("cleanup_file")
        if not published:
            if not pub._remove_directory_if_owned(path, directory):
                raise _recovery_required()
            self._checkpoint("cleanup_directory")
        _sync_parent(path)

    def finish(self) -> None:
        """Commit only a verified published directory; otherwise recover the old state."""
        assert self.lease is not None
        self._verify_bindings()
        published = pub._identity_or_none(self.target) is not None
        if self.record is None:
            if published:
                raise _recovery_required()
        else:
            stage = self.target.parent / self.record.stage
            if published:
                if not self.record.complete:
                    raise _recovery_required()
                self._verify_directory(self.target)
                if pub._identity_or_none(stage) is not None:
                    raise _recovery_required()
                self._cleanup(self.target, published=True)
                self._verify_directory(self.target)
            elif pub._identity_or_none(stage) is not None:
                self._cleanup(stage, published=False)
        if self.target.parent.exists():
            _sync_parent(self.target)
        state = MutationState.COMMITTED if published else MutationState.ABORTED
        self.coordinator.transition(self.lease, MutationTransition(state, ()))
        self.lease = None
        self._checkpoint(state.value)

    @classmethod
    def reconcile_root(cls, root: Path, coordinator: LocalMutationCoordinator) -> None:
        """Reconcile generated releases beneath a newly authorized sync root."""
        cls._initialize(coordinator)
        for interrupted in coordinator.interrupted((cls._root_scope(root, coordinator),)):
            with coordinator._transaction() as database:
                row = database.execute(
                    "SELECT destination FROM sync_destinations WHERE operation_id=?",
                    (interrupted.operation_id,),
                ).fetchone()
            if (
                row is None
                or re.fullmatch(r"g[0-9]{4,}-[0-9]{8}T[0-9]{6}Z", row["destination"]) is None
            ):
                raise _recovery_required()
            cls._reconcile_request(root / row["destination"], coordinator, interrupted)

    @classmethod
    def reconcile(cls, target: Path, coordinator: LocalMutationCoordinator) -> None:
        """Recover a publication using a fresh destination binding."""
        cls._initialize(coordinator)
        for interrupted in coordinator.interrupted((target,)):
            cls._reconcile_request(target, coordinator, interrupted)

    @classmethod
    def _reconcile_request(
        cls, target: Path, coordinator: LocalMutationCoordinator, interrupted: MutationRequest
    ) -> None:
        if interrupted.intent_digest != coordinator._digest("directory-publication-v1"):
            raise _recovery_required()
        with coordinator._transaction() as database:
            row = database.execute(
                "SELECT record FROM directory_publications WHERE operation_id=?",
                (interrupted.operation_id,),
            ).fetchone()
            sync = database.execute(
                "SELECT destination,root_identity FROM sync_destinations WHERE operation_id=?",
                (interrupted.operation_id,),
            ).fetchone()
        mutation = cls(target, coordinator, sync_root=sync is not None)
        if sync is not None:
            mutation.root_identity = sync["root_identity"]
        if row is not None:
            mutation.record = _Record.from_json(row["record"])
        request = replace(
            interrupted,
            owner_id=uuid4().hex,
            session_id=uuid4().hex,
            deadline_ms=time.time_ns() // 1_000_000 + 300_000,
        )
        coordinator.rebind(request, mutation._paths())
        mutation.lease = coordinator.recover(request)
        mutation.finish()

    def _checkpoint(self, boundary: str) -> None:
        """Fault-injection boundary for deterministic process-termination tests."""


__all__ = ["DirectoryMutation"]
