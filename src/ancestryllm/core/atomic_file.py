"""Journaled single-file replacement for freshly authorized local destinations.

The journal contains only keyed fingerprints and random staging identifiers. A
restart must supply the destination again; it cannot recover a private path or a
desktop permission from the journal. Publication is one same-directory rename.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import stat
import time
from ctypes import wintypes
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Self
from uuid import uuid4

from ancestryllm.application.dto import BoundaryDTO
from ancestryllm.application.mutations import (
    MutationLease,
    MutationOutcome,
    MutationRequest,
    MutationState,
    MutationTransition,
)
from ancestryllm.core import publication as pub
from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.errors import AncestryError

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.core.mutation import LocalMutationCoordinator


def _recovery_required() -> AncestryError:
    return AncestryError(
        "MUTATION_RECOVERY_REQUIRED", "The interrupted publication requires recovery."
    )


@dataclass(frozen=True, slots=True)
class _ReplacementRecord(BoundaryDTO):
    stage_token: str
    previous: str | None
    stage_identity: str | None = None
    staged: str | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{32}", self.stage_token) is None:
            raise ValueError("Invalid staging identifier.")
        for value in (self.previous, self.stage_identity, self.staged):
            if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError("Invalid publication fingerprint.")


def _identity(coordinator: LocalMutationCoordinator, info: os.stat_result) -> str:
    # Windows name tunneling can change creation time when this same file is
    # renamed over an existing name. Device and file ID survive that rename.
    birthtime = "" if os.name == "nt" else getattr(info, "st_birthtime_ns", "")
    return coordinator._digest(f"file:{info.st_dev}:{info.st_ino}:{birthtime}")


def _windows_open_fingerprint_descriptor(path: Path, *, writable: bool = False) -> int:
    """Read or flush an owned object while sharing a held delete capability."""

    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create_file = kernel.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    handle = create_file(
        os.fspath(path), 0xC0000000 if writable else 0x80000000, 7, None, 3, 0x00200000, None
    )  # GENERIC_READ, SHARE_READ|WRITE|DELETE, OPEN_EXISTING, OPEN_REPARSE_POINT
    value = ctypes.cast(handle, ctypes.c_void_p).value
    if value in {None, ctypes.c_void_p(-1).value}:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    try:
        return int(
            msvcrt.open_osfhandle(  # type: ignore[attr-defined]
                value,
                (os.O_RDWR if writable else os.O_RDONLY)
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOINHERIT", 0),
            )
        )
    except BaseException:
        close_handle(handle)
        raise


def _open_fingerprint_descriptor(path: Path) -> int:
    if os.name == "nt":
        return _windows_open_fingerprint_descriptor(path)
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))


def _open_flush_descriptor(path: Path) -> int:
    # FlushFileBuffers requires write access and must share held DELETE handles.
    if os.name == "nt":
        return _windows_open_fingerprint_descriptor(path, writable=True)
    return os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))


def _fingerprint(coordinator: LocalMutationCoordinator, path: Path) -> str | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise _recovery_required()
    descriptor = _open_fingerprint_descriptor(path)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != stat.S_IMODE(before.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (
                before.st_dev,
                before.st_ino,
            )
        ):
            raise _recovery_required()
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        after = os.fstat(descriptor)
        at_path = path.lstat()
        if (
            after.st_nlink != 1
            or at_path.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != stat.S_IMODE(opened.st_mode)
            or stat.S_IMODE(at_path.st_mode) != stat.S_IMODE(after.st_mode)
            or (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            != (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            or (at_path.st_dev, at_path.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise _recovery_required()
        return coordinator._digest(
            f"content:{_identity(coordinator, after)}:{after.st_size}:"
            f"{after.st_mtime_ns}:{stat.S_IMODE(after.st_mode)}:{digest.hexdigest()}"
        )
    finally:
        os.close(descriptor)


def _sync_parent(path: Path) -> None:
    # Windows has no supported directory fsync; the replacement still uses the
    # same atomic file-namespace operation as the existing settings writer.
    if os.name == "posix":
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class AtomicFileMutation:
    """Hold shared mutation ownership across caller validation and publication."""

    def __init__(self, target: Path, coordinator: LocalMutationCoordinator) -> None:
        self.target = target.absolute()
        self.coordinator = coordinator
        self.lease: MutationLease | None = None
        self.record: _ReplacementRecord | None = None
        self.outcome: MutationOutcome | None = None
        self._initialize(coordinator)

    @staticmethod
    def _initialize(coordinator: LocalMutationCoordinator) -> None:
        with coordinator._transaction() as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS file_replacements ("
                "operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id), "
                "record TEXT NOT NULL)"
            )

    @classmethod
    def reconcile(cls, target: Path, coordinator: LocalMutationCoordinator) -> None:
        """Reconcile only a destination supplied by a fresh authorized invocation."""

        cls._initialize(coordinator)
        for interrupted in coordinator.interrupted((target,)):
            with coordinator._transaction() as database:
                row = database.execute(
                    "SELECT record FROM file_replacements WHERE operation_id=?",
                    (interrupted.operation_id,),
                ).fetchone()
            if row is None and interrupted.intent_digest != coordinator._digest(
                "atomic-file-replacement-v1"
            ):
                # This may belong to a different publisher. Never resolve it on
                # the basis of a path match alone.
                raise _recovery_required()
            mutation = cls(target, coordinator)
            if row is not None:
                mutation.record = _ReplacementRecord.from_json(row["record"])
            request = replace(
                interrupted,
                deadline_ms=time.time_ns() // 1_000_000 + 300_000,
                owner_id=uuid4().hex,
                session_id=uuid4().hex,
            )
            coordinator.rebind(request, (target,))
            mutation.lease = coordinator.recover(request)
            if row is None:
                # No staging or replacement is allowed until this publisher's
                # first record is durable. An interrupted acquisition is inert.
                coordinator.transition(
                    mutation.lease, MutationTransition(MutationState.ABORTED, ())
                )
            else:
                mutation._resolve()

    def __enter__(self) -> Self:
        self.reconcile(self.target, self.coordinator)
        cancellation_checkpoint()
        request = MutationRequest(
            operation_id=uuid4().hex,
            resources=self.coordinator.bind((self.target,)),
            intent_digest=self.coordinator._digest("atomic-file-replacement-v1"),
            idempotency_key=uuid4().hex + uuid4().hex,
            owner_id=uuid4().hex,
            session_id=uuid4().hex,
            deadline_ms=time.time_ns() // 1_000_000 + 300_000,
            lease_ms=300_000,
            artifacts=(),
            retain_outcome=False,
        )
        lease = self.coordinator.acquire(request)
        assert isinstance(lease, MutationLease)
        self.lease = lease
        self._checkpoint("acquired")
        self.record = _ReplacementRecord(uuid4().hex, _fingerprint(self.coordinator, self.target))
        self._persist()
        self._checkpoint("prepared")
        return self

    def __exit__(self, *_args: object) -> None:
        if self.lease is not None:
            self._resolve()

    @property
    def _stage(self) -> Path:
        assert self.record is not None
        return self.target.parent / f".ancestry-mutation-{self.record.stage_token}"

    def _persist(self) -> None:
        assert self.lease is not None and self.record is not None
        self.coordinator.validate(self.lease)
        with self.coordinator._transaction() as database:
            database.execute(
                "INSERT INTO file_replacements VALUES (?,?) "
                "ON CONFLICT(operation_id) DO UPDATE SET record=excluded.record",
                (self.lease.operation_id, self.record.to_json()),
            )

    def _checkpoint(self, boundary: str) -> None:
        """Fault-injection boundary for process-termination recovery tests."""

    def _resolve(self) -> None:
        assert self.lease is not None and self.record is not None
        self.coordinator._validate_owner(self.lease)
        # Parent identities are checked again before touching a recovery stage.
        for resource_id in self.coordinator._held[self.lease.token].resource_ids:
            self.coordinator._validate_binding(self.coordinator._bindings[resource_id])
        current = _fingerprint(self.coordinator, self.target)
        if self.record.staged is not None and current == self.record.staged:
            state = MutationState.COMMITTED
        elif current == self.record.previous:
            state = MutationState.ABORTED
        else:
            raise _recovery_required()
        try:
            staged = pub.path_stat(self._stage)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(staged.st_mode) or self.record.stage_identity != _identity(
                self.coordinator, staged
            ):
                raise _recovery_required()
            if self.record.staged is not None and (
                _fingerprint(self.coordinator, self._stage) != self.record.staged
            ):
                raise _recovery_required()
            if not pub._unlink_if_owned(self._stage, pub._PathIdentity.from_stat(staged)):
                raise _recovery_required()
        _sync_parent(self.target)
        outcome = self.coordinator.transition(self.lease, MutationTransition(state, ()))
        assert isinstance(outcome, MutationOutcome)
        self.outcome = outcome
        self.lease = None

    def publish(self, content: bytes) -> MutationOutcome:
        """Stage and verify complete bytes, then atomically replace the destination."""

        assert self.lease is not None and self.record is not None
        cancellation_checkpoint()
        self.coordinator.validate(self.lease)
        descriptor = os.open(
            self._stage,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            self.record = replace(
                self.record, stage_identity=_identity(self.coordinator, os.fstat(descriptor))
            )
            self._persist()
            self._checkpoint("created")
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.record = replace(self.record, staged=_fingerprint(self.coordinator, self._stage))
        self._persist()
        _sync_parent(self.target)
        self._checkpoint("staged")
        cancellation_checkpoint()
        lease = self.coordinator.transition(
            self.lease, MutationTransition(MutationState.COMMITTING, ())
        )
        assert isinstance(lease, MutationLease)
        self.lease = lease
        self._checkpoint("committing")
        self.coordinator.validate(self.lease)
        if _fingerprint(self.coordinator, self._stage) != self.record.staged:
            raise _recovery_required()
        if _fingerprint(self.coordinator, self.target) != self.record.previous:
            raise _recovery_required()
        self._stage.replace(self.target)
        self._checkpoint("published")
        self._resolve()
        self._checkpoint("committed")
        assert self.outcome is not None
        return self.outcome


__all__ = ["AtomicFileMutation"]
