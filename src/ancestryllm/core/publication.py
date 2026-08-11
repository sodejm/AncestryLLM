"""Alias-safe, rollback-capable publication of related output artifacts."""

from __future__ import annotations

import errno
import hashlib
import importlib
import os
import secrets
import stat
import sys
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ancestryllm.core.cancellation import cancellation_checkpoint, non_interruptible_section

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


@dataclass(frozen=True, slots=True)
class _PathIdentity:
    """Stable identity and mutation fields for one filesystem object."""

    device: int
    inode: int
    file_type: int
    size: int
    modified_ns: int
    changed_ns: int
    created_ns: int | None

    @classmethod
    def from_stat(cls, value: os.stat_result) -> _PathIdentity:
        birthtime_ns = getattr(value, "st_birthtime_ns", None)
        if birthtime_ns is None:
            birthtime = getattr(value, "st_birthtime", None)
            birthtime_ns = int(birthtime * 1_000_000_000) if birthtime is not None else None
        return cls(
            device=value.st_dev,
            inode=value.st_ino,
            file_type=stat.S_IFMT(value.st_mode),
            size=value.st_size,
            modified_ns=value.st_mtime_ns,
            changed_ns=value.st_ctime_ns,
            created_ns=birthtime_ns,
        )

    def same_object(self, other: _PathIdentity) -> bool:
        """Compare an object generation without accepting an inode reuse."""

        if not self.same_file_id(other):
            return False
        if self.created_ns is not None:
            return self.created_ns == other.created_ns
        return self.changed_ns == other.changed_ns

    def same_file_id(self, other: _PathIdentity) -> bool:
        """Compare the filesystem id used only across one controlled rename/link."""

        if self.inode <= 0 or other.inode <= 0:
            return False
        if (self.device, self.inode, self.file_type) != (
            other.device,
            other.inode,
            other.file_type,
        ):
            return False
        if (self.created_ns is None) != (other.created_ns is None):
            return False
        return self.created_ns is None or self.created_ns == other.created_ns

    def unchanged(self, other: _PathIdentity) -> bool:
        """Compare identity and content fields unaffected by link/rename bookkeeping."""

        return self.same_file_id(other) and (
            self.size,
            self.modified_ns,
        ) == (
            other.size,
            other.modified_ns,
        )

    def pristine(self, other: _PathIdentity) -> bool:
        """Compare fields that must not change after a staged file is sealed."""

        return self.unchanged(other) and self.changed_ns == other.changed_ns

    def same_observation(self, other: _PathIdentity) -> bool:
        """Compare every available field, including unreliable zero inode values."""

        return (
            self.device,
            self.inode,
            self.file_type,
            self.size,
            self.modified_ns,
            self.changed_ns,
            self.created_ns,
        ) == (
            other.device,
            other.inode,
            other.file_type,
            other.size,
            other.modified_ns,
            other.changed_ns,
            other.created_ns,
        )


@dataclass(frozen=True, slots=True)
class StagedFileToken:
    """Opaque identity captured from an atomic writer's temporary descriptor."""

    _identity: _PathIdentity
    _sha256: bytes


@dataclass(frozen=True, slots=True)
class _OwnedPath:
    path: Path
    identity: _PathIdentity
    digest: bytes | None = None


@dataclass(slots=True)
class _Artifact:
    source: _OwnedPath
    target: Path
    original_target: _PathIdentity | None
    backup: _OwnedPath | None = None
    displaced: _OwnedPath | None = None
    published: _OwnedPath | None = None
    restored: _OwnedPath | None = None
    displacement_attempted: bool = False


@dataclass(frozen=True, slots=True)
class _StagingReservation:
    identity: _PathIdentity
    sealed: bool
    digest: bytes | None = None


@dataclass(frozen=True, slots=True)
class _PrivateQuarantine:
    directory: Path
    path: Path
    descriptor: int | None
    identity: _PathIdentity


@dataclass(slots=True)
class _DirectoryCapability:
    path: Path
    descriptor: int
    identity: _PathIdentity


@dataclass(slots=True)
class _PreparedRegularInstall:
    """Caller-owned lifecycle for one interruptible private installation."""

    target: Path
    destination: _DirectoryCapability | None = None
    quarantine_directory: Path | None = None
    quarantine_identity: _PathIdentity | None = None
    quarantine_descriptor: int | None = None
    quarantine: _PrivateQuarantine | None = None
    candidate: _OwnedPath | None = None
    descriptor: int | None = None
    complete: bool = False
    active: bool = True


@dataclass(slots=True)
class _DisplacementLifecycle:
    """Caller-owned reservation state recorded before interruptible creation."""

    path: Path | None = None
    descriptor: int | None = None
    reservation: _OwnedPath | None = None
    transferred: bool = False


_STAGING_IDENTITIES: dict[str, _StagingReservation] = {}
_STAGING_LOCK = threading.Lock()
_NATIVE_REPLACE = os.replace
_NATIVE_UNLINK = os.unlink
_PLATFORM = sys.platform


def _portable_path_key(path: Path) -> tuple[str, ...]:
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in path.parts)


def _path_key(path: Path) -> str:
    return os.path.abspath(os.fspath(path))


def _identity(path: Path) -> _PathIdentity:
    return _PathIdentity.from_stat(os.lstat(path))


def _identity_or_none(path: Path) -> _PathIdentity | None:
    """Return ``None`` only for genuine absence; every other lookup error is unsafe."""

    try:
        return _identity(path)
    except FileNotFoundError:
        return None


def _regular_identity(path: Path) -> _PathIdentity:
    identity = _identity(path)
    if identity.file_type != stat.S_IFREG:
        raise OSError("Publication staging files must remain regular files.")
    return identity


def _assert_unchanged(path: Path, expected: _PathIdentity) -> None:
    try:
        actual = _identity(path)
    except FileNotFoundError as exc:
        raise OSError("A publication pathname disappeared during the operation.") from exc
    if not expected.unchanged(actual):
        raise OSError("A publication pathname changed during the operation.")


def _assert_pristine(path: Path, expected: _PathIdentity) -> None:
    try:
        actual = _identity(path)
    except FileNotFoundError as exc:
        raise OSError("A publication pathname disappeared during the operation.") from exc
    if not expected.pristine(actual):
        raise OSError("A sealed publication pathname changed during the operation.")


def _assert_same_object(path: Path, expected: _PathIdentity) -> None:
    try:
        actual = _identity(path)
    except FileNotFoundError as exc:
        raise OSError("A publication pathname disappeared during the operation.") from exc
    if not expected.same_object(actual):
        raise OSError("A publication pathname was replaced during the operation.")


def _descriptor_matches_path(
    descriptor_identity: _PathIdentity,
    path_identity: _PathIdentity,
) -> bool:
    if descriptor_identity.inode > 0:
        return descriptor_identity.pristine(path_identity)
    return descriptor_identity.same_observation(path_identity)


def _descriptor_survived_move(
    before: _PathIdentity,
    after: _PathIdentity,
) -> bool:
    """Verify a held descriptor across a rename that may update ctime."""

    return (
        before.device,
        before.inode,
        before.file_type,
        before.size,
        before.modified_ns,
        before.created_ns,
    ) == (
        after.device,
        after.inode,
        after.file_type,
        after.size,
        after.modified_ns,
        after.created_ns,
    )


def _supports_directory_fd_cleanup() -> bool:
    """Return whether cleanup can bind both lookup and unlink to one directory."""

    return (
        hasattr(os, "O_DIRECTORY")
        and os.stat in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
    )


def _remove_directory_if_owned(path: Path, expected: _PathIdentity) -> bool:
    """Remove an empty directory only through a matching filesystem capability."""

    if expected.file_type != stat.S_IFDIR:
        return False
    if os.rmdir in os.supports_dir_fd and os.stat in os.supports_dir_fd:
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        parent_descriptor: int | None = None
        try:
            parent_descriptor = os.open(path.parent, flags)
            actual = _PathIdentity.from_stat(
                os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            )
            if expected.inode > 0:
                matches = expected.same_file_id(actual)
            else:
                matches = expected.same_observation(actual)
            if not matches:
                return False
            os.rmdir(path.name, dir_fd=parent_descriptor)
            return True
        except (FileNotFoundError, OSError):
            return False
        finally:
            if parent_descriptor is not None:
                try:
                    os.close(parent_descriptor)
                except OSError:
                    pass
    try:
        actual = _identity(path)
    except (FileNotFoundError, OSError):
        return False
    if expected.inode > 0:
        matches = expected.same_file_id(actual)
    else:
        matches = expected.same_observation(actual)
    if not matches:
        return False
    return _windows_unlink_if_owned(path, actual)


def _create_private_quarantine(
    path: Path,
    prepared: _PreparedRegularInstall,
) -> None:
    """Create a private directory under ownership registered before ``mkdir``.

    ``tempfile.mkdtemp`` creates a directory before its pathname can be stored
    by the caller.  A process-level ``KeyboardInterrupt`` in that return window
    would therefore leave an untracked directory.  Select the unpredictable
    name first, record it in the caller-owned lifecycle, and only then perform
    the interruptible creation.
    """

    directory: Path | None = None
    for _attempt in range(100):
        candidate = _candidate(path, "quarantine")
        prepared.quarantine_directory = candidate
        prepared.quarantine_identity = None
        prepared.quarantine_descriptor = None
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            prepared.quarantine_directory = None
            continue
        directory = candidate
        break
    if directory is None:
        raise OSError("A private publication directory could not be reserved safely.")

    identity: _PathIdentity | None = None
    descriptor: int | None = None
    try:
        identity = _identity(directory)
        prepared.quarantine_identity = identity
        directory.chmod(0o700)
        current = _identity(directory)
        if identity.inode > 0 and not identity.same_file_id(current):
            raise OSError("A private publication directory changed during creation.")
        identity = current
        prepared.quarantine_identity = identity
        if _supports_directory_fd_cleanup():
            flags = os.O_RDONLY
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_DIRECTORY"):
                flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(directory, flags)
            prepared.quarantine_descriptor = descriptor
            opened = _PathIdentity.from_stat(os.fstat(descriptor))
            at_path = _identity(directory)
            if not _descriptor_matches_path(opened, at_path):
                raise OSError("A private publication directory changed while opening.")
            identity = at_path
            prepared.quarantine_identity = identity
        prepared.quarantine = _PrivateQuarantine(
            directory,
            directory / "owned",
            descriptor,
            identity,
        )
        return
    except BaseException:
        if descriptor is not None:
            prepared.quarantine_descriptor = None
            try:
                os.close(descriptor)
            except BaseException:  # noqa: BLE001, S110 - creation failure is authoritative
                pass
        try:
            removal_identity = identity or _identity(directory)
            _remove_directory_if_owned(directory, removal_identity)
        except BaseException:  # noqa: BLE001, S110 - preserve the creation failure
            pass
        raise


def _quarantine_identity(quarantine: _PrivateQuarantine) -> _PathIdentity:
    if quarantine.descriptor is not None:
        return _PathIdentity.from_stat(
            os.stat(
                "owned",
                dir_fd=quarantine.descriptor,
                follow_symlinks=False,
            )
        )
    return _identity(quarantine.path)


def _close_quarantine(quarantine: _PrivateQuarantine) -> None:
    removal_identity: _PathIdentity | None = None
    if quarantine.descriptor is not None:
        try:
            held = _PathIdentity.from_stat(os.fstat(quarantine.descriptor))
            at_path = _identity(quarantine.directory)
            if _descriptor_matches_path(held, at_path):
                removal_identity = at_path
        except OSError:
            pass
        try:
            os.close(quarantine.descriptor)
        except OSError:
            pass
    else:
        try:
            at_path = _identity(quarantine.directory)
            if quarantine.identity.same_file_id(at_path):
                removal_identity = at_path
        except OSError:
            pass
    if removal_identity is not None:
        _remove_directory_if_owned(quarantine.directory, removal_identity)


def _windows_unlink_if_owned(path: Path, expected: _PathIdentity) -> bool:
    """Delete one Windows filesystem object through its verified native handle."""

    if os.name != "nt":
        return False

    ctypes = importlib.import_module("ctypes")
    wintypes = importlib.import_module("ctypes.wintypes")
    msvcrt = importlib.import_module("msvcrt")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
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
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_file_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    file_disposition_info = 4
    invalid_handle = ctypes.c_void_p(-1).value

    handle = create_file(
        os.fspath(path),
        delete_access | file_read_attributes,
        share_read_write_delete,
        None,
        open_existing,
        open_reparse_point | backup_semantics,
        None,
    )
    handle_value = ctypes.cast(handle, ctypes.c_void_p).value
    if handle_value in {None, invalid_handle}:
        return False

    descriptor: int | None = None
    try:
        descriptor = msvcrt.open_osfhandle(
            handle_value,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0),
        )
        handle = None
        opened = _PathIdentity.from_stat(os.fstat(descriptor))
        if not expected.pristine(opened):
            return False
        delete_file = ctypes.c_ubyte(1)
        if not set_file_information(
            msvcrt.get_osfhandle(descriptor),
            file_disposition_info,
            ctypes.byref(delete_file),
            ctypes.sizeof(delete_file),
        ):
            return False
        return True
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        elif handle is not None:
            close_handle(handle)


def _unlink_without_directory_fd(path: Path, expected: _PathIdentity) -> bool:
    """Fail closed unless the platform offers identity-bound deletion by handle."""

    return _windows_unlink_if_owned(path, expected)


def _discard_quarantined_path(
    quarantine: _PrivateQuarantine,
    expected: _PathIdentity,
    descriptor: int | None,
) -> bool:
    """Delete only inside an unexposed private directory after fd binding."""

    try:
        actual = _quarantine_identity(quarantine)
        if descriptor is not None:
            opened = _PathIdentity.from_stat(os.fstat(descriptor))
            if not _descriptor_matches_path(opened, actual):
                return False
        elif not expected.pristine(actual):
            return False
        if quarantine.descriptor is not None:
            _NATIVE_UNLINK("owned", dir_fd=quarantine.descriptor)
            return True
        return _unlink_without_directory_fd(quarantine.path, actual)
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _restore_quarantined_path(
    quarantine: _PrivateQuarantine,
    source: _OwnedPath,
    target: Path,
) -> bool:
    """Best-effort no-clobber restoration of a path moved during cleanup."""

    try:
        restored = _install_no_clobber(source, target)
    except OSError:
        return False
    actual = _identity_or_none(target)
    if actual is None or not restored.identity.pristine(actual):
        return False
    _discard_quarantined_path(quarantine, source.identity, None)
    return True


def _restore_open_descriptor(
    source_descriptor: int,
    source_identity: _PathIdentity,
    target: Path,
) -> bool:
    """Restore a quarantined regular file when pathname identity lookup failed."""

    if source_identity.file_type != stat.S_IFREG:
        return False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    source_stat = os.fstat(source_descriptor)
    destination_descriptor: int | None = None
    destination_identity: _PathIdentity | None = None
    original_offset = os.lseek(source_descriptor, 0, os.SEEK_CUR)
    try:
        destination_descriptor = os.open(
            target,
            flags,
            stat.S_IMODE(source_stat.st_mode),
        )
        os.lseek(source_descriptor, 0, os.SEEK_SET)
        while chunk := os.read(source_descriptor, 1024 * 1024):
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(destination_descriptor, remaining)
                if written <= 0:
                    raise OSError("A recovery copy made no write progress.")
                remaining = remaining[written:]
        if hasattr(os, "fchmod"):
            os.fchmod(destination_descriptor, stat.S_IMODE(source_stat.st_mode))
        if os.utime in os.supports_fd:
            os.utime(
                destination_descriptor,
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            )
        else:
            os.utime(
                target,
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
                follow_symlinks=False,
            )
        os.fsync(destination_descriptor)
        restored = _PathIdentity.from_stat(os.fstat(destination_descriptor))
        destination_identity = restored
        if not _descriptor_matches_path(restored, _identity(target)):
            raise OSError("A recovery destination changed while it was copied.")
        os.close(destination_descriptor)
        destination_descriptor = None
        return True
    except (
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        KeyboardInterrupt,
        SystemExit,
    ):
        if destination_descriptor is not None:
            if destination_identity is None:
                try:
                    destination_identity = _PathIdentity.from_stat(os.fstat(destination_descriptor))
                except (
                    AttributeError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    KeyboardInterrupt,
                    SystemExit,
                ):
                    pass
            try:
                cleanup_open_path(target, destination_descriptor)
            except (
                AttributeError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                KeyboardInterrupt,
                SystemExit,
            ):
                pass
            try:
                os.close(destination_descriptor)
            except (
                AttributeError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                KeyboardInterrupt,
                SystemExit,
            ):
                pass
            if destination_identity is not None:
                _unlink_if_owned(target, destination_identity)
        return False
    finally:
        try:
            os.lseek(source_descriptor, original_offset, os.SEEK_SET)
        except OSError:
            pass


def _unlink_if_owned(
    path: Path,
    expected: _PathIdentity,
    *,
    descriptor: int | None = None,
) -> bool:
    """Unlink only when ``path`` still names the object retained by the caller."""

    owned_descriptor = descriptor
    close_descriptor = False
    try:
        actual = _identity(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if descriptor is None and expected.inode <= 0:
        return False
    if descriptor is None and actual.file_type == stat.S_IFREG and _supports_directory_fd_cleanup():
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            owned_descriptor = os.open(path, flags)
        except OSError:
            return False
        close_descriptor = True
    try:
        if owned_descriptor is not None:
            try:
                opened = _PathIdentity.from_stat(os.fstat(owned_descriptor))
            except OSError:
                return False
            if not _descriptor_matches_path(expected, opened) or not _descriptor_matches_path(
                opened, actual
            ):
                return False
        elif not expected.pristine(actual):
            return False

        cleanup_lifecycle = _PreparedRegularInstall(path)
        try:
            _create_private_quarantine(path, cleanup_lifecycle)
        except OSError:
            return False
        quarantine = cleanup_lifecycle.quarantine
        if quarantine is None:
            return False
        try:
            try:
                _NATIVE_REPLACE(path, quarantine.path)
            except FileNotFoundError:
                return True
            except OSError:
                return False

            try:
                moved_identity = _identity(quarantine.path)
            except OSError:
                try:
                    moved_identity = _quarantine_identity(quarantine)
                except OSError:
                    if owned_descriptor is not None:
                        try:
                            after_move = _PathIdentity.from_stat(os.fstat(owned_descriptor))
                        except OSError:
                            return False
                        if _descriptor_survived_move(opened, after_move):
                            _restore_open_descriptor(
                                owned_descriptor,
                                after_move,
                                path,
                            )
                    return False
            moved = _OwnedPath(quarantine.path, moved_identity)
            if owned_descriptor is not None:
                try:
                    after_move = _PathIdentity.from_stat(os.fstat(owned_descriptor))
                except OSError:
                    _restore_quarantined_path(quarantine, moved, path)
                    return False
                if not _descriptor_survived_move(
                    opened,
                    after_move,
                ) or not _descriptor_matches_path(after_move, moved.identity):
                    _restore_quarantined_path(quarantine, moved, path)
                    return False
            elif not expected.unchanged(moved.identity):
                _restore_quarantined_path(quarantine, moved, path)
                return False
            return _discard_quarantined_path(
                quarantine,
                moved.identity,
                owned_descriptor,
            )
        finally:
            _close_quarantine(quarantine)
            cleanup_lifecycle.quarantine = None
            cleanup_lifecycle.quarantine_descriptor = None
    finally:
        if close_descriptor and owned_descriptor is not None:
            try:
                os.close(owned_descriptor)
            except OSError:
                pass


def cleanup_open_path(path: Path, descriptor: int) -> bool:
    """Quarantine and remove only the path still bound to an open descriptor."""

    try:
        identity = _PathIdentity.from_stat(os.fstat(descriptor))
    except OSError:
        return False
    return _unlink_if_owned(path, identity, descriptor=descriptor)


def _descriptor_digest(descriptor: int) -> bytes:
    """Hash a regular descriptor without changing its caller-visible offset."""

    original_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
    digest = hashlib.sha256()
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.lseek(descriptor, original_offset, os.SEEK_SET)
    return digest.digest()


def staged_file_token(descriptor: int) -> StagedFileToken:
    """Capture the regular-file identity an atomic writer is about to rename."""

    identity = _PathIdentity.from_stat(os.fstat(descriptor))
    if identity.file_type != stat.S_IFREG:
        raise OSError("Atomic publication writers must emit regular files.")
    if identity.inode <= 0:
        raise OSError("The filesystem does not expose reliable publication identities.")
    return StagedFileToken(identity, _descriptor_digest(descriptor))


def seal_staged_path(path: Path) -> StagedFileToken:
    """Seal a reservation written by a trusted path-based native library.

    The reserved inode must remain the same object throughout the external
    write and this verification pass. This exists for libraries such as
    SQLCipher whose backup API accepts only a pathname.
    """

    key = _path_key(path)
    with _STAGING_LOCK:
        reservation = _STAGING_IDENTITIES.get(key)
    if reservation is None:
        raise OSError("The publication staging pathname was not reserved by this process.")
    if reservation.sealed:
        raise OSError("The publication staging pathname is already sealed.")

    flags = os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = _PathIdentity.from_stat(os.fstat(descriptor))
        at_path = _identity(path)
        if not reservation.identity.same_file_id(before) or not before.pristine(at_path):
            raise OSError("The publication staging reservation changed while it was written.")
        os.fsync(descriptor)
        digest = _descriptor_digest(descriptor)
        after = _PathIdentity.from_stat(os.fstat(descriptor))
        final_at_path = _identity(path)
        if not before.pristine(after) or not after.pristine(final_at_path):
            raise OSError("The publication staging payload changed while it was sealed.")
        with _STAGING_LOCK:
            if _STAGING_IDENTITIES.get(key) != reservation:
                raise OSError("The publication staging ownership record changed while sealing.")
            _STAGING_IDENTITIES[key] = _StagingReservation(
                after,
                sealed=True,
                digest=digest,
            )
        return StagedFileToken(after, digest)
    finally:
        os.close(descriptor)


def cleanup_staged_token(path: Path, token: StagedFileToken) -> bool:
    """Remove only the temporary path still represented by ``token``."""

    return _unlink_if_owned(path, token._identity)


def paths_alias(left: str | Path, right: str | Path) -> bool:
    """Return whether two path spellings designate the same filesystem object."""

    try:
        first = Path(left).expanduser()
        second = Path(right).expanduser()
        first_resolved = first.resolve(strict=False)
        second_resolved = second.resolve(strict=False)
        if first_resolved == second_resolved:
            return True
        if _portable_path_key(first_resolved) == _portable_path_key(second_resolved):
            return True
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return True
    try:
        return os.path.samefile(first, second)
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError, ValueError):
        return True


def _cleanup_unreliable_reservation(path: Path, descriptor: int) -> None:
    """Remove a just-created reservation only when every observable field agrees.

    This narrow fallback runs before the unguessable pathname has been exposed
    to caller code. It lets zero-inode filesystems fail cleanly while refusing
    to delete a path whose observation changed.
    """

    cleanup_open_path(path, descriptor)


def staging_path(target: Path) -> Path:
    """Reserve a same-directory temporary name and retain its filesystem identity."""

    descriptor, raw_path = tempfile.mkstemp(
        prefix=".ancestry-publish-",
        dir=target.parent,
    )
    path = Path(raw_path)
    try:
        identity = _PathIdentity.from_stat(os.fstat(descriptor))
        if identity.inode <= 0:
            _cleanup_unreliable_reservation(path, descriptor)
            raise OSError("The filesystem does not expose reliable publication identities.")
    finally:
        os.close(descriptor)
    with _STAGING_LOCK:
        _STAGING_IDENTITIES[_path_key(path)] = _StagingReservation(identity, sealed=False)
    return path


def is_staging_path(path: Path) -> bool:
    """Return whether ``path`` is a live reservation owned by this process."""

    with _STAGING_LOCK:
        return _path_key(path) in _STAGING_IDENTITIES


def _refresh_failed_staged_write(
    path: Path,
    reservation: _StagingReservation,
    descriptor: int,
) -> None:
    """Bind a partially written reservation so cleanup can safely retry."""

    try:
        opened = _PathIdentity.from_stat(os.fstat(descriptor))
        at_path = _identity(path)
    except OSError:
        return
    if not _descriptor_matches_path(opened, at_path):
        return
    with _STAGING_LOCK:
        if _STAGING_IDENTITIES.get(_path_key(path)) == reservation:
            _STAGING_IDENTITIES[_path_key(path)] = _StagingReservation(
                opened,
                sealed=False,
            )


def _cleanup_failed_staged_write(
    path: Path,
    reservation: _StagingReservation,
    descriptor: int,
    *,
    owned: bool,
) -> None:
    if owned:
        _refresh_failed_staged_write(path, reservation, descriptor)
        try:
            cleanup_open_path(path, descriptor)
        except (
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            KeyboardInterrupt,
            SystemExit,
        ):
            pass


def _cleanup_failed_staged_path(path: Path) -> None:
    try:
        cleanup_staged_path(path)
    except (
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        KeyboardInterrupt,
        SystemExit,
    ):
        pass


def write_staged_bytes(path: Path, payload: bytes) -> StagedFileToken:
    """Write and seal an existing reservation through its verified descriptor."""

    key = _path_key(path)
    with _STAGING_LOCK:
        reservation = _STAGING_IDENTITIES.get(key)
    if reservation is None:
        raise OSError("The publication staging pathname was not reserved by this process.")
    if reservation.sealed:
        raise OSError("The publication staging pathname is already sealed.")

    flags = os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor = os.open(path, flags)
    close_attempted = False
    owned = False
    try:
        opened = _PathIdentity.from_stat(os.fstat(descriptor))
        at_path = _identity(path)
        if not reservation.identity.pristine(opened) or not _descriptor_matches_path(
            opened,
            at_path,
        ):
            raise OSError("The publication staging reservation was replaced before writing.")
        owned = True
        os.ftruncate(descriptor, 0)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("A staged publication write made no progress.")
            view = view[written:]
        os.fsync(descriptor)
        final_identity = _PathIdentity.from_stat(os.fstat(descriptor))
        final_at_path = _identity(path)
        if not _descriptor_matches_path(final_identity, final_at_path):
            raise OSError("The publication staging reservation changed while it was written.")
        digest = _descriptor_digest(descriptor)
        if digest != hashlib.sha256(payload).digest():
            raise OSError("The publication staging payload changed while it was written.")
        with _STAGING_LOCK:
            if _STAGING_IDENTITIES.get(key) != reservation:
                raise OSError("The publication staging ownership record changed while writing.")
            final_at_path = _identity(path)
            if not _descriptor_matches_path(final_identity, final_at_path):
                raise OSError("The publication staging reservation changed before sealing.")
            _STAGING_IDENTITIES[key] = _StagingReservation(
                final_identity,
                sealed=True,
                digest=digest,
            )
        token = StagedFileToken(final_identity, digest)
        close_attempted = True
        os.close(descriptor)
        return token
    except BaseException:
        _cleanup_failed_staged_write(
            path,
            reservation,
            descriptor,
            owned=owned,
        )
        if not close_attempted:
            try:
                os.close(descriptor)
            except (
                AttributeError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                KeyboardInterrupt,
                SystemExit,
            ):
                pass
        _cleanup_failed_staged_path(path)
        raise


def write_staged_text(path: Path, payload: str) -> StagedFileToken:
    """UTF-8 encode text and write it through a reserved staging descriptor."""

    return write_staged_bytes(path, payload.encode("utf-8"))


def _token_candidate(path: Path) -> tuple[_PathIdentity, bytes]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        before = _PathIdentity.from_stat(os.fstat(descriptor))
        if before.file_type != stat.S_IFREG:
            raise OSError("Publication staging files must remain regular files.")
        digest = _descriptor_digest(descriptor)
        after = _PathIdentity.from_stat(os.fstat(descriptor))
        at_path = _identity(path)
        if not before.pristine(after) or not after.pristine(at_path):
            raise OSError("The atomic writer result changed while it was being claimed.")
        return after, digest
    finally:
        os.close(descriptor)


def claim_staged_path(path: Path, token: StagedFileToken | None = None) -> None:
    """Claim the regular file atomically written into a staging reservation."""

    if token is None:
        raise OSError("A staged writer identity token is required.")
    identity, digest = _token_candidate(path)
    key = _path_key(path)
    with _STAGING_LOCK:
        reservation = _STAGING_IDENTITIES.get(key)
        if reservation is None:
            raise OSError("The publication staging pathname was not reserved by this process.")
        if not reservation.sealed or reservation.digest is None:
            raise OSError("The publication staging pathname was not sealed by its writer.")
        if not reservation.identity.pristine(identity):
            raise OSError("The publication staging pathname changed after it was claimed.")
        if (
            not token._identity.pristine(identity)
            or token._sha256 != digest
            or reservation.digest != digest
        ):
            raise OSError("The staged writer result changed before it could be claimed.")


def cleanup_staged_path(path: Path) -> bool:
    """Best-effort cleanup that preserves any replacement at a staging pathname."""

    key = _path_key(path)
    with _STAGING_LOCK:
        reservation = _STAGING_IDENTITIES.pop(key, None)
    if reservation is None:
        return False
    return _unlink_if_owned(path, reservation.identity)


def _claim_for_publication(path: Path) -> _OwnedPath:
    current, digest = _token_candidate(path)
    key = _path_key(path)
    with _STAGING_LOCK:
        reservation = _STAGING_IDENTITIES.get(key)
        if reservation is None:
            raise OSError("The publication staging pathname was not reserved by this process.")
        if not reservation.sealed or reservation.digest is None:
            raise OSError("The publication staging pathname was not sealed by its writer.")
        if not reservation.identity.pristine(current) or reservation.digest != digest:
            raise OSError("The publication staging pathname changed after it was sealed.")
    return _OwnedPath(path, current, digest)


def _candidate(target: Path, purpose: str) -> Path:
    return target.parent / f".ancestry-publish-{purpose}-{secrets.token_hex(16)}"


def _copy_regular_no_clobber(
    source: _OwnedPath,
    target: Path,
    *,
    owner: Callable[[_OwnedPath], None] | None = None,
) -> _OwnedPath:
    """Copy one verified regular file into an exclusive destination."""

    _assert_pristine(source.path, source.identity)
    source_flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        source_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        source_flags |= os.O_NONBLOCK
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        destination_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        destination_flags |= os.O_NOFOLLOW

    source_descriptor = os.open(source.path, source_flags)
    destination_descriptor: int | None = None
    destination_close_attempted = False
    source_close_attempted = False
    copied: _PathIdentity | None = None
    try:
        source_stat = os.fstat(source_descriptor)
        opened_source = _PathIdentity.from_stat(source_stat)
        if opened_source.file_type != stat.S_IFREG or not source.identity.pristine(opened_source):
            raise OSError("A publication source changed before it could be copied.")
        destination_descriptor = os.open(
            target,
            destination_flags,
            stat.S_IMODE(source_stat.st_mode),
        )
        digest = hashlib.sha256()
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(destination_descriptor, remaining)
                if written <= 0:
                    raise OSError("A publication copy made no write progress.")
                remaining = remaining[written:]
        if hasattr(os, "fchmod"):
            os.fchmod(destination_descriptor, stat.S_IMODE(source_stat.st_mode))
        if os.utime in os.supports_fd:
            os.utime(
                destination_descriptor,
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            )
        else:
            os.utime(
                target,
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
                follow_symlinks=False,
            )
        os.fsync(destination_descriptor)
        copied = _PathIdentity.from_stat(os.fstat(destination_descriptor))
        source_after = _PathIdentity.from_stat(os.fstat(source_descriptor))
        target_after = _identity(target)
        if not source.identity.pristine(source_after):
            raise OSError("A publication source changed while it was copied.")
        if source.digest is not None and digest.digest() != source.digest:
            raise OSError("A sealed publication source digest changed while it was copied.")
        _assert_pristine(source.path, source.identity)
        if not copied.pristine(target_after):
            raise OSError("A publication destination changed while it was copied.")
        result = _OwnedPath(target, copied, digest.digest())
        destination_close_attempted = True
        os.close(destination_descriptor)
        destination_descriptor = None
        source_close_attempted = True
        os.close(source_descriptor)
        source_descriptor = -1
        if owner is not None:
            # Transfer ownership before returning.  The caller's mutable
            # lifecycle therefore survives an asynchronous exception in the
            # callee-return/caller-assignment window.
            owner(result)
        return result
    except BaseException:
        if destination_descriptor is not None:
            if copied is None:
                try:
                    copied = _PathIdentity.from_stat(os.fstat(destination_descriptor))
                except (
                    AttributeError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    KeyboardInterrupt,
                    SystemExit,
                ):
                    pass
            try:
                cleanup_open_path(target, destination_descriptor)
            except (
                AttributeError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                KeyboardInterrupt,
                SystemExit,
            ):
                pass
            if not destination_close_attempted:
                try:
                    os.close(destination_descriptor)
                except (
                    AttributeError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    KeyboardInterrupt,
                    SystemExit,
                ):
                    pass
        if copied is not None:
            _unlink_if_owned(target, copied)
        if source_descriptor >= 0 and not source_close_attempted:
            try:
                os.close(source_descriptor)
            except (
                AttributeError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                KeyboardInterrupt,
                SystemExit,
            ):
                pass
        raise


def _backup_target(artifact: _Artifact) -> None:
    """Create a backup whose ownership is stored before this helper returns."""

    target = artifact.target
    expected = artifact.original_target
    if expected is None:
        return

    if expected.file_type not in {stat.S_IFREG, stat.S_IFLNK}:
        raise OSError("Publication targets must be regular files or symbolic links.")

    for _attempt in range(100):
        _assert_pristine(target, expected)
        candidate = _candidate(target, "backup")
        if expected.file_type == stat.S_IFLNK:
            link_value = os.readlink(target)
            try:
                try:
                    os.symlink(link_value, candidate)
                except FileExistsError:
                    continue
                symlink_identity = _identity(candidate)
                _assert_pristine(target, expected)
                if os.readlink(candidate) != link_value:
                    raise OSError("A publication backup changed while it was created.")
                artifact.backup = _OwnedPath(candidate, symlink_identity)
            except BaseException:
                try:
                    current = _identity(candidate)
                    if current.file_type == stat.S_IFLNK and os.readlink(candidate) == link_value:
                        _unlink_if_owned(candidate, current)
                except BaseException:  # noqa: BLE001, S110 - preserve the creation failure
                    pass
                raise
            return
        try:
            _copy_regular_no_clobber(
                _OwnedPath(target, expected),
                candidate,
                owner=lambda backup: setattr(artifact, "backup", backup),
            )
            return
        except FileExistsError:
            continue
    raise OSError("A publication backup name could not be reserved safely.")


def _cleanup_displacement_lifecycle(lifecycle: _DisplacementLifecycle) -> None:
    """Remove an untransferred reservation retained by its caller lifecycle."""

    if lifecycle.transferred:
        return
    descriptor = lifecycle.descriptor
    path = lifecycle.path
    reservation = lifecycle.reservation
    lifecycle.descriptor = None
    if descriptor is not None:
        if path is not None:
            try:
                cleanup_open_path(path, descriptor)
            except BaseException:  # noqa: BLE001, S110 - cleanup must continue
                pass
        try:
            os.close(descriptor)
        except BaseException:  # noqa: BLE001, S110 - cleanup must continue
            pass
    if reservation is not None:
        try:
            _unlink_if_owned(reservation.path, reservation.identity)
        except BaseException:  # noqa: BLE001, S110 - cleanup must continue
            pass
    elif path is not None:
        try:
            actual = _identity(path)
            if actual.file_type == stat.S_IFREG and actual.size == 0:
                _unlink_if_owned(path, actual)
        except BaseException:  # noqa: BLE001, S110 - cleanup must continue
            pass
    lifecycle.path = None
    lifecycle.reservation = None


def _reserve_displacement(
    target: Path,
    lifecycle: _DisplacementLifecycle,
) -> None:
    """Reserve an empty path after registering its name with the caller."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    for _attempt in range(100):
        path = _candidate(target, "displaced")
        lifecycle.path = path
        lifecycle.descriptor = None
        lifecycle.reservation = None
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            lifecycle.path = None
            continue
        lifecycle.descriptor = descriptor
        try:
            identity = _PathIdentity.from_stat(os.fstat(descriptor))
            lifecycle.reservation = _OwnedPath(path, identity)
            os.close(descriptor)
            lifecycle.descriptor = None
            return
        except BaseException:
            _cleanup_displacement_lifecycle(lifecycle)
            raise
    raise OSError("A displacement reservation could not be created safely.")


def _linux_rename_no_replace_at(
    source_directory: int,
    source_name: str,
    target_directory: int,
    target_name: str,
) -> None:
    """Linux no-replace rename with both namespace sides descriptor-bound."""

    ctypes = importlib.import_module("ctypes")
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise OSError(
            errno.ENOTSUP,
            "Descriptor-bound publication is unavailable on this Linux system.",
        ) from exc
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if (
        renameat2(
            source_directory,
            os.fsencode(source_name),
            target_directory,
            os.fsencode(target_name),
            1,
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target_name)


def _macos_rename_no_replace_at(
    source_directory: int,
    source_name: str,
    target_directory: int,
    target_name: str,
) -> None:
    """macOS exclusive rename with both namespace sides descriptor-bound."""

    ctypes = importlib.import_module("ctypes")
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameatx_np = libc.renameatx_np
    except AttributeError as exc:
        raise OSError(
            errno.ENOTSUP,
            "Descriptor-bound publication is unavailable on this macOS system.",
        ) from exc
    renameatx_np.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameatx_np.restype = ctypes.c_int
    if (
        renameatx_np(
            source_directory,
            os.fsencode(source_name),
            target_directory,
            os.fsencode(target_name),
            0x00000004,
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target_name)


def _windows_open_shared_descriptor(path: Path) -> int:
    """Open a Windows candidate while permitting its verified atomic move."""

    ctypes = importlib.import_module("ctypes")
    wintypes = importlib.import_module("ctypes.wintypes")
    msvcrt = importlib.import_module("msvcrt")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
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
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    generic_read = 0x80000000
    delete_access = 0x00010000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        os.fspath(path),
        generic_read | delete_access,
        share_read_write_delete,
        None,
        open_existing,
        open_reparse_point,
        None,
    )
    handle_value = ctypes.cast(handle, ctypes.c_void_p).value
    if handle_value in {None, invalid_handle}:
        error = ctypes.get_last_error()
        raise ctypes.WinError(error)
    return _windows_transfer_handle_to_descriptor(
        handle,
        handle_value,
        close_handle,
        msvcrt,
    )


def _windows_transfer_handle_to_descriptor(
    handle: Any,
    handle_value: int,
    close_handle: Callable[[Any], Any],
    msvcrt: Any,
) -> int:
    """Transfer one native handle to the CRT without double-closing ownership."""

    # Python cannot mask the instruction boundary between this C call returning
    # and the local assignment. Ordinary call failure retains native ownership;
    # every later failure closes the transferred CRT descriptor exactly once.
    try:
        transferred_descriptor = msvcrt.open_osfhandle(
            handle_value,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0),
        )
    except BaseException:
        close_handle(handle)
        raise
    try:
        return int(transferred_descriptor)
    except BaseException:
        try:
            os.close(int(transferred_descriptor))
        except (OSError, TypeError, ValueError):
            pass
        raise


def _windows_open_directory_descriptor(path: Path) -> int:
    """Open a Windows directory capability suitable for relative rename."""

    ctypes = importlib.import_module("ctypes")
    wintypes = importlib.import_module("ctypes.wintypes")
    msvcrt = importlib.import_module("msvcrt")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
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
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    generic_read = 0x80000000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        os.fspath(path),
        generic_read,
        share_read_write_delete,
        None,
        open_existing,
        backup_semantics | open_reparse_point,
        None,
    )
    handle_value = ctypes.cast(handle, ctypes.c_void_p).value
    if handle_value in {None, invalid_handle}:
        error = ctypes.get_last_error()
        raise ctypes.WinError(error)

    return _windows_transfer_handle_to_descriptor(
        handle,
        handle_value,
        close_handle,
        msvcrt,
    )


def _open_directory_capability(
    path: Path,
    prepared: _PreparedRegularInstall,
) -> None:
    """Open and identity-bind one destination parent directory."""

    if _PLATFORM == "win32":
        descriptor = _windows_open_directory_descriptor(path)
    else:
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
    try:
        opened = _PathIdentity.from_stat(os.fstat(descriptor))
        at_path = _identity(path)
        if (
            opened.file_type != stat.S_IFDIR
            or not _descriptor_matches_path(opened, at_path)
            or opened.inode <= 0
        ):
            raise OSError("A publication destination directory changed while opening.")
        prepared.destination = _DirectoryCapability(path, descriptor, opened)
        return
    except BaseException:
        prepared.destination = None
        try:
            os.close(descriptor)
        except BaseException:  # noqa: BLE001, S110 - capability creation failed
            pass
        raise


def _directory_capability_matches_path(capability: _DirectoryCapability) -> bool:
    try:
        held = _PathIdentity.from_stat(os.fstat(capability.descriptor))
        at_path = _identity(capability.path)
    except (FileNotFoundError, OSError):
        return False
    return capability.identity.same_file_id(held) and _descriptor_matches_path(held, at_path)


def _close_directory_capability(capability: _DirectoryCapability) -> None:
    try:
        os.close(capability.descriptor)
    except BaseException:  # noqa: BLE001, S110 - cleanup must not alter publication state
        pass


def _fsync_directory_capability(capability: _DirectoryCapability) -> None:
    """Flush a held POSIX namespace capability when the platform supports it.

    Windows guarantees that the candidate's file data was flushed before its
    rename, but ``FlushFileBuffers`` is not supported for directory handles.
    The Win32 API exposed to Python therefore cannot provide the equivalent
    namespace-directory durability barrier used on POSIX.
    """

    if _PLATFORM == "win32":
        return
    os.fsync(capability.descriptor)


def _windows_rename_descriptor_no_replace(
    source_descriptor: int,
    destination: _DirectoryCapability,
    target_name: str,
) -> None:
    """Rename the exact held source handle under a held destination directory."""

    ctypes = importlib.import_module("ctypes")
    wintypes = importlib.import_module("ctypes.wintypes")
    msvcrt = importlib.import_module("msvcrt")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_file_information.restype = wintypes.BOOL

    class _FileRenameInfo(ctypes.Structure):  # type: ignore[misc,name-defined]
        _fields_ = (
            ("replace_if_exists", wintypes.BYTE),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * (len(target_name) + 1)),
        )

    information = _FileRenameInfo()
    information.replace_if_exists = 0
    information.root_directory = msvcrt.get_osfhandle(destination.descriptor)
    information.file_name_length = len(target_name.encode("utf-16-le"))
    information.file_name = target_name
    file_rename_info = 3
    if not set_file_information(
        msvcrt.get_osfhandle(source_descriptor),
        file_rename_info,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        raise ctypes.WinError(error)


def _windows_delete_descriptor(descriptor: int) -> bool:
    """Mark the exact held Windows object for deletion."""

    ctypes = importlib.import_module("ctypes")
    wintypes = importlib.import_module("ctypes.wintypes")
    msvcrt = importlib.import_module("msvcrt")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_file_information.restype = wintypes.BOOL
    delete_file = ctypes.c_ubyte(1)
    return bool(
        set_file_information(
            msvcrt.get_osfhandle(descriptor),
            4,
            ctypes.byref(delete_file),
            ctypes.sizeof(delete_file),
        )
    )


def _open_verified_install_candidate(
    candidate: _OwnedPath,
    prepared: _PreparedRegularInstall,
) -> None:
    """Open and reverify the sealed private candidate before its namespace commit."""

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_NOATIME"):
        # This candidate was created privately by this process, so Linux can
        # suppress access-time changes during the two digest verifications.
        flags |= os.O_NOATIME
    if _PLATFORM == "win32":
        descriptor = _windows_open_shared_descriptor(candidate.path)
    else:
        descriptor = os.open(candidate.path, flags)
    try:
        opened = _PathIdentity.from_stat(os.fstat(descriptor))
        at_path = _identity(candidate.path)
        if (
            opened.file_type != stat.S_IFREG
            or not candidate.identity.pristine(opened)
            or not opened.pristine(at_path)
        ):
            raise OSError("A private publication candidate changed before commit.")
        if candidate.digest is None or _descriptor_digest(descriptor) != candidate.digest:
            raise OSError("A private publication candidate digest changed before commit.")
        prepared.descriptor = descriptor
        return
    except BaseException:
        prepared.descriptor = None
        try:
            os.close(descriptor)
        except BaseException:  # noqa: BLE001, S110 - a failed close must not mask the cause
            pass
        raise


def _identity_in_directory(
    capability: _DirectoryCapability,
    name: str,
) -> _PathIdentity | None:
    """Read one entry through its held parent directory where supported."""

    try:
        if _PLATFORM == "win32":
            if not _directory_capability_matches_path(capability):
                return None
            return _identity(capability.path / name)
        return _PathIdentity.from_stat(
            os.stat(
                name,
                dir_fd=capability.descriptor,
                follow_symlinks=False,
            )
        )
    except FileNotFoundError:
        return None


def _commit_prepared_namespace(
    prepared: _PreparedRegularInstall,
    target: Path,
) -> None:
    """Commit using held capabilities for both namespace sides."""

    destination = prepared.destination
    quarantine = prepared.quarantine
    if destination is None or quarantine is None or not prepared.complete:
        raise OSError("A private publication candidate was not fully prepared.")
    if not _directory_capability_matches_path(destination):
        raise OSError("A publication destination directory changed before commit.")
    if _PLATFORM == "win32":
        assert prepared.descriptor is not None
        _windows_rename_descriptor_no_replace(
            prepared.descriptor,
            destination,
            target.name,
        )
        return
    source_directory = quarantine.descriptor
    if source_directory is None:
        raise OSError("A descriptor-bound publication source is unavailable.")
    source_identity = _PathIdentity.from_stat(os.fstat(source_directory))
    if not quarantine.identity.same_file_id(source_identity):
        raise OSError("A private publication directory changed before commit.")
    if _PLATFORM == "darwin":
        _macos_rename_no_replace_at(
            source_directory,
            "owned",
            destination.descriptor,
            target.name,
        )
    elif _PLATFORM.startswith("linux"):
        _linux_rename_no_replace_at(
            source_directory,
            "owned",
            destination.descriptor,
            target.name,
        )
    else:
        raise OSError(
            errno.ENOTSUP,
            "Descriptor-bound publication is unsupported on this platform.",
        )


def _cleanup_prepared_candidate(
    prepared: _PreparedRegularInstall,
    target: Path,
) -> None:
    """Remove only the held candidate, wherever an interrupted commit left it."""

    descriptor = prepared.descriptor
    candidate = prepared.candidate
    quarantine = prepared.quarantine
    if descriptor is None:
        if candidate is None:
            return
        try:
            if quarantine is not None:
                _discard_quarantined_path(
                    quarantine,
                    candidate.identity,
                    None,
                )
            else:
                _unlink_if_owned(candidate.path, candidate.identity)
        except BaseException:  # noqa: BLE001, S110 - preserve the triggering failure
            pass
        return
    if _PLATFORM == "win32":
        try:
            _windows_delete_descriptor(descriptor)
        except BaseException:  # noqa: BLE001, S110 - preserve the triggering failure
            pass
        return
    try:
        held = _PathIdentity.from_stat(os.fstat(descriptor))
    except BaseException:  # noqa: BLE001 - preserve the triggering failure
        return
    locations: list[tuple[int, str, _DirectoryCapability | None]] = []
    if prepared.destination is not None:
        locations.append(
            (
                prepared.destination.descriptor,
                target.name,
                prepared.destination,
            )
        )
    if quarantine is not None and quarantine.descriptor is not None:
        locations.append((quarantine.descriptor, "owned", None))
    for directory_descriptor, name, capability in locations:
        try:
            actual = _PathIdentity.from_stat(
                os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            )
            if not _descriptor_matches_path(held, actual):
                continue
            os.unlink(name, dir_fd=directory_descriptor)
            if capability is not None:
                _fsync_directory_capability(capability)
            return
        except (FileNotFoundError, OSError):
            continue


def _verify_committed_install(
    prepared: _PreparedRegularInstall,
    target: Path,
) -> _OwnedPath:
    """Verify that the namespace commit moved exactly the held sealed candidate."""

    descriptor = prepared.descriptor
    candidate = prepared.candidate
    destination = prepared.destination
    if descriptor is None:
        raise OSError("A committed publication descriptor was closed too early.")
    if candidate is None or destination is None:
        raise OSError("A committed publication lifecycle was incomplete.")
    held = _PathIdentity.from_stat(os.fstat(descriptor))
    installed = _identity_in_directory(destination, target.name)
    if installed is None:
        raise OSError("A publication destination directory changed during commit.")
    if not _descriptor_survived_move(
        candidate.identity,
        held,
    ) or not _descriptor_matches_path(held, installed):
        raise OSError("A private publication candidate was replaced during commit.")
    digest = _descriptor_digest(descriptor)
    if candidate.digest is None or digest != candidate.digest:
        raise OSError("A committed publication digest changed during commit.")
    if not _directory_capability_matches_path(destination):
        raise OSError("A publication destination directory changed during commit.")
    return _OwnedPath(target, installed, digest)


def _close_prepared_descriptor(prepared: _PreparedRegularInstall) -> None:
    descriptor = prepared.descriptor
    prepared.descriptor = None
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except BaseException:  # noqa: BLE001, S110 - cleanup must continue
        pass


def _close_prepared_destination(prepared: _PreparedRegularInstall) -> None:
    destination = prepared.destination
    prepared.destination = None
    if destination is not None:
        _close_directory_capability(destination)


def _close_private_quarantine_quietly(quarantine: _PrivateQuarantine) -> None:
    try:
        _close_quarantine(quarantine)
    except BaseException:  # noqa: BLE001, S110 - cleanup must not change the outcome
        pass


def _close_prepared_quarantine(prepared: _PreparedRegularInstall) -> None:
    """Close and remove every quarantine state registered in the lifecycle."""

    quarantine = prepared.quarantine
    prepared.quarantine = None
    if quarantine is not None:
        _close_private_quarantine_quietly(quarantine)
        prepared.quarantine_descriptor = None
        prepared.quarantine_directory = None
        prepared.quarantine_identity = None
        return

    descriptor = prepared.quarantine_descriptor
    prepared.quarantine_descriptor = None
    if descriptor is not None:
        try:
            os.close(descriptor)
        except BaseException:  # noqa: BLE001, S110 - cleanup must continue
            pass
    directory = prepared.quarantine_directory
    identity = prepared.quarantine_identity
    if directory is not None:
        try:
            removal_identity = identity or _identity(directory)
            _remove_directory_if_owned(directory, removal_identity)
        except BaseException:  # noqa: BLE001, S110 - cleanup must continue
            pass
    prepared.quarantine_directory = None
    prepared.quarantine_identity = None


def _release_prepared_install(prepared: _PreparedRegularInstall) -> None:
    """Release held descriptors after the candidate was committed or removed."""

    _close_prepared_descriptor(prepared)
    _close_prepared_quarantine(prepared)
    _close_prepared_destination(prepared)
    prepared.active = False


def _prepare_regular_install(
    source: _OwnedPath,
    prepared: _PreparedRegularInstall,
) -> None:
    """Build and seal a candidate inside a caller-owned mutable lifecycle."""

    if source.identity.file_type != stat.S_IFREG:
        raise OSError("Only regular files can use private prepared installation.")
    target = prepared.target
    _assert_pristine(source.path, source.identity)
    try:
        _open_directory_capability(target.parent, prepared)
        destination = prepared.destination
        if destination is None:
            raise OSError("A publication destination capability was not retained.")
        _create_private_quarantine(target, prepared)
        quarantine = prepared.quarantine
        if quarantine is None:
            raise OSError("A private publication directory was not retained.")
        if not _directory_capability_matches_path(destination):
            raise OSError("A publication destination directory changed during preparation.")
        _copy_regular_no_clobber(
            source,
            quarantine.path,
            owner=lambda candidate: setattr(prepared, "candidate", candidate),
        )
        candidate = prepared.candidate
        if candidate is None:
            raise OSError("A private publication candidate was not retained.")
        if source.identity.same_file_id(candidate.identity):
            raise OSError("A private publication candidate aliased its source.")
        _open_verified_install_candidate(candidate, prepared)
        descriptor = prepared.descriptor
        if descriptor is None:
            raise OSError("A private publication descriptor was not retained.")
        if quarantine.descriptor is not None:
            actual = _PathIdentity.from_stat(
                os.stat(
                    "owned",
                    dir_fd=quarantine.descriptor,
                    follow_symlinks=False,
                )
            )
            held = _PathIdentity.from_stat(os.fstat(descriptor))
            if not _descriptor_matches_path(held, actual):
                raise OSError("A private publication candidate escaped its held directory.")
        prepared.complete = True
        return
    except BaseException:
        _abandon_prepared_install(prepared, target)
        raise


def _abandon_prepared_install(prepared: _PreparedRegularInstall, target: Path) -> None:
    """Discard an uncommitted private candidate without masking its caller."""

    if not prepared.active:
        return
    _cleanup_prepared_candidate(prepared, target)
    _release_prepared_install(prepared)


def _commit_prepared_install(
    prepared: _PreparedRegularInstall,
    target: Path,
    *,
    artifact: _Artifact | None = None,
    restoration: _Artifact | None = None,
) -> _OwnedPath:
    """Commit and verify one fully sealed private regular-file candidate."""

    destination = prepared.destination
    if destination is None or not prepared.complete:
        raise OSError("A private publication candidate was not fully prepared.")
    try:
        _commit_prepared_namespace(prepared, target)
        _fsync_directory_capability(destination)
        installed = _verify_committed_install(prepared, target)
        if artifact is not None:
            artifact.published = installed
        if restoration is not None:
            restoration.restored = installed
    except BaseException as exc:
        recorded = artifact.published if artifact is not None else None
        if recorded is None and restoration is not None:
            recorded = restoration.restored
        if recorded is None:
            _cleanup_prepared_candidate(prepared, target)
        _release_prepared_install(prepared)
        if recorded is not None:
            return recorded
        if isinstance(exc, FileExistsError) or (
            isinstance(exc, OSError)
            and (
                exc.errno in {errno.EEXIST, errno.ENOTEMPTY}
                or getattr(exc, "winerror", None) in {80, 183}
            )
        ):
            raise OSError("A publication target appeared during the operation.") from exc
        raise

    _release_prepared_install(prepared)
    return installed


def _install_no_clobber(
    source: _OwnedPath,
    target: Path,
    *,
    restoration: _Artifact | None = None,
) -> _OwnedPath:
    """Install ``source`` only if no object currently occupies ``target``."""

    _assert_pristine(source.path, source.identity)
    try:
        if source.identity.file_type == stat.S_IFLNK:
            link_value = os.readlink(source.path)
            try:
                os.symlink(link_value, target)
            except FileExistsError as exc:
                raise OSError("A publication target appeared during the operation.") from exc
            installed = _OwnedPath(target, _identity(target))
            try:
                if installed.identity.file_type != stat.S_IFLNK:
                    raise OSError("The restored symbolic-link target changed during publication.")
                _assert_pristine(source.path, source.identity)
                if os.readlink(target) != link_value:
                    raise OSError("The restored symbolic-link target changed during publication.")
            except BaseException:
                _unlink_if_owned(installed.path, installed.identity)
                raise
            if restoration is not None:
                restoration.restored = installed
            return installed
        if source.identity.file_type != stat.S_IFREG:
            raise OSError("Only regular files and symbolic links can be published.")
        prepared = _PreparedRegularInstall(target)
        try:
            _prepare_regular_install(source, prepared)
            return _commit_prepared_install(
                prepared,
                target,
                restoration=restoration,
            )
        except BaseException:
            _abandon_prepared_install(prepared, target)
            raise
    except FileExistsError as exc:
        raise OSError("A publication target appeared during the operation.") from exc


def _displace_original(
    artifact: _Artifact,
    replace: Callable[[str | Path, str | Path], None],
    destination: _DirectoryCapability | None = None,
) -> None:
    expected = artifact.original_target
    if expected is None:
        if _identity_or_none(artifact.target) is not None:
            raise OSError("A publication target appeared after preflight.")
        return

    artifact.displacement_attempted = True
    _assert_pristine(artifact.target, expected)
    lifecycle = _DisplacementLifecycle()
    try:
        _reserve_displacement(artifact.target, lifecycle)
        reservation = lifecycle.reservation
        if reservation is None:
            raise OSError("A displacement reservation was not retained.")
        replace(artifact.target, reservation.path)
        moved_identity = _identity(reservation.path)
        if not expected.unchanged(moved_identity):
            moved = _OwnedPath(reservation.path, moved_identity)
            if _identity_or_none(artifact.target) is None:
                try:
                    restored = _install_no_clobber(moved, artifact.target)
                except OSError:
                    pass
                else:
                    _unlink_if_owned(moved.path, moved.identity)
                    _assert_same_object(artifact.target, restored.identity)
            raise OSError("A publication target changed while it was displaced.")
        artifact.displaced = _OwnedPath(reservation.path, moved_identity)
        lifecycle.transferred = True
        if destination is not None:
            _fsync_directory_capability(destination)
    except BaseException:
        reservation = lifecycle.reservation
        if artifact.displaced is None and reservation is not None:
            moved_after_error = _identity_or_none(reservation.path)
            if moved_after_error is not None and expected.unchanged(moved_after_error):
                artifact.displaced = _OwnedPath(reservation.path, moved_after_error)
                lifecycle.transferred = True
            else:
                try:
                    _unlink_if_owned(reservation.path, reservation.identity)
                except BaseException:  # noqa: BLE001, S110 - preserve displacement failure
                    pass
        elif artifact.displaced is not None:
            lifecycle.transferred = True
        _cleanup_displacement_lifecycle(lifecycle)
        raise


def _publish_artifact(
    artifact: _Artifact,
    replace: Callable[[str | Path, str | Path], None],
) -> None:
    _assert_pristine(artifact.source.path, artifact.source.identity)
    if artifact.source.identity.file_type != stat.S_IFREG:
        raise OSError("Publication staging files must remain regular files.")
    prepared = _PreparedRegularInstall(artifact.target)
    try:
        _prepare_regular_install(artifact.source, prepared)
        destination = prepared.destination
        if destination is None:
            raise OSError("A publication destination capability was not retained.")
        _displace_original(artifact, replace, destination)
        _assert_pristine(artifact.source.path, artifact.source.identity)
        _commit_prepared_install(
            prepared,
            artifact.target,
            artifact=artifact,
        )
    except BaseException:
        _abandon_prepared_install(prepared, artifact.target)
        raise
    assert artifact.published is not None

    # Preserve the injectable failure boundary used by callers without relying
    # on a same-path rename in production. Installation already committed with
    # the platform's atomic no-replace primitive.
    if replace is not _NATIVE_REPLACE:
        replace(artifact.target, artifact.target)
    _assert_pristine(artifact.target, artifact.published.identity)


def _restore_original(artifact: _Artifact) -> OSError | None:
    if artifact.original_target is None:
        return None
    if not artifact.displacement_attempted:
        return None
    current_target = _identity_or_none(artifact.target)
    if current_target is not None:
        if artifact.restored is not None and artifact.restored.identity.pristine(current_target):
            artifact.original_target = artifact.restored.identity
            return None
        if artifact.original_target.pristine(current_target):
            return None
        return OSError(
            "A concurrent replacement was preserved; the prior target remains in recovery storage."
        )
    # The sealed backup retains metadata captured before its source was read.
    # Prefer it because reading the original can advance the displaced inode's
    # access time on Linux; the displaced copy remains a verified fallback.
    candidates = tuple(item for item in (artifact.backup, artifact.displaced) if item is not None)
    for candidate in candidates:
        try:
            _assert_pristine(candidate.path, candidate.identity)
            _install_no_clobber(
                candidate,
                artifact.target,
                restoration=artifact,
            )
        except OSError:
            continue
        restored = artifact.restored
        if restored is None:
            raise OSError("A restored publication target was not retained.")
        removed = _unlink_if_owned(candidate.path, candidate.identity)
        if candidate is artifact.displaced:
            if removed:
                artifact.displaced = None
        else:
            if removed:
                artifact.backup = None
        artifact.original_target = restored.identity
        return None
    return OSError("The prior publication target could not be restored safely.")


def _cleanup_owned(item: _OwnedPath | None) -> None:
    if item is not None:
        _unlink_if_owned(item.path, item.identity)


def _cleanup_artifact_source(artifact: _Artifact) -> None:
    _unlink_if_owned(artifact.source.path, artifact.source.identity)
    key = _path_key(artifact.source.path)
    with _STAGING_LOCK:
        reservation = _STAGING_IDENTITIES.get(key)
        if reservation is not None and reservation.identity.pristine(artifact.source.identity):
            _STAGING_IDENTITIES.pop(key, None)


def _cleanup_after_commit(action: Callable[[], None]) -> None:
    """Retry and suppress cleanup interruptions after a validated commit."""

    for _attempt in range(2):
        try:
            action()
            return
        except BaseException:  # noqa: BLE001, S112 - retry late cleanup once
            continue


def _cleanup_committed_bundle(artifacts: list[_Artifact]) -> None:
    """Keep the validated commit authoritative across whole-loop interruptions."""

    for _attempt in range(2):
        try:
            for artifact in artifacts:
                _cleanup_after_commit(partial(_cleanup_owned, artifact.backup))
                _cleanup_after_commit(partial(_cleanup_owned, artifact.displaced))
                _cleanup_after_commit(partial(_cleanup_artifact_source, artifact))
            return
        except BaseException:  # noqa: BLE001, S112 - restart the idempotent sweep
            continue


def _rollback_bundle(artifacts: list[_Artifact]) -> OSError | None:
    """Restart the complete idempotent rollback sweep after interruptions.

    Cleanup-time ``KeyboardInterrupt`` and ``SystemExit`` must neither mask the
    publication error nor strand later artifacts.  Each pass attempts every
    action, then restarts from removal if any secondary ``BaseException`` was
    observed.  Identity-bound operations make a repeated pass safe.
    """

    for _attempt in range(3):
        interrupted = False
        rollback_error: OSError | None = None

        for artifact in reversed(artifacts):
            if artifact.published is None:
                continue
            try:
                _unlink_if_owned(artifact.target, artifact.published.identity)
            except BaseException:  # noqa: BLE001 - retry the whole rollback sweep
                interrupted = True

        for artifact in reversed(artifacts):
            try:
                restore_error = _restore_original(artifact)
            except BaseException:  # noqa: BLE001 - retry the whole rollback sweep
                interrupted = True
            else:
                rollback_error = rollback_error or restore_error

        for artifact in artifacts:
            try:
                _cleanup_artifact_source(artifact)
            except BaseException:  # noqa: BLE001 - retry the whole rollback sweep
                interrupted = True

        if rollback_error is None and not interrupted:
            for artifact in artifacts:
                for retained in (artifact.backup, artifact.displaced):
                    try:
                        _cleanup_owned(retained)
                    except BaseException:  # noqa: BLE001 - retry the whole rollback sweep
                        interrupted = True

        if not interrupted:
            return rollback_error

    # Persistent cleanup interruption cannot safely replace the triggering
    # publication exception. Identity-bound recovery files remain available.
    return None


def publish_staged_bundle(
    artifacts: Iterable[tuple[Path, Path]],
    *,
    replace: Callable[[str | Path, str | Path], None],
    validate_after: Callable[[], None] | None = None,
) -> None:
    """Publish staged files as one rollback-capable logical transaction.

    Filesystems cannot atomically rename multiple files. Existing targets are
    therefore retained by identity until every no-clobber installation and the
    optional post-publication validation succeeds. Replacing one existing
    target requires a brief displacement-then-commit window; this is a
    rollback-capable logical transaction, not continuous old-or-new namespace
    visibility across a crash or concurrent reader. Candidate file data is
    flushed on every platform. POSIX additionally flushes the destination
    directory; Windows does not expose a supported equivalent for directory
    handles, so namespace crash durability cannot be strengthened there.
    """

    cancellation_checkpoint()
    selected = [(Path(source), Path(target)) for source, target in artifacts]
    for index, (_source, target) in enumerate(selected):
        for _other_source, other_target in selected[index + 1 :]:
            if paths_alias(target, other_target):
                raise OSError("Publication bundle targets must not alias each other.")

    with non_interruptible_section("publishing output bundle"):
        prepared = [
            _Artifact(
                source=_claim_for_publication(source),
                target=target,
                original_target=_identity_or_none(target),
            )
            for source, target in selected
        ]
        for artifact in prepared:
            if artifact.original_target is not None and artifact.original_target.file_type not in {
                stat.S_IFREG,
                stat.S_IFLNK,
            }:
                raise OSError("Publication targets must be regular files or symbolic links.")

        try:
            for artifact in prepared:
                if artifact.original_target is not None:
                    _backup_target(artifact)
            for artifact in prepared:
                _publish_artifact(artifact, replace)
            if validate_after is not None:
                validate_after()
            for artifact in prepared:
                assert artifact.published is not None
                _assert_pristine(artifact.target, artifact.published.identity)
        except BaseException as publish_error:
            rollback_error = _rollback_bundle(prepared)
            if rollback_error is not None:
                raise rollback_error from publish_error
            raise
        else:
            try:
                _cleanup_committed_bundle(prepared)
            except BaseException:  # noqa: BLE001, S110 - validated commit is authoritative
                pass


__all__ = [
    "StagedFileToken",
    "claim_staged_path",
    "cleanup_open_path",
    "cleanup_staged_path",
    "cleanup_staged_token",
    "is_staging_path",
    "paths_alias",
    "publish_staged_bundle",
    "seal_staged_path",
    "staged_file_token",
    "staging_path",
    "write_staged_bytes",
    "write_staged_text",
]
