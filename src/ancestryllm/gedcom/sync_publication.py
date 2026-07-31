"""Capability-safe atomic publication, rollback, and recovery for GEDCOM sync."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ancestryllm.core.cancellation import (
    cancellation_checkpoint,
)
from ancestryllm.gedcom.sync_contracts import (
    SyncError,
)


@dataclass(frozen=True, slots=True)
class _DirectoryIdentity:
    """Stable identity for a directory created by this invocation."""

    device: int
    inode: int
    changed_ns: int
    birth_ns: int | None

    @classmethod
    def from_stat(cls, value: os.stat_result) -> _DirectoryIdentity:
        if value.st_ino <= 0:
            raise ValueError("filesystem identity is unavailable")
        birth_ns = getattr(value, "st_birthtime_ns", None)
        if birth_ns is None:
            birth = getattr(value, "st_birthtime", None)
            birth_ns = int(birth * 1_000_000_000) if birth is not None else None
        return cls(value.st_dev, value.st_ino, value.st_ctime_ns, birth_ns)

    def same_object(self, other: _DirectoryIdentity) -> bool:
        """Compare stable birth identity while allowing directory ctime changes."""

        return (
            self.device == other.device
            and self.inode == other.inode
            and (self.birth_ns is None or other.birth_ns is None or self.birth_ns == other.birth_ns)
        )


@dataclass(slots=True)
class _DirectoryCapability:
    """Held proof that an operation still addresses the directory it claimed."""

    selected_path: Path
    descriptor: int | None
    marker_name: str
    marker_descriptor: int
    owned: bool


@dataclass(slots=True)
class _PublicationTransactionState:
    """Mutable ownership state spanning publish, finalization, and caller cleanup."""

    marker_descriptor: int | None
    committed: bool = False


def _write_bytes(path: Path, payload: bytes) -> None:
    """Write a new artifact inside an unpublished staging directory."""
    cancellation_checkpoint()
    with path.open("xb") as handle:
        handle.write(payload)
    cancellation_checkpoint()


def _exclusive_rename_directory(
    source: Path,
    destination: Path,
    *,
    source_dir_fd: int | None = None,
    destination_dir_fd: int | None = None,
) -> None:
    """Atomically rename one directory without replacing any destination."""

    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if os.name == "nt":
        # MoveFileEx without MOVEFILE_REPLACE_EXISTING is what os.rename uses
        # on Windows.
        os.rename(source, destination)
        return
    library = ctypes.CDLL(None, use_errno=True)
    current_platform = str(sys.platform)
    if current_platform == "darwin":
        try:
            rename_exclusive = (
                library.renameatx_np
                if source_dir_fd is not None and destination_dir_fd is not None
                else library.renamex_np
            )
        except AttributeError as exc:
            raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable") from exc
        if source_dir_fd is not None and destination_dir_fd is not None:
            rename_exclusive.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            arguments: tuple[Any, ...] = (
                source_dir_fd,
                source_bytes,
                destination_dir_fd,
                destination_bytes,
                0x00000004,
            )
        else:
            rename_exclusive.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            arguments = (source_bytes, destination_bytes, 0x00000004)
        rename_exclusive.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = rename_exclusive(*arguments)
    elif current_platform.startswith("linux"):
        try:
            rename_no_replace = library.renameat2
        except AttributeError as exc:
            raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable") from exc
        rename_no_replace.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_no_replace.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = rename_no_replace(
            source_dir_fd if source_dir_fd is not None else -100,
            source_bytes,
            destination_dir_fd if destination_dir_fd is not None else -100,
            destination_bytes,
            0x00000001,
        )
    else:
        raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable")
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number))


def _directory_identity(path: Path) -> _DirectoryIdentity:
    """Return identity for an existing real directory or fail closed."""

    try:
        value = os.lstat(path)
    except OSError as exc:
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be inspected safely.",
            "Release ownership must be proven before publication or cleanup.",
            ["Choose an accessible local --release-root and retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    if not stat.S_ISDIR(value.st_mode):
        raise SyncError(
            "SYNC_OUTPUT",
            "The release destination is not a real directory.",
            "Symlinks and non-directory destinations cannot preserve immutable releases safely.",
            ["Choose a new local --release-root that is an ordinary directory."],
        )
    try:
        return _DirectoryIdentity.from_stat(value)
    except ValueError as exc:
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory has no trustworthy filesystem identity.",
            "Publication and recursive cleanup require a positive, stable file identity.",
            ["Choose a supported local filesystem and retry."],
        ) from exc


def _raw_directory_identity(path: Path) -> _DirectoryIdentity:
    """Return an ordinary directory identity without translating failures."""

    value = os.lstat(path)
    if not stat.S_ISDIR(value.st_mode):
        raise ValueError("path is not an ordinary directory")
    return _DirectoryIdentity.from_stat(value)


def _held_file_path(descriptor: int) -> Path:
    """Return the current physical pathname of one held file descriptor."""

    current_platform = str(sys.platform)
    if current_platform == "darwin":
        import fcntl

        try:
            raw = fcntl.fcntl(descriptor, 50, b"\0" * 1024)
        except (OSError, ValueError) as exc:
            raise SyncError(
                "SYNC_OUTPUT",
                "A held release capability no longer has a usable path.",
                "The release root moved while the operation was active.",
                ["Stop concurrent filesystem changes and retry."],
            ) from exc
        return Path(os.fsdecode(raw.split(b"\0", 1)[0]))
    if current_platform.startswith("linux"):
        try:
            return Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        except OSError as exc:
            raise SyncError(
                "SYNC_OUTPUT",
                "A held release capability no longer has a usable path.",
                "The release root moved while the operation was active.",
                ["Stop concurrent filesystem changes and retry."],
            ) from exc
    if os.name == "nt":
        import msvcrt

        handle = msvcrt.get_osfhandle(descriptor)  # type: ignore[attr-defined]
        library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        get_path = library.GetFinalPathNameByHandleW
        get_path.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_uint,
        )
        get_path.restype = ctypes.c_uint
        size = get_path(handle, None, 0, 0)
        if size == 0:
            raise SyncError(
                "SYNC_OUTPUT",
                "A held release capability no longer has a usable path.",
                "The release root moved while the operation was active.",
                ["Stop concurrent filesystem changes and retry."],
            )
        buffer = ctypes.create_unicode_buffer(size + 1)
        if get_path(handle, buffer, len(buffer), 0) == 0:
            raise SyncError(
                "SYNC_OUTPUT",
                "A held release capability no longer has a usable path.",
                "The release root moved while the operation was active.",
                ["Stop concurrent filesystem changes and retry."],
            )
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)
    raise SyncError(
        "SYNC_OUTPUT",
        "The platform cannot hold a release-directory capability.",
        "Safe publication requires a supported directory-handle implementation.",
        ["Use Ubuntu, macOS, or Windows and retry."],
    )


def _capability_current_path(capability: _DirectoryCapability) -> Path:
    """Return the directory containing the held, unguessable marker."""

    return _held_file_path(capability.marker_descriptor).parent


def _uses_windows_capability_handles() -> bool:
    return os.name == "nt"


def _windows_create_file_handle(
    path: Path,
    *,
    access: int,
    share: int,
    creation: int,
    flags: int,
) -> int:
    library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create_file = library.CreateFileW
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
    handle = create_file(
        os.fspath(path),
        access,
        share,
        None,
        creation,
        flags,
        None,
    )
    value = ctypes.cast(handle, ctypes.c_void_p).value
    if value in {None, ctypes.c_void_p(-1).value}:
        error_number = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise OSError(error_number, "Windows could not open a held release marker.")
    assert value is not None
    return int(value)


def _windows_close_handle(handle: int) -> None:
    library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    close_handle = library.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _windows_descriptor_from_handle(handle: int, flags: int) -> int:
    import msvcrt

    return int(msvcrt.open_osfhandle(handle, flags))  # type: ignore[attr-defined]


def _windows_mark_handle_for_deletion(handle: int) -> None:
    library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    set_information = library.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    delete_file = ctypes.c_ubyte(1)
    if not set_information(
        handle,
        4,
        ctypes.byref(delete_file),
        ctypes.sizeof(delete_file),
    ):
        error_number = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise OSError(error_number, "Windows could not delete an owned release path.")


def _windows_mark_descriptor_for_deletion(descriptor: int) -> None:
    import msvcrt

    _windows_mark_handle_for_deletion(
        msvcrt.get_osfhandle(descriptor)  # type: ignore[attr-defined]
    )


def _open_windows_shared_marker(path: Path, *, create: bool) -> int:
    generic_read = 0x80000000
    generic_write = 0x40000000
    delete_access = 0x00010000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    create_new = 1
    open_existing = 3
    file_attribute_normal = 0x00000080
    open_reparse_point = 0x00200000
    handle = _windows_create_file_handle(
        path,
        access=generic_read | generic_write | delete_access,
        share=share_read_write_delete,
        creation=create_new if create else open_existing,
        flags=file_attribute_normal | open_reparse_point,
    )
    descriptor_flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        return _windows_descriptor_from_handle(handle, descriptor_flags)
    except BaseException:
        if create:
            try:
                _windows_mark_handle_for_deletion(handle)
            except OSError:
                pass
        _windows_close_handle(handle)
        raise


def _open_held_marker(
    path: Path,
    *,
    create: bool,
    directory_descriptor: int | None = None,
) -> int:
    if _uses_windows_capability_handles():
        if directory_descriptor is not None:
            raise OSError("Windows marker paths must be absolute.")
        return _open_windows_shared_marker(path, create=create)
    flags = os.O_RDWR
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags, 0o600, dir_fd=directory_descriptor)


def _open_windows_delete_descriptor(path: Path, *, directory: bool) -> int:
    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    handle = _windows_create_file_handle(
        path,
        access=delete_access | file_read_attributes,
        share=share_read_write_delete,
        creation=open_existing,
        flags=open_reparse_point | (backup_semantics if directory else 0),
    )
    descriptor_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        return _windows_descriptor_from_handle(handle, descriptor_flags)
    except BaseException:
        _windows_close_handle(handle)
        raise


def _marker_identity_at(
    marker_name: str,
    *,
    directory_descriptor: int | None,
    marker_path: Path,
) -> _DirectoryIdentity:
    if directory_descriptor is not None:
        value = os.stat(
            marker_name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    else:
        value = os.lstat(marker_path)
    return _DirectoryIdentity.from_stat(value)


def _delete_held_marker(
    marker_descriptor: int,
    marker_name: str,
    *,
    directory_descriptor: int | None,
    marker_path: Path,
    expected: _DirectoryIdentity | None = None,
    consume_on_failure: bool = True,
) -> None:
    """Delete a proven marker, retaining its descriptor on requested failure."""

    deleted = False
    try:
        held = _DirectoryIdentity.from_stat(os.fstat(marker_descriptor))
        current = _marker_identity_at(
            marker_name,
            directory_descriptor=directory_descriptor,
            marker_path=marker_path,
        )
        if not held.same_object(current) or (
            expected is not None and not expected.same_object(held)
        ):
            raise SyncError(
                "SYNC_OUTPUT",
                "The release ownership marker changed unexpectedly.",
                "Cleanup cannot remove a marker it no longer owns.",
                ["Stop concurrent filesystem changes and inspect the release root."],
            )
        if _uses_windows_capability_handles():
            _windows_mark_descriptor_for_deletion(marker_descriptor)
        elif directory_descriptor is not None:
            os.unlink(marker_name, dir_fd=directory_descriptor)
        else:
            os.unlink(marker_path)
        deleted = True
    finally:
        if deleted or consume_on_failure:
            _close_descriptor_quietly(marker_descriptor)


def _open_directory_capability(path: Path, *, owned: bool) -> _DirectoryCapability:
    """Bind a directory to held directory and marker descriptors."""

    initial: _DirectoryIdentity | None = None
    descriptor: int | None = None
    marker_descriptor = -1
    marker_identity: _DirectoryIdentity | None = None
    marker_name = f".ancestryllm-capability-{uuid.uuid4().hex}"
    try:
        initial = _directory_identity(path)
        if not _uses_windows_capability_handles():
            flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            descriptor = os.open(path, flags)
            opened = _DirectoryIdentity.from_stat(os.fstat(descriptor))
            if not initial.same_object(opened):
                raise SyncError(
                    "SYNC_OUTPUT",
                    "The release directory changed while it was being claimed.",
                    "Publication cannot continue without a stable directory capability.",
                    ["Stop concurrent filesystem changes and retry."],
                )
        if descriptor is not None:
            marker_descriptor = _open_held_marker(
                Path(marker_name),
                create=True,
                directory_descriptor=descriptor,
            )
        else:
            marker_descriptor = _open_held_marker(path / marker_name, create=True)
        os.write(marker_descriptor, os.urandom(32))
        os.fsync(marker_descriptor)
        marker_identity = _DirectoryIdentity.from_stat(os.fstat(marker_descriptor))
        return _DirectoryCapability(
            selected_path=path,
            descriptor=descriptor,
            marker_name=marker_name,
            marker_descriptor=marker_descriptor,
            owned=owned,
        )
    except BaseException:
        if owned:
            parent_descriptor: int | None = None
            try:
                if initial is None:
                    initial = _raw_directory_identity(path)
                if not _uses_windows_capability_handles():
                    parent_descriptor = _open_plain_directory_descriptor(path.parent)
                owned_descriptor = descriptor
                owned_marker_descriptor = marker_descriptor if marker_descriptor >= 0 else None
                descriptor = None
                marker_descriptor = -1
                _cleanup_owned_flat_directory(
                    path,
                    path.name,
                    parent_descriptor=parent_descriptor,
                    descriptor=owned_descriptor,
                    expected=initial,
                    marker_name=(marker_name if owned_marker_descriptor is not None else None),
                    marker_descriptor=owned_marker_descriptor,
                    marker_identity=marker_identity,
                    allowed_names=frozenset(),
                )
            except BaseException as cleanup_error:  # noqa: BLE001 - best-effort rollback
                del cleanup_error
            finally:
                _close_descriptor_quietly(parent_descriptor)
        elif marker_descriptor >= 0:
            try:
                held_marker_descriptor = marker_descriptor
                marker_descriptor = -1
                _delete_held_marker(
                    held_marker_descriptor,
                    marker_name,
                    directory_descriptor=descriptor,
                    marker_path=path / marker_name,
                    expected=marker_identity,
                )
            except (OSError, SyncError, ValueError):
                pass
        _close_descriptor_quietly(marker_descriptor if marker_descriptor >= 0 else None)
        _close_descriptor_quietly(descriptor)
        raise


def _capability_matches_selected(capability: _DirectoryCapability) -> bool:
    """Return whether the selected path still contains the held marker and root."""

    try:
        selected = os.lstat(capability.selected_path)
        marker = os.lstat(capability.selected_path / capability.marker_name)
        marker_held = os.fstat(capability.marker_descriptor)
        selected_identity = _DirectoryIdentity.from_stat(selected)
        marker_identity = _DirectoryIdentity.from_stat(marker)
        marker_held_identity = _DirectoryIdentity.from_stat(marker_held)
        if not stat.S_ISDIR(selected.st_mode) or marker_identity != marker_held_identity:
            return False
        if capability.descriptor is not None:
            held = _DirectoryIdentity.from_stat(os.fstat(capability.descriptor))
            return selected_identity == held
        return os.path.normcase(os.path.abspath(capability.selected_path)) == os.path.normcase(
            os.path.abspath(_capability_current_path(capability))
        )
    except (OSError, SyncError, ValueError):
        return False


def _require_selected_capability(capability: _DirectoryCapability) -> None:
    """Reject release-root replacement before another filesystem side effect."""

    if not _capability_matches_selected(capability):
        raise SyncError(
            "SYNC_OUTPUT",
            "The release root changed while the operation was active.",
            "Continuing could publish into a directory owned by another process.",
            ["Restore the selected --release-root and retry without concurrent changes."],
        )


def _remove_capability_marker(capability: _DirectoryCapability) -> None:
    """Remove only the marker proven by the held descriptor."""

    if capability.marker_descriptor < 0:
        return
    marker_path = _held_file_path(capability.marker_descriptor)
    marker_descriptor = capability.marker_descriptor
    _delete_held_marker(
        marker_descriptor,
        capability.marker_name,
        directory_descriptor=capability.descriptor,
        marker_path=marker_path,
        consume_on_failure=False,
    )
    capability.marker_descriptor = -1


def _close_capability(capability: _DirectoryCapability) -> None:
    """Remove the owned marker and close held descriptors."""

    _remove_capability_marker(capability)
    if capability.descriptor is not None:
        descriptor = capability.descriptor
        capability.descriptor = None
        _close_descriptor_quietly(descriptor)


def _close_capability_quietly(capability: _DirectoryCapability) -> None:
    for _attempt in range(2):
        try:
            _close_capability(capability)
            return
        except BaseException as exc:  # noqa: BLE001 - cleanup must survive interruption
            del exc
    if capability.marker_descriptor >= 0:
        marker_descriptor = capability.marker_descriptor
        capability.marker_descriptor = -1
        _close_descriptor_quietly(marker_descriptor)
    if capability.descriptor is not None:
        descriptor = capability.descriptor
        capability.descriptor = None
        _close_descriptor_quietly(descriptor)


_STAGING_CLEANUP_NAMES = frozenset(
    {
        "manifest.json",
        "master.ged",
        "quality.md",
        "rollback.json",
        "update.md",
    }
)


def _open_plain_directory_descriptor(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return os.open(path, flags)


def _open_plain_directory_entry_descriptor(name: str, parent_descriptor: int) -> int:
    """Open one directory entry relative to a held parent descriptor."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return os.open(name, flags, dir_fd=parent_descriptor)


def _close_descriptor_quietly(descriptor: int | None) -> None:
    if descriptor is None or descriptor < 0:
        return
    for _attempt in range(2):
        try:
            os.close(descriptor)
            return
        except OSError:
            return
        except BaseException as exc:  # noqa: BLE001 - cleanup must survive interruption
            del exc


def _is_flat_cleanup_entry(value: os.stat_result) -> bool:
    return stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode)


def _cleanup_owned_flat_directory(
    path: Path,
    name: str,
    *,
    parent_descriptor: int | None,
    descriptor: int | None,
    expected: _DirectoryIdentity,
    marker_name: str | None,
    marker_descriptor: int | None,
    marker_identity: _DirectoryIdentity | None,
    allowed_names: frozenset[str],
) -> bool:
    """Delete a known flat directory through held capabilities, never recursively.

    All supplied directory and marker descriptors are consumed, including when
    validation fails.
    """

    opened_marker = marker_descriptor
    windows_directory_descriptor: int | None = None
    windows_entry_descriptors: list[int] = []
    try:
        if _uses_windows_capability_handles():
            current_path = (
                _held_file_path(opened_marker).parent if opened_marker is not None else path
            )
            windows_directory_descriptor = _open_windows_delete_descriptor(
                current_path,
                directory=True,
            )
            held_directory = _DirectoryIdentity.from_stat(os.fstat(windows_directory_descriptor))
            current_directory_stat = os.lstat(current_path)
            current_directory = _DirectoryIdentity.from_stat(current_directory_stat)
            if (
                not stat.S_ISDIR(current_directory_stat.st_mode)
                or not expected.same_object(held_directory)
                or not held_directory.same_object(current_directory)
            ):
                return False
            if marker_name is not None and opened_marker is None:
                opened_marker = _open_held_marker(
                    current_path / marker_name,
                    create=False,
                )
            if marker_name is not None:
                if opened_marker is None:
                    return False
                held_marker_stat = os.fstat(opened_marker)
                held_marker = _DirectoryIdentity.from_stat(held_marker_stat)
                expected_marker = marker_identity or held_marker
                current_marker = _marker_identity_at(
                    marker_name,
                    directory_descriptor=None,
                    marker_path=current_path / marker_name,
                )
                if (
                    not stat.S_ISREG(held_marker_stat.st_mode)
                    or not expected_marker.same_object(held_marker)
                    or not held_marker.same_object(current_marker)
                ):
                    return False
            entries = set(os.listdir(current_path))
            expected_names = set(allowed_names)
            if marker_name is not None:
                expected_names.add(marker_name)
                if marker_name not in entries:
                    return False
            if not entries.issubset(expected_names):
                return False
            for entry_name in sorted(entries):
                if entry_name == marker_name:
                    continue
                entry_path = current_path / entry_name
                entry_stat = os.lstat(entry_path)
                if not _is_flat_cleanup_entry(entry_stat):
                    return False
                entry_descriptor = _open_windows_delete_descriptor(
                    entry_path,
                    directory=False,
                )
                windows_entry_descriptors.append(entry_descriptor)
                opened_identity = _DirectoryIdentity.from_stat(os.fstat(entry_descriptor))
                current_identity = _DirectoryIdentity.from_stat(os.lstat(entry_path))
                if not opened_identity.same_object(
                    _DirectoryIdentity.from_stat(entry_stat)
                ) or not opened_identity.same_object(current_identity):
                    return False
            current_directory = _DirectoryIdentity.from_stat(os.lstat(current_path))
            held_directory = _DirectoryIdentity.from_stat(os.fstat(windows_directory_descriptor))
            if not expected.same_object(held_directory) or not held_directory.same_object(
                current_directory
            ):
                return False
            for entry_descriptor in windows_entry_descriptors:
                _windows_mark_descriptor_for_deletion(entry_descriptor)
                os.close(entry_descriptor)
            windows_entry_descriptors.clear()
            if marker_name is not None:
                assert opened_marker is not None
                marker_to_delete = opened_marker
                opened_marker = None
                _delete_held_marker(
                    marker_to_delete,
                    marker_name,
                    directory_descriptor=None,
                    marker_path=current_path / marker_name,
                    expected=marker_identity,
                )
            _windows_mark_descriptor_for_deletion(windows_directory_descriptor)
            os.close(windows_directory_descriptor)
            windows_directory_descriptor = None
            return True

        if descriptor is None:
            descriptor = _open_plain_directory_descriptor(path)
        if parent_descriptor is None:
            return False
        held_directory = _DirectoryIdentity.from_stat(os.fstat(descriptor))
        current_directory_stat = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        current_directory = _DirectoryIdentity.from_stat(current_directory_stat)
        if (
            not stat.S_ISDIR(current_directory_stat.st_mode)
            or not expected.same_object(held_directory)
            or not held_directory.same_object(current_directory)
        ):
            return False
        if marker_name is not None and opened_marker is None:
            opened_marker = _open_held_marker(
                Path(marker_name),
                create=False,
                directory_descriptor=descriptor,
            )
        if marker_name is not None:
            if opened_marker is None:
                return False
            held_marker_stat = os.fstat(opened_marker)
            held_marker = _DirectoryIdentity.from_stat(held_marker_stat)
            expected_marker = marker_identity or held_marker
            current_marker = _marker_identity_at(
                marker_name,
                directory_descriptor=descriptor,
                marker_path=path / marker_name,
            )
            if (
                not stat.S_ISREG(held_marker_stat.st_mode)
                or not expected_marker.same_object(held_marker)
                or not held_marker.same_object(current_marker)
            ):
                return False
        entries = set(os.listdir(descriptor))
        expected_names = set(allowed_names)
        if marker_name is not None:
            expected_names.add(marker_name)
            if marker_name not in entries:
                return False
        if not entries.issubset(expected_names):
            return False
        for entry_name in sorted(entries):
            entry_stat = os.stat(
                entry_name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if entry_name == marker_name:
                if not stat.S_ISREG(entry_stat.st_mode):
                    return False
            elif not _is_flat_cleanup_entry(entry_stat):
                return False
        current_directory = _DirectoryIdentity.from_stat(
            os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        )
        held_directory = _DirectoryIdentity.from_stat(os.fstat(descriptor))
        if not expected.same_object(held_directory) or not held_directory.same_object(
            current_directory
        ):
            return False
        for entry_name in sorted(entries):
            if entry_name != marker_name:
                os.unlink(entry_name, dir_fd=descriptor)
        if marker_name is not None:
            assert opened_marker is not None
            marker_to_delete = opened_marker
            opened_marker = None
            _delete_held_marker(
                marker_to_delete,
                marker_name,
                directory_descriptor=descriptor,
                marker_path=path / marker_name,
                expected=marker_identity,
            )
        os.fsync(descriptor)
        # POSIX has no portable primitive that removes a directory by its held
        # descriptor. A final ``rmdir(name, dir_fd=parent_descriptor)`` would
        # reopen a namespace race and could delete an empty foreign replacement
        # after the last identity check. Fail closed: leave only the now-empty
        # app-owned directory for explicit inspection/removal.
        return False
    finally:
        for entry_descriptor in windows_entry_descriptors:
            _close_descriptor_quietly(entry_descriptor)
        _close_descriptor_quietly(windows_directory_descriptor)
        _close_descriptor_quietly(opened_marker)
        _close_descriptor_quietly(descriptor)


def _cleanup_capability_tree(capability: _DirectoryCapability) -> None:
    """Delete only an owned, marker-only capability directory."""

    parent_descriptor: int | None = None
    current_path: Path | None = None
    marker_identity: _DirectoryIdentity | None = None
    cleaned = False
    try:
        current_path = _capability_current_path(capability)
        expected = (
            _DirectoryIdentity.from_stat(os.fstat(capability.descriptor))
            if capability.descriptor is not None
            else _directory_identity(current_path)
        )
        marker_identity = _DirectoryIdentity.from_stat(os.fstat(capability.marker_descriptor))
        if not _uses_windows_capability_handles():
            parent_descriptor = _open_plain_directory_descriptor(current_path.parent)
        descriptor = capability.descriptor
        marker_descriptor = capability.marker_descriptor
        capability.descriptor = None
        capability.marker_descriptor = -1
        cleaned = _cleanup_owned_flat_directory(
            current_path,
            current_path.name,
            parent_descriptor=parent_descriptor,
            descriptor=descriptor,
            expected=expected,
            marker_name=capability.marker_name,
            marker_descriptor=marker_descriptor,
            marker_identity=marker_identity,
            allowed_names=frozenset(),
        )
        if not cleaned and current_path is not None and marker_identity is not None:
            reopened_marker = _open_held_marker(
                current_path / capability.marker_name,
                create=False,
            )
            _delete_held_marker(
                reopened_marker,
                capability.marker_name,
                directory_descriptor=None,
                marker_path=current_path / capability.marker_name,
                expected=marker_identity,
            )
    except BaseException:  # noqa: BLE001 - cleanup must survive interruption
        _close_capability_quietly(capability)
    finally:
        _close_descriptor_quietly(parent_descriptor)


def _cleanup_empty_release_root(capability: _DirectoryCapability) -> None:
    """Remove an owned root only when its held directory remains otherwise empty."""

    if not capability.owned:
        _close_capability_quietly(capability)
        return
    _cleanup_capability_tree(capability)


def _cleanup_preselected_empty_directory(path: Path) -> None:
    """Best-effort cleanup for an unguessable mkdir candidate.

    The candidate name is selected before ``mkdir`` so a wrapper that raises
    after the syscall cannot hide which directory may have been created. The
    flat cleanup remains identity-bound and deliberately retains the empty
    directory where final directory deletion is name-bound. On Windows, an
    exception raised by a ``mkdir`` wrapper leaves no held handle or marker
    proving that the current pathname is still the created directory, so this
    ambiguous boundary must also fail closed without touching it.
    """

    if _uses_windows_capability_handles():
        return
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        expected = _raw_directory_identity(path)
        if not _uses_windows_capability_handles():
            parent_descriptor = _open_plain_directory_descriptor(path.parent)
            descriptor = _open_plain_directory_descriptor(path)
        _cleanup_owned_flat_directory(
            path,
            path.name,
            parent_descriptor=parent_descriptor,
            descriptor=descriptor,
            expected=expected,
            marker_name=None,
            marker_descriptor=None,
            marker_identity=None,
            allowed_names=frozenset(),
        )
        descriptor = None
    except BaseException as cleanup_error:  # noqa: BLE001 - best-effort cleanup
        del cleanup_error
    finally:
        _close_descriptor_quietly(descriptor)
        _close_descriptor_quietly(parent_descriptor)


def _ensure_release_root(path: Path) -> _DirectoryCapability:
    """Return a held capability for a preexisting or exclusively created root."""

    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be inspected safely.",
            "Release ownership must be proven before publication.",
            ["Choose an accessible local --release-root and retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    else:
        return _open_directory_capability(path, owned=False)

    try:
        _directory_identity(path.parent)
    except (OSError, SyncError, ValueError) as exc:
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be created safely.",
            "Its parent must already be a stable, writable local directory.",
            ["Create the parent directory explicitly, then retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    candidate: Path | None = None
    creation_error: BaseException | None = None
    for _attempt in range(8):
        selected_candidate = path.parent / f".ancestryllm-release-root-{uuid.uuid4().hex}"
        try:
            os.mkdir(selected_candidate, 0o700)
        except FileExistsError:
            continue
        except BaseException as exc:
            _cleanup_preselected_empty_directory(selected_candidate)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            creation_error = exc
            break
        candidate = selected_candidate
        break
    if candidate is None:
        error_class = (
            type(creation_error).__name__ if creation_error is not None else "FileExistsError"
        )
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be created safely.",
            "Its parent must already be a stable, writable local directory.",
            ["Create the parent directory explicitly, then retry."],
            details=(f"Error class: {error_class}",),
        ) from creation_error
    candidate_capability = _open_directory_capability(candidate, owned=True)
    try:
        _exclusive_rename_directory(candidate, path)
    except OSError as exc:
        _cleanup_capability_tree(candidate_capability)
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY, errno.EISDIR, errno.ENOTDIR}:
            return _open_directory_capability(path, owned=False)
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be claimed safely.",
            "The filesystem did not provide exclusive no-replace directory publication.",
            ["Choose a supported local filesystem and retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    except BaseException:
        _cleanup_capability_tree(candidate_capability)
        raise
    candidate_capability.selected_path = path
    try:
        _require_selected_capability(candidate_capability)
    except BaseException:
        _cleanup_capability_tree(candidate_capability)
        raise
    return candidate_capability


def _create_staging_directory(
    release_root: _DirectoryCapability,
    prefix: str,
) -> tuple[
    Path,
    str,
    int | None,
    _DirectoryIdentity,
    str,
    int,
    _DirectoryIdentity,
]:
    """Create staging under the physical held root and retain its identity."""

    _require_selected_capability(release_root)
    name = f"{prefix}{uuid.uuid4().hex}"
    path = _capability_current_path(release_root) / name
    created = False
    identity: _DirectoryIdentity | None = None
    descriptor: int | None = None
    marker_name = f".ancestryllm-staging-{uuid.uuid4().hex}"
    marker_descriptor: int | None = None
    marker_identity: _DirectoryIdentity | None = None
    try:
        if release_root.descriptor is not None:
            os.mkdir(name, 0o700, dir_fd=release_root.descriptor)
        else:
            path.mkdir(mode=0o700)
        created = True
        path = _capability_current_path(release_root) / name
        if release_root.descriptor is not None:
            entry_stat = os.stat(
                name,
                dir_fd=release_root.descriptor,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(entry_stat.st_mode):
                raise SyncError(
                    "SYNC_OUTPUT",
                    "The staging path is not a real directory.",
                    "Publication cannot continue without stable staging ownership.",
                    ["Stop concurrent filesystem changes and retry."],
                )
            identity = _DirectoryIdentity.from_stat(entry_stat)
            descriptor = _open_plain_directory_entry_descriptor(
                name,
                release_root.descriptor,
            )
            opened = _DirectoryIdentity.from_stat(os.fstat(descriptor))
            if not identity.same_object(opened):
                raise SyncError(
                    "SYNC_OUTPUT",
                    "The staging directory changed while it was being claimed.",
                    "Publication cannot continue without stable staging ownership.",
                    ["Stop concurrent filesystem changes and retry."],
                )
        else:
            identity = _directory_identity(path)
        if descriptor is not None:
            marker_descriptor = _open_held_marker(
                Path(marker_name),
                create=True,
                directory_descriptor=descriptor,
            )
        else:
            marker_descriptor = _open_held_marker(path / marker_name, create=True)
        os.write(marker_descriptor, os.urandom(32))
        os.fsync(marker_descriptor)
        marker_identity = _DirectoryIdentity.from_stat(os.fstat(marker_descriptor))
        _require_selected_capability(release_root)
        return (
            path,
            name,
            descriptor,
            identity,
            marker_name,
            marker_descriptor,
            marker_identity,
        )
    except BaseException:
        if identity is None:
            try:
                path = _capability_current_path(release_root) / name
                if release_root.descriptor is not None:
                    entry_stat = os.stat(
                        name,
                        dir_fd=release_root.descriptor,
                        follow_symlinks=False,
                    )
                    if not stat.S_ISDIR(entry_stat.st_mode):
                        raise ValueError("staging path is not an ordinary directory")
                    identity = _DirectoryIdentity.from_stat(entry_stat)
                    if descriptor is None:
                        descriptor = _open_plain_directory_entry_descriptor(
                            name,
                            release_root.descriptor,
                        )
                    opened = _DirectoryIdentity.from_stat(os.fstat(descriptor))
                    if not identity.same_object(opened):
                        raise ValueError("staging directory identity changed")
                else:
                    identity = _raw_directory_identity(path)
                created = True
            except (OSError, SyncError, ValueError):
                identity = None
        try:
            if created and identity is not None:
                _cleanup_owned_flat_directory(
                    path,
                    name,
                    parent_descriptor=release_root.descriptor,
                    descriptor=descriptor,
                    expected=identity,
                    marker_name=marker_name if marker_descriptor is not None else None,
                    marker_descriptor=marker_descriptor,
                    marker_identity=marker_identity,
                    allowed_names=frozenset(),
                )
            else:
                _close_descriptor_quietly(marker_descriptor)
                _close_descriptor_quietly(descriptor)
        except BaseException:  # noqa: BLE001 - descriptor cleanup must not leak
            _close_descriptor_quietly(marker_descriptor)
            _close_descriptor_quietly(descriptor)
        raise


def _cleanup_staging_directory(
    release_root: _DirectoryCapability,
    name: str,
    descriptor: int | None,
    expected: _DirectoryIdentity,
    marker_name: str,
    marker_descriptor: int | None,
    marker_identity: _DirectoryIdentity,
) -> None:
    """Delete staging only when it still matches its held live identity."""

    try:
        physical_root = _capability_current_path(release_root)
        path = physical_root / name
        cleanup_marker_name: str | None = marker_name
        cleanup_marker_identity: _DirectoryIdentity | None = marker_identity
        if (
            not _uses_windows_capability_handles()
            and descriptor is not None
            and marker_descriptor is not None
        ):
            held_marker_stat = os.fstat(marker_descriptor)
            held_marker = _DirectoryIdentity.from_stat(held_marker_stat)
            held_directory = _DirectoryIdentity.from_stat(os.fstat(descriptor))
            if (
                held_marker_stat.st_nlink == 0
                and marker_identity.same_object(held_marker)
                and expected.same_object(held_directory)
            ):
                cleanup_marker_name = None
                cleanup_marker_identity = None
        _cleanup_owned_flat_directory(
            path,
            name,
            parent_descriptor=release_root.descriptor,
            descriptor=descriptor,
            expected=expected,
            marker_name=cleanup_marker_name,
            marker_descriptor=marker_descriptor,
            marker_identity=cleanup_marker_identity,
            allowed_names=_STAGING_CLEANUP_NAMES,
        )
    except BaseException:  # noqa: BLE001 - best-effort staging cleanup
        return


def _publication_destination_is_selected(
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
) -> bool:
    """Prove the held publication is at the selected destination."""

    if not _capability_matches_selected(release_root):
        return False
    try:
        if release_root.descriptor is not None:
            current_stat = os.stat(
                destination_name,
                dir_fd=release_root.descriptor,
                follow_symlinks=False,
            )
        else:
            current_stat = os.lstat(release_root.selected_path / destination_name)
        if not stat.S_ISDIR(current_stat.st_mode):
            return False
        current = _DirectoryIdentity.from_stat(current_stat)
        held = (
            _DirectoryIdentity.from_stat(os.fstat(directory_descriptor))
            if directory_descriptor is not None
            else current
        )
    except (OSError, SyncError, ValueError):
        return False
    return directory_identity.same_object(held) and held.same_object(current)


def _publication_root_changed_error() -> SyncError:
    return SyncError(
        "SYNC_OUTPUT",
        "The release root changed during final publication.",
        "The selected path no longer contains the held release destination.",
        ["Restore the selected --release-root and retry without concurrent changes."],
    )


def _publication_incomplete_error(cause: BaseException) -> SyncError:
    return SyncError(
        "SYNC_PUBLICATION_INCOMPLETE",
        "Release publication could not be finalized or rolled back.",
        "An incomplete generation directory with an ownership marker may remain.",
        [
            "Do not use or rename the incomplete generation as a release.",
            "Inspect the release root and remove only an empty app-owned residue.",
            "Retry with a new patch version after the filesystem issue is resolved.",
        ],
        details=(f"Error class: {type(cause).__name__}",),
    )


def _remove_published_staging_marker(
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    destination_name: str,
    release_root: _DirectoryCapability,
    transaction: _PublicationTransactionState,
) -> BaseException | None:
    """Remove the marker at the irreversible commit boundary.

    A failed delete retains the open marker descriptor in ``transaction`` so
    rollback and location proofs remain capability-bound.
    """

    descriptor = transaction.marker_descriptor
    if descriptor is None:
        return SyncError(
            "SYNC_OUTPUT",
            "The staged release marker capability is unavailable.",
            "Publication cannot be finalized without retained ownership.",
            ["Retry with a new patch version after inspecting the release root."],
        )
    try:
        if not _publication_destination_is_selected(
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
        ):
            raise _publication_root_changed_error()
        marker_path = _held_file_path(descriptor)
        held_stat = os.fstat(descriptor)
        held = _DirectoryIdentity.from_stat(held_stat)
        current = _marker_identity_at(
            marker_name,
            directory_descriptor=directory_descriptor,
            marker_path=marker_path,
        )
        if (
            not stat.S_ISREG(held_stat.st_mode)
            or not marker_identity.same_object(held)
            or not held.same_object(current)
        ):
            raise SyncError(
                "SYNC_OUTPUT",
                "The staged release marker changed unexpectedly.",
                "The committed directory cannot be finalized without proving marker ownership.",
                ["Inspect the release root and retry with a new patch version if needed."],
            )
        if not _publication_destination_is_selected(
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
        ):
            raise _publication_root_changed_error()
        if _uses_windows_capability_handles():
            _windows_mark_descriptor_for_deletion(descriptor)
            close_error: BaseException | None = None
            for _attempt in range(2):
                try:
                    os.close(descriptor)
                    close_error = None
                    break
                except OSError as exc:
                    if exc.errno == errno.EBADF:
                        close_error = None
                        break
                    close_error = exc
                except BaseException as exc:  # noqa: BLE001 - preserve original failure
                    close_error = exc
            if close_error is not None:
                return close_error
            transaction.committed = True
            transaction.marker_descriptor = None
            return None
        elif directory_descriptor is not None:
            os.unlink(marker_name, dir_fd=directory_descriptor)
        else:
            os.unlink(marker_path)
        if not _publication_destination_is_selected(
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
        ):
            raise _publication_root_changed_error()
    except BaseException as exc:  # noqa: BLE001 - return publication failure atomically
        return exc
    transaction.committed = True
    transaction.marker_descriptor = None
    _close_descriptor_quietly(descriptor)
    return None


def _rollback_published_directory(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
) -> bool:
    """Move a not-yet-finalized commit back to its private staging name."""

    try:
        physical_root = _capability_current_path(release_root)
        source = (
            Path(destination_name)
            if release_root.descriptor is not None
            else physical_root / destination_name
        )
        destination = (
            Path(staging_name)
            if release_root.descriptor is not None
            else physical_root / staging_name
        )
        _exclusive_rename_directory(
            source,
            destination,
            source_dir_fd=release_root.descriptor,
            destination_dir_fd=release_root.descriptor,
        )
    except (OSError, SyncError):
        return False
    return True


def _finalize_published_directory(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> BaseException | None:
    """Finalize a commit, or roll it back before exposing marker cleanup failure."""

    error = _remove_published_staging_marker(
        marker_name,
        marker_identity,
        directory_descriptor,
        directory_identity,
        destination_name,
        release_root,
        transaction,
    )
    if error is None:
        return None
    return _recover_interrupted_publication(
        error,
        staging_name,
        destination_name,
        release_root,
        directory_descriptor,
        directory_identity,
        marker_name,
        marker_identity,
        transaction,
    )


def _publish_directory_no_clobber(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
) -> None:
    """Publish within the held root, rollback, and fail if its selected path moved."""

    physical_root = _capability_current_path(release_root)
    source = (
        Path(staging_name) if release_root.descriptor is not None else physical_root / staging_name
    )
    destination = (
        Path(destination_name)
        if release_root.descriptor is not None
        else physical_root / destination_name
    )
    try:
        _require_selected_capability(release_root)
        _exclusive_rename_directory(
            source,
            destination,
            source_dir_fd=release_root.descriptor,
            destination_dir_fd=release_root.descriptor,
        )
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY, errno.EISDIR, errno.ENOTDIR}:
            raise SyncError(
                "SYNC_OUTPUT",
                "The release destination was claimed by another operation.",
                "Existing and concurrent release paths are immutable and cannot be replaced.",
                ["Wait one second and retry the operation."],
            ) from exc
        raise SyncError(
            "SYNC_OUTPUT",
            "The staged release could not be published safely.",
            "The filesystem did not complete an exclusive no-replace directory rename.",
            ["Check free space and use a supported local filesystem, then retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    if _capability_matches_selected(release_root):
        return
    try:
        _exclusive_rename_directory(
            destination,
            source,
            source_dir_fd=release_root.descriptor,
            destination_dir_fd=release_root.descriptor,
        )
    except OSError:
        pass
    raise SyncError(
        "SYNC_OUTPUT",
        "The release root changed during final publication.",
        "The staged generation was rolled back inside the held original root.",
        ["Restore the selected --release-root and retry without concurrent changes."],
    )


def _held_staging_location(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> str | None:
    """Return the owned staging directory's current transaction name."""

    marker_descriptor = transaction.marker_descriptor
    if marker_descriptor is None:
        return None
    try:
        held_marker_stat = os.fstat(marker_descriptor)
        held_marker = _DirectoryIdentity.from_stat(held_marker_stat)
        if not stat.S_ISREG(held_marker_stat.st_mode) or not marker_identity.same_object(
            held_marker
        ):
            return None
        marker_is_unlinked = held_marker_stat.st_nlink == 0
        if marker_is_unlinked:
            if _uses_windows_capability_handles() or directory_descriptor is None:
                return None
            try:
                os.stat(
                    marker_name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                return None
            current_directory_path = _held_file_path(directory_descriptor)
        else:
            marker_path = _held_file_path(marker_descriptor)
            current_marker = _DirectoryIdentity.from_stat(os.lstat(marker_path))
            if marker_path.name != marker_name or not held_marker.same_object(current_marker):
                return None
            current_directory_path = marker_path.parent
        physical_root = _capability_current_path(release_root)
        if os.path.normcase(os.path.abspath(current_directory_path.parent)) != os.path.normcase(
            os.path.abspath(physical_root)
        ):
            return None
        current_directory_stat = os.lstat(current_directory_path)
        current_directory = _DirectoryIdentity.from_stat(current_directory_stat)
        held_directory = (
            _DirectoryIdentity.from_stat(os.fstat(directory_descriptor))
            if directory_descriptor is not None
            else current_directory
        )
        if (
            not stat.S_ISDIR(current_directory_stat.st_mode)
            or not directory_identity.same_object(held_directory)
            or not held_directory.same_object(current_directory)
        ):
            return None
        if current_directory_path.name in {staging_name, destination_name}:
            return current_directory_path.name
    except (OSError, SyncError, ValueError):
        return None
    return None


def _prove_committed_unlinked_marker(
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> bool:
    """Recover a POSIX commit interrupted after unlink but before state storage."""

    marker_descriptor = transaction.marker_descriptor
    if (
        _uses_windows_capability_handles()
        or marker_descriptor is None
        or directory_descriptor is None
        or release_root.descriptor is None
    ):
        return False
    try:
        marker_stat = os.fstat(marker_descriptor)
        marker_held = _DirectoryIdentity.from_stat(marker_stat)
        if (
            not stat.S_ISREG(marker_stat.st_mode)
            or marker_stat.st_nlink != 0
            or not marker_identity.same_object(marker_held)
        ):
            return False
        try:
            os.stat(
                marker_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            return False
        if not _publication_destination_is_selected(
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
        ):
            return False
    except (OSError, SyncError, ValueError):
        return False
    transaction.committed = True
    transaction.marker_descriptor = None
    _close_descriptor_quietly(marker_descriptor)
    return True


def _recover_interrupted_publication(
    error: BaseException,
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> BaseException | None:
    """Roll an interrupted owned rename back while ownership remains held."""

    if transaction.committed:
        return None
    if _prove_committed_unlinked_marker(
        destination_name,
        release_root,
        directory_descriptor,
        directory_identity,
        marker_name,
        marker_identity,
        transaction,
    ):
        return None
    location = _held_staging_location(
        staging_name,
        destination_name,
        release_root,
        directory_descriptor,
        directory_identity,
        marker_name,
        marker_identity,
        transaction,
    )
    if location == destination_name:
        try:
            _rollback_published_directory(
                staging_name,
                destination_name,
                release_root,
            )
        except BaseException as rollback_error:  # noqa: BLE001 - best-effort rollback
            del rollback_error
        location = _held_staging_location(
            staging_name,
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
            marker_name,
            marker_identity,
            transaction,
        )
    if location == staging_name:
        return error
    if location != destination_name:
        return _publication_incomplete_error(error)
    cleanup_error = _remove_published_staging_marker(
        marker_name,
        marker_identity,
        directory_descriptor,
        directory_identity,
        destination_name,
        release_root,
        transaction,
    )
    if cleanup_error is None:
        return None
    return _publication_incomplete_error(cleanup_error)


def _publish_and_finalize_directory(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> BaseException | None:
    """Treat no-clobber publication and marker finalization as one transaction."""

    try:
        _publish_directory_no_clobber(
            staging_name,
            destination_name,
            release_root,
        )
        return _finalize_published_directory(
            staging_name,
            destination_name,
            release_root,
            marker_name,
            marker_identity,
            directory_descriptor,
            directory_identity,
            transaction,
        )
    except BaseException as exc:  # noqa: BLE001 - rollback on interruption too
        if transaction.committed:
            return None
        return _recover_interrupted_publication(
            exc,
            staging_name,
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
            marker_name,
            marker_identity,
            transaction,
        )
