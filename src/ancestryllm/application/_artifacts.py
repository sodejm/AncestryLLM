"""Private adapter-owned artifact capabilities and safe publication.

Only trusted input adapters construct grants or resolve them to host paths.
Public service requests and results carry the opaque references from
``application.dto`` instead.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from ancestryllm.application.dto import (
    MAX_ARTIFACT_BYTES,
    ArtifactAccess,
    ArtifactGrantRef,
    ArtifactRef,
    ArtifactStatus,
)
from ancestryllm.application.ports import CancellationPort
from ancestryllm.core.publication import (
    claim_staged_path,
    cleanup_staged_path,
    publish_staged_bundle,
    staging_path,
    write_staged_bytes,
)
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode


@dataclass(frozen=True, slots=True)
class _Grant:
    """Private binding between one capability and one verified host path."""

    path: Path
    operation: str
    access: ArtifactAccess
    media_type: str
    artifact_type: str
    identity: tuple[int, int, int, int, int] | None


class _ArtifactRegistry:
    """Issue process-local grants and own all path resolution/publication."""

    __slots__ = ("_grants",)

    def __init__(self) -> None:
        self._grants: dict[str, _Grant] = {}

    @staticmethod
    def _grant_id() -> str:
        return f"grt_{secrets.token_hex(32)}"

    @staticmethod
    def _artifact_id() -> str:
        return f"art_{secrets.token_hex(32)}"

    def grant_input(
        self,
        path: Path,
        *,
        operation: str,
        media_type: str,
        artifact_type: str,
    ) -> ArtifactGrantRef:
        """Grant one operation read access to a current regular file."""

        try:
            resolved = path.resolve(strict=True)
            metadata = os.lstat(resolved)
        except (OSError, RuntimeError) as exc:
            raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID) from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID)
        if metadata.st_size > MAX_ARTIFACT_BYTES:
            raise DomainFailure(DomainFailureCode.ARTIFACT_TOO_LARGE)
        return self._issue(
            resolved,
            operation=operation,
            access=ArtifactAccess.READ,
            media_type=media_type,
            artifact_type=artifact_type,
            identity=self._identity(metadata),
        )

    def grant_output(
        self,
        path: Path,
        *,
        operation: str,
        media_type: str,
        artifact_type: str,
    ) -> ArtifactGrantRef:
        """Grant one operation write access without exposing the destination."""

        try:
            parent = path.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID) from exc
        if not parent.is_dir() or path.name in {"", ".", ".."}:
            raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID)
        target = parent / path.name
        return self._issue(
            target,
            operation=operation,
            access=ArtifactAccess.WRITE,
            media_type=media_type,
            artifact_type=artifact_type,
            identity=None,
        )

    def _issue(
        self,
        path: Path,
        *,
        operation: str,
        access: ArtifactAccess,
        media_type: str,
        artifact_type: str,
        identity: tuple[int, int, int, int, int] | None,
    ) -> ArtifactGrantRef:
        grant = ArtifactGrantRef(self._grant_id(), operation, access)
        self._grants[grant.grant_id] = _Grant(
            path,
            operation,
            access,
            media_type,
            artifact_type,
            identity,
        )
        return grant

    @staticmethod
    def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
        )

    def revoke(self, grant: ArtifactGrantRef) -> None:
        """Revoke one capability without touching its host artifact."""

        self._grants.pop(grant.grant_id, None)

    def resolve(
        self,
        grant: ArtifactGrantRef,
        *,
        operation: str,
        access: ArtifactAccess,
    ) -> Path:
        """Resolve a capability only for its issuing operation and access."""

        binding = self._grants.get(grant.grant_id)
        if (
            binding is None
            or grant.operation != operation
            or binding.operation != operation
            or grant.access is not access
            or binding.access is not access
        ):
            raise DomainFailure(DomainFailureCode.ARTIFACT_FORBIDDEN)
        if access is ArtifactAccess.READ:
            try:
                metadata = os.lstat(binding.path)
            except OSError as exc:
                raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID) from exc
            if not stat.S_ISREG(metadata.st_mode):
                raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID)
            if metadata.st_size > MAX_ARTIFACT_BYTES:
                raise DomainFailure(DomainFailureCode.ARTIFACT_TOO_LARGE)
            if binding.identity != self._identity(metadata):
                raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID)
        return binding.path

    def describe_input(
        self,
        grant: ArtifactGrantRef,
        *,
        operation: str,
    ) -> ArtifactRef:
        """Create a public descriptor for one currently granted input."""

        path = self.resolve(grant, operation=operation, access=ArtifactAccess.READ)
        binding = self._grants[grant.grant_id]
        return self._describe(path, binding)

    def publish_bytes(
        self,
        grant: ArtifactGrantRef,
        payload: bytes,
        *,
        operation: str,
        cancellation: CancellationPort,
    ) -> ArtifactRef:
        """Stage, cancel-check, and atomically publish one granted output."""

        if len(payload) > MAX_ARTIFACT_BYTES:
            raise DomainFailure(DomainFailureCode.ARTIFACT_TOO_LARGE)
        target = self.resolve(grant, operation=operation, access=ArtifactAccess.WRITE)
        binding = self._grants[grant.grant_id]
        staged: Path | None = None
        try:
            staged = staging_path(target)
            token = write_staged_bytes(staged, payload)
            claim_staged_path(staged, token)
            cancellation.check_cancelled()
            publish_staged_bundle(((staged, target),), replace=os.replace)
            staged = None
            return self._describe(target, binding)
        except DomainFailure:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise DomainFailure(DomainFailureCode.PUBLICATION_FAILED) from exc
        finally:
            if staged is not None:
                cleanup_staged_path(staged)

    def publish_text(
        self,
        grant: ArtifactGrantRef,
        payload: str,
        *,
        operation: str,
        cancellation: CancellationPort,
    ) -> ArtifactRef:
        """Encode UTF-8 text and publish through the same safe boundary."""

        return self.publish_bytes(
            grant,
            payload.encode("utf-8"),
            operation=operation,
            cancellation=cancellation,
        )

    def _describe(self, path: Path, binding: _Grant) -> ArtifactRef:
        flags = os.O_RDONLY
        for flag_name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK", "O_BINARY", "O_NOINHERIT"):
            flags |= getattr(os, flag_name, 0)
        try:
            before = os.lstat(path)
            if not stat.S_ISREG(before.st_mode):
                raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID)
            if before.st_size > MAX_ARTIFACT_BYTES:
                raise DomainFailure(DomainFailureCode.ARTIFACT_TOO_LARGE)
            if binding.identity is not None and binding.identity != self._identity(before):
                raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID)
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID) from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or self._identity(opened) != self._identity(before):
                raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID)
            digest = hashlib.sha256()
            size_bytes = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > MAX_ARTIFACT_BYTES:
                    raise DomainFailure(DomainFailureCode.ARTIFACT_TOO_LARGE)
                digest.update(chunk)
            after = os.fstat(descriptor)
            if self._identity(after) != self._identity(opened) or size_bytes != opened.st_size:
                raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID)
        except OSError as exc:
            raise DomainFailure(DomainFailureCode.ARTIFACT_INVALID) from exc
        finally:
            os.close(descriptor)
        return ArtifactRef(
            artifact_id=self._artifact_id(),
            media_type=binding.media_type,
            artifact_type=binding.artifact_type,
            size_bytes=size_bytes,
            status=ArtifactStatus.READY,
            sha256=digest.hexdigest(),
        )


__all__ = ["_ArtifactRegistry"]
