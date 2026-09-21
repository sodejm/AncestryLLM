"""Local durable mutation journal, independent of workspace storage bootstrap.

SQLite transactions serialize metadata changes only. Operating-system locks on
opaque resource slots remain held for the whole mutation, including after lease
expiry. An interrupted journal entry continues to reserve its scopes until an
authorized invocation rebinds and reconciles the actual publication.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import sqlite3
import stat
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast
from uuid import uuid4

from ancestryllm.application.mutations import (
    MutationLease,
    MutationOutcome,
    MutationRequest,
    MutationResource,
    MutationState,
    MutationTransition,
)
from ancestryllm.core.errors import AncestryError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


def _error(code: str, message: str) -> AncestryError:
    return AncestryError(code, message)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def coordinator_namespace() -> Path:
    """Use the account home, never workspace/configuration directory overrides."""

    if os.name == "posix":
        import pwd

        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    else:
        from ancestryllm.core.windows_mutation import account_home

        home = account_home()
    return home / ".ancestryllm-coordination"


def _secure_directory(path: Path) -> None:
    if os.name == "nt":
        from ancestryllm.core.windows_mutation import create_private_directory

        create_private_directory(path)
    else:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (
        os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077)
    ):
        raise _error("MUTATION_JOURNAL_UNSAFE", "The mutation journal directory is unsafe.")


def _open_private(path: Path) -> int:
    descriptor = os.open(
        path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        if os.name == "nt":
            from ancestryllm.core.windows_mutation import validate_private

            validate_private(path)
        info = os.fstat(descriptor)
        at_path = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077))
            or (at_path.st_dev, at_path.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise _error("MUTATION_JOURNAL_UNSAFE", "A mutation journal file is unsafe.")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _lock(path: Path) -> int:
    descriptor = _open_private(path)
    try:
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        raise _error(
            "MUTATION_CONFLICT", "Another mutation owns this resource. Retry later."
        ) from None
    return descriptor


def _unlock(descriptor: int) -> None:
    try:
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class _Binding:
    path: Path
    revision: str
    selector: str
    parent_identity: str


@dataclass
class _Held:
    lease: MutationLease
    descriptors: list[int]
    request: MutationRequest
    process_id: int
    resource_ids: tuple[str, ...]


class LocalMutationCoordinator:
    """Metadata-only journal and fenced resource locks for one adapter session."""

    def __init__(self, namespace: Path | None = None) -> None:
        self.namespace = namespace if namespace is not None else coordinator_namespace()
        self._bindings: dict[str, _Binding] = {}
        self._held: dict[str, _Held] = {}
        _secure_directory(self.namespace)
        _secure_directory(self.namespace / "locks")
        bootstrap_deadline = time.monotonic() + 5
        while True:
            try:
                bootstrap = _lock(self.namespace / "bootstrap.lock")
                break
            except AncestryError as exc:
                if exc.code != "MUTATION_CONFLICT" or time.monotonic() >= bootstrap_deadline:
                    raise
                time.sleep(0.01)
        try:
            descriptor = _open_private(self.namespace / "namespace.key")
            try:
                self._key = os.read(descriptor, 33)
                if not self._key:
                    self._key = os.urandom(32)
                    os.write(descriptor, self._key)
                    os.fsync(descriptor)
                if len(self._key) != 32:
                    raise _error(
                        "MUTATION_JOURNAL_UNSAFE", "The mutation namespace key is invalid."
                    )
            finally:
                os.close(descriptor)
            os.close(_open_private(self.namespace / "journal.sqlite3"))
            with self._transaction() as database:
                database.execute(
                    "CREATE TABLE IF NOT EXISTS operations ("
                    "operation_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, "
                    "intent TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL, "
                    "token TEXT NOT NULL, fence INTEGER NOT NULL, expires_ms INTEGER NOT NULL, "
                    "outcome TEXT)"
                )
                database.execute(
                    "CREATE TABLE IF NOT EXISTS scopes (resource_id TEXT PRIMARY KEY, "
                    "operation_id TEXT NOT NULL REFERENCES operations(operation_id))"
                )
                database.execute(
                    "CREATE TABLE IF NOT EXISTS bindings ("
                    "operation_id TEXT NOT NULL REFERENCES operations(operation_id), "
                    "resource_id TEXT NOT NULL, selector TEXT NOT NULL, parent_identity TEXT NOT NULL, "
                    "PRIMARY KEY (operation_id,resource_id))"
                )
        finally:
            _unlock(bootstrap)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            database = sqlite3.connect(self.namespace / "journal.sqlite3", timeout=1)
        except sqlite3.Error:
            raise _error(
                "MUTATION_JOURNAL_UNAVAILABLE", "The mutation journal is unavailable."
            ) from None
        try:
            database.row_factory = sqlite3.Row
            database.execute("PRAGMA foreign_keys=ON")
            database.execute("PRAGMA synchronous=FULL")
            database.execute("BEGIN IMMEDIATE")
            yield database
            database.commit()
        except sqlite3.Error:
            database.rollback()
            raise _error(
                "MUTATION_JOURNAL_UNAVAILABLE", "The mutation journal is unavailable."
            ) from None
        except BaseException:
            database.rollback()
            raise
        finally:
            database.close()

    def _digest(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()

    def _revision(self, path: Path) -> str:
        try:
            info = path.stat()
        except FileNotFoundError:
            return self._digest("missing")
        return self._digest(
            f"revision:{info.st_dev}:{info.st_ino}:{info.st_mode}:{info.st_size}:"
            f"{info.st_mtime_ns}:{info.st_ctime_ns}"
        )

    def bind(self, paths: Iterable[Path]) -> tuple[MutationResource, ...]:
        """Bind authorized paths in memory; canonical aliases never enter SQLite."""

        resources: dict[str, MutationResource] = {}
        for selected in paths:
            path = selected.absolute()
            canonical = path.resolve()
            revision = self._revision(path)
            binding = self._binding(path, revision)
            keys = {
                f"path:{os.path.normcase(str(path))}",
                f"path:{os.path.normcase(str(canonical))}",
                # Reserve equivalent spellings even before a destination exists.
                # This is deliberately conservative on case-sensitive volumes;
                # the exact selector still governs recovery authorization.
                f"name:{binding.parent_identity}:"
                + unicodedata.normalize("NFD", path.name).casefold(),
            }
            try:
                info = path.stat()
            except FileNotFoundError:
                pass
            else:
                keys.add(f"object:{info.st_dev}:{info.st_ino}")
            for key in keys:
                identity = self._digest(key)
                self._bindings[identity] = binding
                resources[identity] = MutationResource(identity, revision)
        return tuple(resources[key] for key in sorted(resources))

    def _binding(self, path: Path, revision: str) -> _Binding:
        try:
            parent = path.parent.resolve(strict=True)
            info = parent.stat()
        except OSError:
            raise _error(
                "MUTATION_REAUTHORIZATION_REQUIRED", "Reauthorize the mutation resources."
            ) from None
        selector = self._digest(f"binding:{os.path.normcase(str(parent / path.name))}")
        parent_identity = self._digest(
            f"parent:{info.st_dev}:{info.st_ino}:{getattr(info, 'st_birthtime_ns', '')}"
        )
        return _Binding(path, revision, selector, parent_identity)

    def _validate_binding(self, binding: _Binding) -> None:
        current = self._binding(binding.path, binding.revision)
        if (current.selector, current.parent_identity) != (
            binding.selector,
            binding.parent_identity,
        ):
            raise _error("MUTATION_REAUTHORIZATION_REQUIRED", "Reauthorize the mutation resources.")

    def interrupted(self, paths: Iterable[Path]) -> tuple[MutationRequest, ...]:
        """Find reservations for freshly authorized destinations without storing their names."""

        bindings = [self._binding(path.absolute(), self._revision(path)) for path in paths]
        found: dict[str, MutationRequest] = {}
        with self._transaction() as database:
            for binding in bindings:
                rows = database.execute(
                    "SELECT operations.operation_id,request FROM operations JOIN bindings "
                    "ON operations.operation_id=bindings.operation_id "
                    "WHERE outcome IS NULL AND selector=? AND parent_identity=?",
                    (binding.selector, binding.parent_identity),
                ).fetchall()
                for row in rows:
                    found[row["operation_id"]] = MutationRequest.from_json(row["request"])
        return tuple(found[key] for key in sorted(found))

    def rebind(self, request: MutationRequest, paths: Iterable[Path]) -> None:
        """Authorize historical scopes after replacement, retaining parent-directory identity.

        Adapters must first validate their current grants. This never reconstructs paths or
        renews a desktop grant from journal metadata. Changed contents still require the
        publication adapter's ownership and digest checks before any recovery mutation.
        """

        authorized = {
            binding.selector: binding
            for path in paths
            for binding in (self._binding(path.absolute(), self._revision(path)),)
        }
        rebound: dict[str, _Binding] = {}
        with self._transaction() as database:
            operation = self._record(database, request)
            if operation is None or operation["outcome"] is not None:
                raise _error(
                    "MUTATION_RECOVERY_INVALID", "No interrupted mutation matches this request."
                )
            rows = database.execute(
                "SELECT resource_id,selector,parent_identity FROM bindings WHERE operation_id=?",
                (operation["operation_id"],),
            ).fetchall()
            for row in rows:
                binding = authorized.get(row["selector"])
                if binding is None or binding.parent_identity != row["parent_identity"]:
                    raise _error(
                        "MUTATION_REAUTHORIZATION_REQUIRED", "Reauthorize the mutation resources."
                    )
                rebound[row["resource_id"]] = binding
            if not {item.resource_id for item in request.resources} <= rebound.keys():
                raise _error(
                    "MUTATION_REAUTHORIZATION_REQUIRED", "Reauthorize the mutation resources."
                )
        self._bindings.update(rebound)

    def _intent(self, request: MutationRequest) -> str:
        return self._digest(
            json.dumps(
                {
                    "intent": request.intent_digest,
                    "resources": sorted(
                        (r.resource_id, r.expected_revision) for r in request.resources
                    ),
                    "artifacts": [item.to_json() for item in request.artifacts],
                },
                sort_keys=True,
            )
        )

    def _record(self, database: sqlite3.Connection, request: MutationRequest) -> sqlite3.Row | None:
        row = cast(
            "sqlite3.Row | None",
            database.execute(
                "SELECT * FROM operations WHERE idempotency_key=?", (request.idempotency_key,)
            ).fetchone(),
        )
        if row is not None and row["intent"] != self._intent(request):
            raise _error(
                "MUTATION_IDEMPOTENCY_MISMATCH", "This retry does not match the recorded intent."
            )
        return row

    def acquire(self, request: MutationRequest) -> MutationLease | MutationOutcome:
        """Acquire deterministic scopes, retaining unresolved reservations after crashes."""

        with self._transaction() as database:
            row = self._record(database, request)
            if row is not None and row["outcome"] is not None:
                return MutationOutcome.from_json(row["outcome"])
        return self._acquire(request, recovery=False)

    def recover(self, request: MutationRequest) -> MutationLease:
        """Acquire interrupted ownership; callers must verify publication before resolving it."""

        return self._acquire(request, recovery=True)

    def _acquire(self, request: MutationRequest, *, recovery: bool) -> MutationLease:
        if request.deadline_ms <= _now_ms():
            raise _error("MUTATION_DEADLINE_EXCEEDED", "The mutation deadline has expired.")
        descriptors: list[int] = []
        try:
            resources = {resource.resource_id: resource for resource in request.resources}
            for resource in request.resources:
                if resource.resource_id not in self._bindings:
                    raise _error(
                        "MUTATION_REAUTHORIZATION_REQUIRED", "Reauthorize the mutation resources."
                    )
            if recovery:
                # Fence the new inode as well as the original reservation; otherwise a
                # hard-link alias of a replacement could mutate during reconciliation.
                resources = {
                    key: MutationResource(key, self._bindings[key].revision) for key in resources
                }
                current_paths = {self._bindings[key].path for key in resources}
                resources.update({item.resource_id: item for item in self.bind(current_paths)})
            ordered_ids = tuple(sorted(resources))
            descriptors.extend(_lock(self.namespace / "locks" / key) for key in ordered_ids)
            with self._transaction() as database:
                row = self._record(database, request)
                if recovery and (row is None or row["outcome"] is not None):
                    raise _error(
                        "MUTATION_RECOVERY_INVALID", "No interrupted mutation matches this request."
                    )
                if row is not None and not recovery:
                    raise _error(
                        "MUTATION_RECOVERY_REQUIRED", "An interrupted mutation requires recovery."
                    )
                operation_id = row["operation_id"] if row is not None else request.operation_id
                if recovery:
                    for binding_row in database.execute(
                        "SELECT resource_id,selector,parent_identity FROM bindings WHERE operation_id=?",
                        (operation_id,),
                    ):
                        binding = self._bindings.get(binding_row["resource_id"])
                        current = self._binding(binding.path, binding.revision) if binding else None
                        if current is None or (current.selector, current.parent_identity) != (
                            binding_row["selector"],
                            binding_row["parent_identity"],
                        ):
                            raise _error(
                                "MUTATION_REAUTHORIZATION_REQUIRED",
                                "Reauthorize the mutation resources.",
                            )
                for resource in resources.values():
                    self._validate_binding(self._bindings[resource.resource_id])
                    reservation = database.execute(
                        "SELECT operation_id FROM scopes WHERE resource_id=?",
                        (resource.resource_id,),
                    ).fetchone()
                    if reservation is not None and reservation["operation_id"] != operation_id:
                        raise _error(
                            "MUTATION_RECOVERY_REQUIRED",
                            "An interrupted mutation requires recovery.",
                        )
                    if (
                        self._revision(self._bindings[resource.resource_id].path)
                        != resource.expected_revision
                    ):
                        raise _error(
                            "MUTATION_REVISION_STALE",
                            "An authorized resource revision has changed.",
                        )
                state = MutationState.RECOVERY_REQUIRED if recovery else MutationState.PREPARED
                lease = MutationLease(
                    operation_id,
                    uuid4().hex,
                    row["fence"] + 1 if row else 1,
                    min(_now_ms() + request.lease_ms, request.deadline_ms),
                    state,
                )
                if row is None:
                    database.execute(
                        "INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,NULL)",
                        (
                            operation_id,
                            request.idempotency_key,
                            self._intent(request),
                            request.to_json(),
                            state.value,
                            lease.token,
                            lease.fence,
                            lease.expires_ms,
                        ),
                    )
                else:
                    database.execute(
                        "UPDATE operations SET state=?,token=?,fence=?,expires_ms=? WHERE operation_id=?",
                        (state.value, lease.token, lease.fence, lease.expires_ms, operation_id),
                    )
                for resource in resources.values():
                    database.execute(
                        "INSERT OR IGNORE INTO scopes VALUES (?,?)",
                        (resource.resource_id, operation_id),
                    )
                    binding = self._bindings[resource.resource_id]
                    database.execute(
                        "INSERT OR IGNORE INTO bindings VALUES (?,?,?,?)",
                        (
                            operation_id,
                            resource.resource_id,
                            binding.selector,
                            binding.parent_identity,
                        ),
                    )
            self._held[lease.token] = _Held(lease, descriptors, request, os.getpid(), ordered_ids)
            return lease
        except BaseException:
            for descriptor in reversed(descriptors):
                _unlock(descriptor)
            raise

    def validate(self, lease: MutationLease) -> None:
        """Fence stale/forked handles and expired owners before publication."""

        self._validate_owner(lease)
        if lease.expires_ms <= _now_ms():
            raise _error("MUTATION_LEASE_EXPIRED", "The mutation lease has expired.")

    def _validate_owner(self, lease: MutationLease) -> None:
        """Check retained exclusive ownership, including during terminal cleanup."""

        held = self._held.get(lease.token)
        if held is None or held.lease != lease or held.process_id != os.getpid():
            raise _error("MUTATION_OWNER_STALE", "The mutation owner is no longer valid.")
        for resource_id, descriptor in zip(
            held.resource_ids,
            held.descriptors,
            strict=True,
        ):
            try:
                opened = os.fstat(descriptor)
                at_path = (self.namespace / "locks" / resource_id).lstat()
            except OSError:
                raise _error(
                    "MUTATION_OWNER_STALE", "The mutation owner is no longer valid."
                ) from None
            if (opened.st_dev, opened.st_ino) != (
                at_path.st_dev,
                at_path.st_ino,
            ) or opened.st_nlink != 1:
                raise _error("MUTATION_OWNER_STALE", "The mutation owner is no longer valid.")
        with self._transaction() as database:
            row = database.execute(
                "SELECT token,fence FROM operations WHERE operation_id=?", (lease.operation_id,)
            ).fetchone()
            if row is None or row["token"] != lease.token or row["fence"] != lease.fence:
                raise _error("MUTATION_OWNER_STALE", "The mutation owner is no longer valid.")

    def renew(self, lease: MutationLease) -> MutationLease:
        """Renew only a still-live owner, bounded by its original deadline."""

        self.validate(lease)
        held = self._held[lease.token]
        renewed = replace(
            lease, expires_ms=min(_now_ms() + held.request.lease_ms, held.request.deadline_ms)
        )
        with self._transaction() as database:
            database.execute(
                "UPDATE operations SET expires_ms=? WHERE operation_id=?",
                (renewed.expires_ms, lease.operation_id),
            )
        held.lease = renewed
        return renewed

    def transition(
        self, lease: MutationLease, transition: MutationTransition
    ) -> MutationLease | MutationOutcome:
        """Persist the result before releasing locks; terminal results cannot change."""

        terminal = transition.state in {MutationState.COMMITTED, MutationState.ABORTED}
        if terminal:
            # Expiry prevents publication, but cannot erase an already published
            # result. The original OS ownership must still be held to finalize.
            self._validate_owner(lease)
        else:
            self.validate(lease)
        allowed = {
            MutationState.PREPARED: {MutationState.COMMITTING, MutationState.ABORTED},
            MutationState.COMMITTING: {MutationState.COMMITTED, MutationState.ABORTED},
            MutationState.RECOVERY_REQUIRED: {MutationState.COMMITTED, MutationState.ABORTED},
        }
        if transition.state not in allowed.get(lease.state, set()):
            raise _error("MUTATION_TRANSITION_INVALID", "The mutation transition is invalid.")
        if transition.state is MutationState.COMMITTING:
            for resource in self._held[lease.token].request.resources:
                self._validate_binding(self._bindings[resource.resource_id])
                if (
                    self._revision(self._bindings[resource.resource_id].path)
                    != resource.expected_revision
                ):
                    raise _error(
                        "MUTATION_REVISION_STALE", "An authorized resource revision has changed."
                    )
        outcome = (
            MutationOutcome(lease.operation_id, transition.state, transition.artifacts)
            if terminal
            else None
        )
        with self._transaction() as database:
            database.execute(
                "UPDATE operations SET state=?,outcome=? WHERE operation_id=?",
                (
                    transition.state.value,
                    outcome.to_json() if outcome is not None else None,
                    lease.operation_id,
                ),
            )
            if terminal:
                database.execute("DELETE FROM scopes WHERE operation_id=?", (lease.operation_id,))
        held = self._held[lease.token]
        if terminal:
            assert outcome is not None
            del self._held[lease.token]
            for descriptor in reversed(held.descriptors):
                _unlock(descriptor)
            return outcome
        held.lease = replace(lease, state=transition.state)
        return held.lease

    def close(self) -> None:
        """Release OS handles, preserving nonterminal journal reservations."""

        for held in list(self._held.values()):
            if held.process_id != os.getpid():
                # A forked child must not explicitly unlock its parent's flock.
                for descriptor in held.descriptors:
                    os.close(descriptor)
                continue
            try:
                with self._transaction() as database:
                    database.execute(
                        "UPDATE operations SET state=? WHERE operation_id=? AND token=?",
                        (
                            MutationState.RECOVERY_REQUIRED.value,
                            held.lease.operation_id,
                            held.lease.token,
                        ),
                    )
            finally:
                for descriptor in reversed(held.descriptors):
                    _unlock(descriptor)
        self._held.clear()


__all__ = ["LocalMutationCoordinator", "coordinator_namespace"]
