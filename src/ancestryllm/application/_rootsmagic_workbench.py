"""Private, revocable source sessions for the immutable RootsMagic workbench."""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING

from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
from ancestryllm.application.operations import RootsMagicSourceSummary
from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.ingress import FileFingerprint
from ancestryllm.core.windows_identity import windows_stat_identity
from ancestryllm.rootsmagic.core import RootsMagicReader

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ancestryllm.application.operations import (
        RootsMagicPresetQueryRequest,
        RootsMagicResultPage,
    )
    from ancestryllm.rootsmagic.core import SourceFingerprint


def _unavailable() -> AncestryError:
    return AncestryError(
        "ROOTSMAGIC_SOURCE_UNAVAILABLE",
        "The source is unavailable in this session.",
        "Select the source again using the native picker.",
    )


def source_digest(fingerprint: SourceFingerprint) -> str:
    """Bind the display fingerprint to all bytes in a verified SQLite generation."""
    components: list[str | None] = [fingerprint.sha256]
    if not isinstance(fingerprint, FileFingerprint):
        components.extend(
            [fingerprint.wal.sha256, fingerprint.shm.sha256 if fingerprint.shm else None]
        )
    return hashlib.sha256(json.dumps(components, separators=(",", ":")).encode()).hexdigest()


def sanitized_source_name(name: str) -> str:
    """Keep native source names safe for the desktop's plain-text display."""
    return (
        "".join(char for char in name if not unicodedata.category(char).startswith("C"))
        or "RootsMagic source"
    )


@dataclass(frozen=True, slots=True)
class PickerFileApproval:
    """Exact identity and bytes approved by Main for one SQLite generation member."""

    device: int
    inode: int
    size_bytes: int
    sha256: str

    def verify(self, path: Path, fingerprint: FileFingerprint) -> None:
        """Reject a captured generation member that differs from Main's approval."""
        expected = (self.device, self.inode)
        if sys.platform == "win32":
            import msvcrt

            # Check libuv's legacy IDs and Python's full IDs on the same held file.
            with path.open("rb") as stream:
                handle = msvcrt.get_osfhandle(stream.fileno())
                if RootsMagicReader._windows_handle_identity(handle) != expected:
                    raise AncestryError("FILE_INPUT_CHANGED", "The selected source changed.")
                expected = windows_stat_identity(handle)
        snapshot = fingerprint.snapshot
        if (
            (snapshot.device, snapshot.inode) != expected
            or snapshot.size != self.size_bytes
            or fingerprint.sha256 != self.sha256
        ):
            raise AncestryError("FILE_INPUT_CHANGED", "The selected source changed.")


@dataclass(slots=True)
class _SourceSession:
    path: Path
    reader: RootsMagicReader
    fingerprint: SourceFingerprint
    summary: RootsMagicSourceSummary
    lock: RLock = field(default_factory=RLock)
    publication_lock: RLock = field(default_factory=RLock)
    revoked: bool = False

    @contextmanager
    def publication_guard(self) -> Iterator[None]:
        """Order revocation against the short, irreversible publication section."""
        with self.publication_lock:
            self.verify()
            yield

    def verify(self) -> None:
        if self.revoked:
            raise _unavailable()
        cancellation_checkpoint()
        self.reader.verify_source(self.path, self.fingerprint)


class RootsMagicWorkbench:
    """Retain at most eight private immutable sources until explicit revocation."""

    def __init__(self) -> None:
        self._sources: dict[str, _SourceSession] = {}
        self._lock = RLock()
        self._closed = False

    def inspect(
        self,
        path: Path,
        *,
        size_bytes: int,
        sha256: str,
        approval: tuple[PickerFileApproval, PickerFileApproval | None, PickerFileApproval | None]
        | None = None,
    ) -> RootsMagicSourceSummary:
        """Verify a picker-authorized source and issue a fresh session capability."""
        reader = RootsMagicReader(allowed_directories=[path.parent], max_rows=100)
        selected = reader.resolve_tree(path)
        fingerprint = reader.fingerprint_source(selected)
        if fingerprint.snapshot.size != size_bytes or fingerprint.sha256 != sha256:
            raise AncestryError(
                "FILE_INPUT_CHANGED", "The selected source changed before inspection."
            )
        if approval is not None:
            components = (
                (fingerprint, None, None)
                if isinstance(fingerprint, FileFingerprint)
                else (fingerprint.main, fingerprint.wal, fingerprint.shm)
            )
            for suffix, approved, captured in zip(
                ("", "-wal", "-shm"), approval, components, strict=True
            ):
                if (approved is None) != (captured is None):
                    raise AncestryError("FILE_INPUT_CHANGED", "The selected source changed.")
                if approved is not None and captured is not None:
                    approved.verify(selected.with_name(selected.name + suffix), captured)
        RootsMagicPresetService(reader).validate_capabilities(selected, "people")
        reader.verify_source(selected, fingerprint)
        cancellation_checkpoint()
        with self._lock:
            if self._closed:
                raise _unavailable()
            if len(self._sources) >= 8:
                raise AncestryError(
                    "ROOTSMAGIC_SOURCE_CAPACITY", "Discard a source before selecting another."
                )
            source_ref = secrets.token_hex(32)
            summary = RootsMagicSourceSummary(
                source_ref,
                sanitized_source_name(selected.name),
                source_digest(fingerprint),
                "unknown",
                "active",
                True,
            )
            self._sources[source_ref] = _SourceSession(selected, reader, fingerprint, summary)
            return summary

    def source(self, source_ref: str) -> _SourceSession:
        """Resolve a capability for trusted in-process operations only."""
        with self._lock:
            source = self._sources.get(source_ref)
            if self._closed or source is None or source.revoked:
                raise _unavailable()
            return source

    def query(self, request: RootsMagicPresetQueryRequest) -> RootsMagicResultPage:
        """Run one bounded preset, retaining no unbounded genealogy payload."""
        source = self.source(request.source_ref)
        with source.lock:
            source.verify()
            result = RootsMagicPresetService(source.reader).query(source.path, request)
            source.verify()
            return result

    def discard(self, source_ref: str) -> None:
        """Revoke the source, waiting only for an already-started publication."""
        with self._lock:
            source = self._sources.pop(source_ref, None)
            if source is not None:
                with source.publication_lock:
                    source.revoked = True

    def close(self) -> None:
        """Invalidate every source when the owning native sidecar shuts down."""
        with self._lock:
            self._closed = True
            for source in self._sources.values():
                with source.publication_lock:
                    source.revoked = True
            self._sources.clear()
