"""Small fail-closed primitives shared by the reviewed container entrypoints."""

from __future__ import annotations

import errno
import os
import secrets
from contextlib import suppress
from pathlib import Path

_MAX_RUNTIME_VALUE_BYTES = 4_096
PROBE_PORT = 8_000
PROBE_TOKEN_PATH = Path("/run/ancestryllm/probe-token")
WORKER_READY_PATH = Path("/run/ancestryllm/worker-ready")


class ContainerRuntimeError(RuntimeError):
    """A sanitized, stable container startup or evidence failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _io_error(exc: OSError) -> ContainerRuntimeError:
    if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
        return ContainerRuntimeError(
            "CONTAINER_RUNTIME_DISK_FULL",
            "The private runtime filesystem has no space available.",
        )
    if exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
        return ContainerRuntimeError(
            "CONTAINER_RUNTIME_READ_ONLY",
            "The private runtime filesystem is not writable by the container user.",
        )
    return ContainerRuntimeError(
        "CONTAINER_RUNTIME_IO_FAILED",
        "The private runtime file could not be published safely.",
    )


def publish_private_runtime_file(target: Path, payload: str) -> None:
    """Atomically publish one bounded owner-only value without exposing it in errors."""

    encoded = payload.encode("utf-8")
    if not encoded or len(encoded) > _MAX_RUNTIME_VALUE_BYTES or b"\x00" in encoded:
        raise ContainerRuntimeError(
            "CONTAINER_RUNTIME_VALUE_INVALID",
            "The private runtime value is empty, oversized, or malformed.",
        )
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(encoded):
            count = os.write(descriptor, encoded[written:])
            if count <= 0:
                raise OSError(errno.EIO, "short write")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        temporary.replace(target)
    except OSError as exc:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise _io_error(exc) from exc


def read_private_runtime_file(target: Path) -> str:
    """Read one bounded runtime value and return only sanitized failure details."""

    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            value = os.read(descriptor, _MAX_RUNTIME_VALUE_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise _io_error(exc) from exc
    if not value or len(value) > _MAX_RUNTIME_VALUE_BYTES or b"\x00" in value:
        raise ContainerRuntimeError(
            "CONTAINER_RUNTIME_VALUE_INVALID",
            "The private runtime value is empty, oversized, or malformed.",
        )
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContainerRuntimeError(
            "CONTAINER_RUNTIME_VALUE_INVALID",
            "The private runtime value is not valid UTF-8.",
        ) from exc


def remove_private_runtime_file(target: Path) -> None:
    """Remove an ephemeral runtime value without following links or touching data storage."""

    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        raise _io_error(exc) from exc


__all__ = [
    "PROBE_PORT",
    "PROBE_TOKEN_PATH",
    "WORKER_READY_PATH",
    "ContainerRuntimeError",
    "publish_private_runtime_file",
    "read_private_runtime_file",
    "remove_private_runtime_file",
]
