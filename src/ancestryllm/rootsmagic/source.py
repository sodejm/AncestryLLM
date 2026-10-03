"""Hardened, immutable SQLite access for RootsMagic databases."""

from __future__ import annotations

import base64
import errno
import hashlib
import math
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlglot import exp, parse

from ancestryllm.core.cancellation import (
    CancellationError,
    cancellation_checkpoint,
    current_cancellation_token,
)
from ancestryllm.core.errors import AncestryError, FileIngressError, SecurityPolicyError
from ancestryllm.core.ingress import (
    FileFingerprint,
    FileIngressPolicy,
    FileKind,
    FileSnapshot,
    cleanup_open_path,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

DENIED_ACTIONS = {
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_ANALYZE,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_PRAGMA,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
}
FORBIDDEN_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Transaction,
    exp.Merge,
)
_ARCHIVE_SIGNATURES = (
    b"PK\x03\x04",
    b"\x1f\x8b",
    b"BZh",
    b"\xfd7zXZ\x00",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
)
_READ_CHUNK_BYTES = 1024 * 1024
_SQLITE_MAX_COLUMNS = 32_767
_SQLITE_VARINT_MAX_BYTES = 9


@dataclass(frozen=True, slots=True)
class _AllowedRoot:
    path: Path
    identity: tuple[int, int] | None


def sha256_file(path: Path) -> str:
    """Compute the SHA-256 digest of a source file without modifying it."""
    digest, _ = FileIngressPolicy().sha256(path, FileKind.ROOTSMAGIC)
    return digest


@dataclass(frozen=True, slots=True)
class TruncationMetadata:
    """Serializable evidence describing the enforced query row bound."""

    row_limit: int
    returned_rows: int
    has_more: bool


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, str]


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Return immutable column names and rows from a validated RootsMagic query."""

    columns: tuple[str, ...]
    rows: tuple[tuple[JsonValue, ...], ...]
    sql: str
    truncated: bool
    truncation: TruncationMetadata


@dataclass(frozen=True, slots=True)
class TableSchema:
    """Deterministic, JSON-safe schema description for one source table."""

    name: str
    columns: tuple[str, ...]
    declared_types: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class DatabaseSchema:
    """Deterministic, transport-neutral schema snapshot."""

    tables: tuple[TableSchema, ...]

    def as_mapping(self) -> dict[str, tuple[str, ...]]:
        """Serialize the database schema as a plain mapping."""
        return {table.name: table.columns for table in self.tables}


@dataclass(frozen=True, slots=True)
class _WalFingerprint:
    """One stable main/WAL generation captured without opening the source in SQLite."""

    main: FileFingerprint
    wal: FileFingerprint
    shm: FileFingerprint | None

    @property
    def snapshot(self) -> FileSnapshot:
        """Keep the existing main-file snapshot interface for internal callers."""

        return self.main.snapshot

    @property
    def sha256(self) -> str:
        """Keep the existing main-file digest interface for internal callers."""

        return self.main.sha256


SourceFingerprint = FileFingerprint | _WalFingerprint


def _json_safe_value(value: Any) -> JsonValue:
    """Encode every SQLite result value without lossy implicit coercion."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"encoding": "float", "data": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "encoding": "base64",
            "data": base64.b64encode(bytes(value)).decode("ascii"),
        }
    return {"encoding": "text", "data": str(value)}


@dataclass(frozen=True, slots=True)
class _SchemaInspection:
    columns: dict[str, tuple[str, ...]]
    declared_types: dict[str, tuple[tuple[str, str], ...]]

    def snapshot(self) -> DatabaseSchema:
        return DatabaseSchema(
            tuple(
                TableSchema(
                    name=table_name,
                    columns=columns,
                    declared_types=self.declared_types.get(table_name, ()),
                )
                for table_name, columns in self.columns.items()
            )
        )


class RootsMagicReader:
    """Open a configured RootsMagic file only through SQLite read-only mode."""

    def __init__(
        self,
        allowed_directories: list[Path],
        max_rows: int = 100,
        timeout_seconds: float = 10.0,
        ingress: FileIngressPolicy | None = None,
    ) -> None:
        self.ingress = ingress or FileIngressPolicy()
        self.allowed_directories = [
            self.ingress.normalize_path(path, FileKind.ROOTSMAGIC, resolve=True)
            for path in allowed_directories
        ]
        self._allowed_roots = [
            _AllowedRoot(path, self._capture_root_identity(path))
            for path in self.allowed_directories
        ]
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self._operation_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"rootsmagic_operation_connection_{id(self)}",
            default=None,
        )
        self._operation_path: ContextVar[Path | None] = ContextVar(
            f"rootsmagic_operation_path_{id(self)}",
            default=None,
        )
        self._operation_fingerprint: ContextVar[SourceFingerprint | None] = ContextVar(
            f"rootsmagic_operation_fingerprint_{id(self)}",
            default=None,
        )
        self._operation_schema: ContextVar[dict[str, tuple[str, ...]] | None] = ContextVar(
            f"rootsmagic_operation_schema_{id(self)}",
            default=None,
        )
        self._operation_declared_types: ContextVar[
            dict[str, tuple[tuple[str, str], ...]] | None
        ] = ContextVar(
            f"rootsmagic_operation_declared_types_{id(self)}",
            default=None,
        )

    @staticmethod
    def _bound_error(
        code: str,
        message: str,
        *,
        error_type: str | None = None,
    ) -> FileIngressError:
        details = {"input_class": FileKind.ROOTSMAGIC.value}
        if error_type is not None:
            details["error_type"] = error_type
        return FileIngressError(code, message, details=details)

    @classmethod
    def _changed_error(cls, *, error_type: str | None = None) -> FileIngressError:
        return cls._bound_error(
            "FILE_INPUT_CHANGED",
            "The rootsmagic input changed while it was being consumed.",
            error_type=error_type,
        )

    @classmethod
    def _unreadable_error(cls, *, error_type: str | None = None) -> FileIngressError:
        return cls._bound_error(
            "FILE_INPUT_UNREADABLE",
            "The rootsmagic input could not be opened safely.",
            error_type=error_type,
        )

    @staticmethod
    def _windows_open_directory_handle(path: Path) -> int:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(path),
            0x0080,  # FILE_READ_ATTRIBUTES
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in {None, invalid}:
            error = ctypes.get_last_error()  # type: ignore[attr-defined]
            raise ctypes.WinError(error)  # type: ignore[attr-defined]
        return int(handle)

    @staticmethod
    def _windows_close_handle(handle: int) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(handle))

    @staticmethod
    def _windows_handle_identity(handle: int) -> tuple[int, int]:
        import ctypes
        from ctypes import wintypes

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("file_attributes", wintypes.DWORD),
                ("creation_time", wintypes.FILETIME),
                ("last_access_time", wintypes.FILETIME),
                ("last_write_time", wintypes.FILETIME),
                ("volume_serial_number", wintypes.DWORD),
                ("file_size_high", wintypes.DWORD),
                ("file_size_low", wintypes.DWORD),
                ("number_of_links", wintypes.DWORD),
                ("file_index_high", wintypes.DWORD),
                ("file_index_low", wintypes.DWORD),
            ]

        information = _ByHandleFileInformation()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        ]
        get_information.restype = wintypes.BOOL
        succeeded = get_information(
            wintypes.HANDLE(handle),
            ctypes.byref(information),
        )
        if not succeeded:
            error = ctypes.get_last_error()  # type: ignore[attr-defined]
            raise ctypes.WinError(error)  # type: ignore[attr-defined]
        file_index = (int(information.file_index_high) << 32) | int(information.file_index_low)
        if file_index <= 0:
            raise OSError("The directory handle has no reliable identity.")
        return int(information.volume_serial_number), file_index

    @staticmethod
    def _windows_final_path(handle: int) -> Path:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        get_final_path.restype = wintypes.DWORD
        required = get_final_path(wintypes.HANDLE(handle), None, 0, 0)
        if required <= 0:
            error = ctypes.get_last_error()  # type: ignore[attr-defined]
            raise ctypes.WinError(error)  # type: ignore[attr-defined]
        buffer = ctypes.create_unicode_buffer(required + 1)
        written = get_final_path(
            wintypes.HANDLE(handle),
            buffer,
            len(buffer),
            0,
        )
        if written <= 0 or written >= len(buffer):
            error = ctypes.get_last_error()  # type: ignore[attr-defined]
            raise ctypes.WinError(error)  # type: ignore[attr-defined]
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)

    @classmethod
    def _capture_root_identity(cls, path: Path) -> tuple[int, int] | None:
        if os.name == "nt":
            handle: int | None = None
            try:
                handle = cls._windows_open_directory_handle(path)
                return cls._windows_handle_identity(handle)
            except (OSError, RuntimeError, ValueError):
                return None
            finally:
                if handle is not None:
                    cls._windows_close_handle(handle)
        try:
            value = path.stat()
        except (OSError, RuntimeError, ValueError):
            return None
        if not path.is_dir() or value.st_ino <= 0:
            return None
        return value.st_dev, value.st_ino

    @staticmethod
    def _path_within(candidate: Path, root: Path) -> bool:
        try:
            common = os.path.commonpath((str(root), str(candidate)))
        except ValueError:
            return False
        return os.path.normcase(common) == os.path.normcase(str(root))

    def _root_relative(self, selected: Path) -> tuple[_AllowedRoot, Path]:
        for root in sorted(
            self._allowed_roots,
            key=lambda item: len(item.path.parts),
            reverse=True,
        ):
            try:
                relative_text = os.path.relpath(selected, root.path)
            except ValueError:
                continue
            relative = Path(relative_text)
            if relative == Path() or relative.is_absolute() or ".." in relative.parts:
                continue
            if self._path_within(selected, root.path):
                return root, relative
        raise self._unreadable_error()

    @staticmethod
    def _directory_flags() -> int:
        required = ("O_DIRECTORY", "O_NOFOLLOW")
        if os.open not in os.supports_dir_fd or any(not hasattr(os, name) for name in required):
            raise OSError("Descriptor-relative directory traversal is unavailable.")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return flags

    @staticmethod
    def _file_flags() -> int:
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK", "O_BINARY", "O_NOINHERIT"):
            if hasattr(os, name):
                flags |= int(getattr(os, name))
        return flags

    def _open_posix_parent(
        self,
        root: _AllowedRoot,
        relative: Path,
        *,
        missing_ok: bool,
    ) -> list[int] | None:
        if root.identity is None:
            raise self._unreadable_error()
        try:
            flags = self._directory_flags()
            root_descriptor = os.open(root.path, flags)
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._changed_error(error_type=type(exc).__name__) from exc
        descriptors = [root_descriptor]
        try:
            opened_identity = os.fstat(root_descriptor)
            if (opened_identity.st_dev, opened_identity.st_ino) != root.identity:
                raise self._changed_error()
            for part in relative.parts[:-1]:
                try:
                    descriptor = os.open(part, flags, dir_fd=descriptors[-1])
                except FileNotFoundError:
                    if missing_ok:
                        for opened in reversed(descriptors):
                            os.close(opened)
                        return None
                    raise
                descriptors.append(descriptor)
            return descriptors
        except FileIngressError:
            for opened in reversed(descriptors):
                os.close(opened)
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            for opened in reversed(descriptors):
                os.close(opened)
            code = getattr(exc, "errno", None)
            if code in {errno.ELOOP, errno.ENOTDIR}:
                raise self._changed_error(error_type=type(exc).__name__) from exc
            raise self._unreadable_error(error_type=type(exc).__name__) from exc

    def _open_windows_parent(
        self,
        root: _AllowedRoot,
        relative: Path,
        *,
        missing_ok: bool,
    ) -> tuple[list[int], Path] | None:
        if root.identity is None:
            raise self._unreadable_error()
        try:
            root_handle = self._windows_open_directory_handle(root.path)
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._changed_error(error_type=type(exc).__name__) from exc
        handles = [root_handle]
        try:
            if self._windows_handle_identity(root_handle) != root.identity:
                raise self._changed_error()
            root_final = self._windows_final_path(root_handle)
            current = root.path
            for part in relative.parts[:-1]:
                current /= part
                try:
                    handle = self._windows_open_directory_handle(current)
                except FileNotFoundError:
                    if missing_ok:
                        for opened in reversed(handles):
                            self._windows_close_handle(opened)
                        return None
                    raise
                final = self._windows_final_path(handle)
                if not self._path_within(final, root_final):
                    self._windows_close_handle(handle)
                    raise self._changed_error()
                handles.append(handle)
            return handles, root_final
        except FileIngressError:
            for opened in reversed(handles):
                self._windows_close_handle(opened)
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            for opened in reversed(handles):
                self._windows_close_handle(opened)
            raise self._unreadable_error(error_type=type(exc).__name__) from exc

    def _open_bound_raw(
        self,
        path: str | Path,
        *,
        missing_ok: bool = False,
    ) -> int | None:
        selected = self.ingress.normalize_path(path, FileKind.ROOTSMAGIC, absolute=True)
        root, relative = self._root_relative(selected)
        if os.name == "nt":
            parent = self._open_windows_parent(root, relative, missing_ok=missing_ok)
            if parent is None:
                return None
            handles, root_final = parent
            descriptor: int | None = None
            try:
                try:
                    descriptor = os.open(selected, self._file_flags())
                except FileNotFoundError:
                    if missing_ok:
                        return None
                    raise
                import msvcrt

                final = self._windows_final_path(
                    msvcrt.get_osfhandle(descriptor)  # type: ignore[attr-defined]
                )
                if not self._path_within(final, root_final):
                    raise self._changed_error()
                return descriptor
            except FileIngressError:
                if descriptor is not None:
                    os.close(descriptor)
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                if descriptor is not None:
                    os.close(descriptor)
                raise self._unreadable_error(error_type=type(exc).__name__) from exc
            finally:
                for handle in reversed(handles):
                    self._windows_close_handle(handle)

        parents = self._open_posix_parent(root, relative, missing_ok=missing_ok)
        if parents is None:
            return None
        try:
            try:
                return os.open(relative.parts[-1], self._file_flags(), dir_fd=parents[-1])
            except FileNotFoundError:
                if missing_ok:
                    return None
                raise
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise self._changed_error(error_type=type(exc).__name__) from exc
                raise
        except FileIngressError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._unreadable_error(error_type=type(exc).__name__) from exc
        finally:
            for descriptor in reversed(parents):
                os.close(descriptor)

    def _bound_lstat(self, path: str | Path) -> os.stat_result | None:
        selected = self.ingress.normalize_path(path, FileKind.ROOTSMAGIC, absolute=True)
        root, relative = self._root_relative(selected)
        if os.name == "nt":
            parent = self._open_windows_parent(root, relative, missing_ok=True)
            if parent is None:
                return None
            handles, _root_final = parent
            try:
                try:
                    return os.lstat(selected)
                except FileNotFoundError:
                    return None
                except (OSError, RuntimeError, ValueError) as exc:
                    raise self._unreadable_error(error_type=type(exc).__name__) from exc
            finally:
                for handle in reversed(handles):
                    self._windows_close_handle(handle)

        parents = self._open_posix_parent(root, relative, missing_ok=True)
        if parents is None:
            return None
        try:
            try:
                return os.stat(
                    relative.parts[-1],
                    dir_fd=parents[-1],
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None
            except (OSError, RuntimeError, ValueError) as exc:
                raise self._unreadable_error(error_type=type(exc).__name__) from exc
        finally:
            for descriptor in reversed(parents):
                os.close(descriptor)

    def _open_bound_descriptor(
        self,
        path: str | Path,
        *,
        expected: FileSnapshot | None = None,
        missing_ok: bool = False,
    ) -> tuple[int, FileSnapshot] | None:
        descriptor = self._open_bound_raw(path, missing_ok=missing_ok)
        if descriptor is None:
            return None
        try:
            snapshot = self.ingress._validate_stat(
                os.fstat(descriptor),
                FileKind.ROOTSMAGIC,
            )
            if expected is not None and snapshot != expected:
                raise self._changed_error()
            prefix = os.read(
                descriptor,
                min(16, self.ingress.limit(FileKind.ROOTSMAGIC).max_bytes),
            )
            os.lseek(descriptor, 0, os.SEEK_SET)
            if any(prefix.startswith(signature) for signature in _ARCHIVE_SIGNATURES):
                raise self._bound_error(
                    "FILE_ARCHIVE_UNSUPPORTED",
                    "Compressed or archived rootsmagic input is not supported.",
                )
            if not prefix.startswith(b"SQLite format 3\x00"):
                raise self._bound_error(
                    "FILE_FORMAT_INVALID",
                    "The rootsmagic input is not a supported SQLite database.",
                )
            return descriptor, snapshot
        except FileIngressError:
            os.close(descriptor)
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            os.close(descriptor)
            raise self._bound_error(
                "FILE_INPUT_IO",
                "The rootsmagic input could not be read completely.",
                error_type=type(exc).__name__,
            ) from exc

    def _inspect_bound(
        self,
        path: str | Path,
        *,
        missing_ok: bool = False,
    ) -> FileSnapshot | None:
        opened = self._open_bound_descriptor(path, missing_ok=missing_ok)
        if opened is None:
            return None
        descriptor, snapshot = opened
        os.close(descriptor)
        return snapshot

    @contextmanager
    def operation(
        self,
        path: Path,
        expected: SourceFingerprint | None = None,
    ) -> Iterator[dict[str, tuple[str, ...]]]:
        """Bind schema, row preflight, and reads to one immutable SQLite snapshot."""

        selected = self.ingress.normalize_path(path, FileKind.ROOTSMAGIC, absolute=True)
        fingerprint = expected or self.fingerprint_source(selected)
        try:
            with self.connection(selected, fingerprint) as connection:
                inspection = self._inspect_schema_from_connection(connection)
                schema = inspection.columns
                connection_token = self._operation_connection.set(connection)
                path_token = self._operation_path.set(selected)
                fingerprint_token = self._operation_fingerprint.set(fingerprint)
                schema_token = self._operation_schema.set(schema)
                declared_types_token = self._operation_declared_types.set(inspection.declared_types)
                try:
                    self.validate_row_limits(selected, schema, fingerprint.snapshot)
                    self.verify_source(selected, fingerprint)
                    yield schema
                    self.verify_source(selected, fingerprint)
                finally:
                    self._operation_declared_types.reset(declared_types_token)
                    self._operation_schema.reset(schema_token)
                    self._operation_fingerprint.reset(fingerprint_token)
                    self._operation_path.reset(path_token)
                    self._operation_connection.reset(connection_token)
        except AncestryError:
            raise
        except sqlite3.Error as exc:
            raise AncestryError(
                "ROOTSMAGIC_INPUT_INVALID",
                "The RootsMagic input could not be inspected as a SQLite database.",
                exit_code=2,
                details={"error_type": type(exc).__name__},
            ) from exc

    def list_trees(self) -> list[Path]:
        """Return immutable RootsMagic trees found beneath allowed directories."""
        results: set[Path] = set()
        for root in self._allowed_roots:
            directory = root.path
            if directory.is_dir():
                for path in directory.glob("*.rmtree"):
                    try:
                        self._inspect_bound(path)
                    except AncestryError as exc:
                        if exc.code == "FILE_INPUT_UNREADABLE" and not path.exists():
                            continue
                        raise
                    results.add(path.absolute())
                    self.ingress.validate_record(
                        FileKind.ROOTSMAGIC,
                        count=1,
                        byte_count=0,
                        nesting=0,
                        collection_items=len(results),
                    )
        return sorted(results)

    def _within_allowed_directory(self, candidate: Path) -> bool:
        for directory in self.allowed_directories:
            try:
                if os.path.commonpath((str(directory), str(candidate))) == str(directory):
                    return True
            except ValueError:
                continue
        return False

    def resolve_tree(self, name_or_path: str | Path) -> Path:
        """Resolve a RootsMagic tree within the configured read-only roots."""
        requested = self.ingress.normalize_path(name_or_path, FileKind.ROOTSMAGIC)
        candidates: list[Path]
        explicit_path = requested.is_absolute()
        if explicit_path:
            candidates = [requested]
        else:
            name = requested if requested.suffix == ".rmtree" else requested.with_suffix(".rmtree")
            candidates = [directory / name for directory in self.allowed_directories]
        for candidate in candidates:
            resolved = self.ingress.normalize_path(
                candidate,
                FileKind.ROOTSMAGIC,
                resolve=True,
            )
            if not self._within_allowed_directory(resolved):
                continue
            if resolved.suffix.casefold() != ".rmtree":
                continue
            inspected = self._inspect_bound(
                resolved,
                missing_ok=not explicit_path,
            )
            if inspected is not None:
                return resolved
        raise AncestryError(
            "ROOTSMAGIC_TREE_NOT_FOUND",
            "No configured RootsMagic database matches the requested input.",
            "Add its parent directory to config.toml and try again.",
        )

    @staticmethod
    def _authorizer(
        action: int, first: str | None, second: str | None, _db: str | None, _source: str | None
    ) -> int:
        if action in DENIED_ACTIONS:
            return sqlite3.SQLITE_DENY
        if (
            action == sqlite3.SQLITE_FUNCTION
            and (second or first or "").casefold() == "load_extension"
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @staticmethod
    def _sidecars(path: Path) -> tuple[Path, Path, Path]:
        return (
            Path(f"{path}-wal"),
            Path(f"{path}-shm"),
            Path(f"{path}-journal"),
        )

    def _sidecar_error(self) -> AncestryError:
        return AncestryError(
            "ROOTSMAGIC_WAL_ACTIVE",
            "The RootsMagic database has an unsafe SQLite transaction sidecar.",
            "Close RootsMagic or create a stable backup, then retry.",
            exit_code=2,
        )

    def _assert_no_unsafe_sidecars(self, path: Path) -> None:
        wal, shm, journal = self._sidecars(path)
        if self._bound_lstat(journal) is not None or (
            self._bound_lstat(shm) is not None and self._bound_lstat(wal) is None
        ):
            raise self._sidecar_error()

    def _fingerprint_auxiliary_bound(
        self,
        path: Path,
        expected: FileSnapshot | None = None,
        *,
        missing_ok: bool = False,
    ) -> FileFingerprint | None:
        descriptor = self._open_bound_raw(path, missing_ok=missing_ok)
        if descriptor is None:
            return None
        digest = hashlib.sha256()
        try:
            snapshot = self.ingress._validate_stat(
                os.fstat(descriptor),
                FileKind.ROOTSMAGIC,
            )
            if expected is not None and snapshot != expected:
                raise self._changed_error()
            while True:
                cancellation_checkpoint()
                chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
            cancellation_checkpoint()
            current = self.ingress._validate_stat(
                os.fstat(descriptor),
                FileKind.ROOTSMAGIC,
            )
            if current != snapshot or (expected is not None and current != expected):
                raise self._changed_error()
        except FileIngressError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._bound_error(
                "FILE_INPUT_IO",
                "The rootsmagic input could not be read completely.",
                error_type=type(exc).__name__,
            ) from exc
        finally:
            os.close(descriptor)
        return FileFingerprint(snapshot=snapshot, sha256=digest.hexdigest())

    def _copy_auxiliary_bound_to(
        self,
        path: Path,
        destination: Path,
        expected: FileFingerprint,
    ) -> None:
        descriptor = self._open_bound_raw(path)
        assert descriptor is not None
        digest = hashlib.sha256()
        try:
            snapshot = self.ingress._validate_stat(
                os.fstat(descriptor),
                FileKind.ROOTSMAGIC,
            )
            if snapshot != expected.snapshot:
                raise self._changed_error()
            with destination.open("xb", buffering=0) as output:
                try:
                    while True:
                        cancellation_checkpoint()
                        chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        digest.update(chunk)
                        remaining = memoryview(chunk)
                        while remaining:
                            cancellation_checkpoint()
                            written = output.write(remaining)
                            if written is None or written <= 0:
                                raise OSError("The destination write made no progress.")
                            remaining = remaining[written:]
                    output.flush()
                    os.fsync(output.fileno())
                    cancellation_checkpoint()
                    current = self.ingress._validate_stat(
                        os.fstat(descriptor),
                        FileKind.ROOTSMAGIC,
                    )
                    if (
                        current != snapshot
                        or current != expected.snapshot
                        or digest.hexdigest() != expected.sha256
                    ):
                        raise self._changed_error()
                except BaseException:
                    cleanup_open_path(destination, output.fileno())
                    raise
        except FileIngressError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._bound_error(
                "FILE_INPUT_IO",
                "The rootsmagic input could not be copied safely.",
                error_type=type(exc).__name__,
            ) from exc
        except CancellationError:
            raise
        finally:
            os.close(descriptor)

    def _consolidate_wal_snapshot(self, path: Path, destination: Path) -> None:
        """Recover a verified WAL pair only inside the process-owned directory."""

        connection: sqlite3.Connection | None = None
        output: sqlite3.Connection | None = None
        primary_error: BaseException | None = None
        try:
            uri = f"{path.resolve().as_uri()}?mode=rw"
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=min(self.timeout_seconds, 30.0),
            )
            connection.enable_load_extension(False)
            connection.execute("PRAGMA trusted_schema = OFF")
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
            if journal_mode is None or str(journal_mode[0]).casefold() != "wal":
                raise self._sidecar_error()
            checkpoint = connection.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
            if (
                checkpoint is None
                or len(checkpoint) != 3
                or int(checkpoint[0]) != 0
                or int(checkpoint[1]) <= 0
                or int(checkpoint[2]) != int(checkpoint[1])
            ):
                raise self._sidecar_error()
            cancellation_checkpoint()
            output = sqlite3.connect(destination)

            def check_backup(_status: int, _remaining: int, _total: int) -> None:
                cancellation_checkpoint()

            connection.backup(output, pages=256, progress=check_backup)
            output.execute("PRAGMA trusted_schema = OFF")
            integrity = output.execute("PRAGMA quick_check").fetchone()
            if integrity != ("ok",):
                raise self._sidecar_error()
            output.commit()
        except AncestryError as exc:
            primary_error = exc
            raise
        except CancellationError as exc:
            primary_error = exc
            raise
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
            primary_error = exc
            raise AncestryError(
                "ROOTSMAGIC_WAL_ACTIVE",
                "The RootsMagic database has an unsafe SQLite transaction sidecar.",
                "Close RootsMagic or create a stable backup, then retry.",
                exit_code=2,
                details={"error_type": type(exc).__name__},
            ) from exc
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanup_error: BaseException | None = None
            if output is not None:
                try:
                    output.close()
                except BaseException as exc:  # noqa: BLE001 - preserve the primary error
                    cleanup_error = exc
            if connection is not None:
                try:
                    connection.close()
                except BaseException as exc:  # noqa: BLE001 - finish ordered cleanup
                    cleanup_error = cleanup_error or exc
            if primary_error is None and cleanup_error is not None:
                raise cleanup_error

    def _fingerprint_bound(
        self,
        path: Path,
        expected: FileSnapshot | None = None,
    ) -> FileFingerprint:
        opened = self._open_bound_descriptor(path, expected=expected)
        assert opened is not None
        descriptor, snapshot = opened
        digest = hashlib.sha256()
        try:
            while True:
                cancellation_checkpoint()
                chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
            cancellation_checkpoint()
            current = self.ingress._validate_stat(
                os.fstat(descriptor),
                FileKind.ROOTSMAGIC,
            )
            if current != snapshot or (expected is not None and current != expected):
                raise self._changed_error()
        except FileIngressError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._bound_error(
                "FILE_INPUT_IO",
                "The rootsmagic input could not be read completely.",
                error_type=type(exc).__name__,
            ) from exc
        finally:
            os.close(descriptor)
        return FileFingerprint(snapshot=snapshot, sha256=digest.hexdigest())

    def _copy_bound_to(
        self,
        path: Path,
        destination: Path,
        expected: FileFingerprint,
    ) -> None:
        opened = self._open_bound_descriptor(path, expected=expected.snapshot)
        assert opened is not None
        descriptor, snapshot = opened
        digest = hashlib.sha256()
        try:
            with destination.open("xb", buffering=0) as output:
                try:
                    while True:
                        cancellation_checkpoint()
                        chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        cancellation_checkpoint()
                        digest.update(chunk)
                        remaining = memoryview(chunk)
                        while remaining:
                            cancellation_checkpoint()
                            written = output.write(remaining)
                            if written is None or written <= 0:
                                raise OSError("The destination write made no progress.")
                            remaining = remaining[written:]
                    output.flush()
                    os.fsync(output.fileno())
                    cancellation_checkpoint()
                    current = self.ingress._validate_stat(
                        os.fstat(descriptor),
                        FileKind.ROOTSMAGIC,
                    )
                    if (
                        current != snapshot
                        or current != expected.snapshot
                        or digest.hexdigest() != expected.sha256
                    ):
                        raise self._changed_error()
                except BaseException:
                    cleanup_open_path(destination, output.fileno())
                    raise
        except FileIngressError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._bound_error(
                "FILE_INPUT_IO",
                "The rootsmagic input could not be copied safely.",
                error_type=type(exc).__name__,
            ) from exc
        except CancellationError:
            raise
        finally:
            os.close(descriptor)

    def verify_source(self, path: Path, expected: SourceFingerprint) -> None:
        """Require every source component to remain byte-for-byte stable."""

        wal_path, shm_path, journal_path = self._sidecars(path)
        if self._bound_lstat(journal_path) is not None:
            raise self._changed_error()
        current = self._fingerprint_bound(path, expected.snapshot)
        if current.sha256 != expected.sha256:
            raise self._changed_error()
        if isinstance(expected, FileFingerprint):
            if (
                self._bound_lstat(wal_path) is not None
                or self._bound_lstat(shm_path) is not None
                or self._bound_lstat(journal_path) is not None
            ):
                raise self._changed_error()
            return
        current_wal = self._fingerprint_auxiliary_bound(
            wal_path,
            expected.wal.snapshot,
            missing_ok=True,
        )
        if current_wal is None or current_wal.sha256 != expected.wal.sha256:
            raise self._changed_error()
        if expected.shm is None:
            if self._bound_lstat(shm_path) is not None:
                raise self._changed_error()
        else:
            current_shm = self._fingerprint_auxiliary_bound(
                shm_path,
                expected.shm.snapshot,
                missing_ok=True,
            )
            if current_shm is None or current_shm.sha256 != expected.shm.sha256:
                raise self._changed_error()
        if self._bound_lstat(journal_path) is not None:
            raise self._changed_error()

    def fingerprint_source(
        self,
        path: Path,
        expected: FileSnapshot | None = None,
    ) -> SourceFingerprint:
        """Hash one stable main file or main/WAL generation."""

        selected = self.ingress.normalize_path(path, FileKind.ROOTSMAGIC, absolute=True)
        self._assert_no_unsafe_sidecars(selected)
        wal_path, shm_path, journal_path = self._sidecars(selected)
        main_fingerprint = self._fingerprint_bound(selected, expected)
        fingerprint: SourceFingerprint = main_fingerprint
        wal = self._fingerprint_auxiliary_bound(wal_path, missing_ok=True)
        if wal is not None:
            shm = self._fingerprint_auxiliary_bound(shm_path, missing_ok=True)
            fingerprint = _WalFingerprint(main=main_fingerprint, wal=wal, shm=shm)
        elif self._bound_lstat(shm_path) is not None:
            raise self._sidecar_error()
        if self._bound_lstat(journal_path) is not None:
            raise self._sidecar_error()
        self.verify_source(selected, fingerprint)
        return fingerprint

    @staticmethod
    def snapshot_provenance(fingerprint: SourceFingerprint) -> str:
        """Describe the verified snapshot mode without disclosing sidecar metadata."""

        if isinstance(fingerprint, _WalFingerprint):
            return "verified main database plus WAL generation"
        return "verified standalone database"

    @staticmethod
    def _same_path(left: Path | None, right: Path) -> bool:
        return left is not None and left.absolute() == right.absolute()

    @contextmanager
    def connection(
        self,
        path: Path,
        expected: FileSnapshot | SourceFingerprint | None = None,
    ) -> Iterator[sqlite3.Connection]:
        """Open a read-only connection to the immutable RootsMagic database copy."""
        selected = self.ingress.normalize_path(path, FileKind.ROOTSMAGIC, absolute=True)
        active = self._operation_connection.get()
        if active is not None and self._same_path(self._operation_path.get(), selected):
            yield active
            return
        if isinstance(expected, (FileFingerprint, _WalFingerprint)):
            fingerprint = expected
            self.verify_source(selected, fingerprint)
        else:
            fingerprint = self.fingerprint_source(selected, expected)
        with tempfile.TemporaryDirectory(prefix="ancestry-rootsmagic-") as temporary:
            copied_path = Path(temporary) / selected.name
            snapshot_path = copied_path
            self._copy_bound_to(
                selected,
                copied_path,
                fingerprint.main if isinstance(fingerprint, _WalFingerprint) else fingerprint,
            )
            if isinstance(fingerprint, _WalFingerprint):
                self._copy_auxiliary_bound_to(
                    Path(f"{selected}-wal"),
                    Path(f"{copied_path}-wal"),
                    fingerprint.wal,
                )
            self.verify_source(selected, fingerprint)
            if isinstance(fingerprint, _WalFingerprint):
                snapshot_path = Path(temporary) / "consolidated.rmtree"
                self._consolidate_wal_snapshot(copied_path, snapshot_path)
                self.verify_source(selected, fingerprint)
            uri = f"{snapshot_path.resolve().as_uri()}?mode=ro&immutable=1"
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=min(self.timeout_seconds, 30.0),
            )
            interruption_reason: str | None = None
            progress_interrupted = False
            interruption_lock = threading.Lock()
            progress_handler_installed = False
            unsubscribe_cancellation: Callable[[], None] | None = None
            operation_error: BaseException | None = None
            try:
                record_limit = self.ingress.limit(FileKind.ROOTSMAGIC).max_record_bytes
                if (
                    record_limit is not None
                    and hasattr(connection, "setlimit")
                    and hasattr(sqlite3, "SQLITE_LIMIT_LENGTH")
                ):
                    column_limit = _SQLITE_MAX_COLUMNS
                    getlimit = getattr(connection, "getlimit", None)
                    if callable(getlimit) and hasattr(sqlite3, "SQLITE_LIMIT_COLUMN"):
                        column_limit = max(
                            0,
                            getlimit(sqlite3.SQLITE_LIMIT_COLUMN),
                        )
                    # A SQLite record header has one serial-type varint per
                    # result column plus its own size varint. Python enforces
                    # the exact public payload budget as each row arrives.
                    framing_headroom = _SQLITE_VARINT_MAX_BYTES * (column_limit + 1)
                    sqlite_length_limit = max(
                        record_limit + framing_headroom,
                        4_096,
                    )
                    if callable(getlimit):
                        sqlite_length_limit = min(
                            sqlite_length_limit,
                            getlimit(sqlite3.SQLITE_LIMIT_LENGTH),
                        )
                    connection.setlimit(
                        sqlite3.SQLITE_LIMIT_LENGTH,
                        sqlite_length_limit,
                    )
                connection.execute("PRAGMA query_only = ON")
                connection.execute("PRAGMA trusted_schema = OFF")
                connection.execute("BEGIN")
                connection.enable_load_extension(False)
                connection.set_authorizer(self._authorizer)
                deadline = time.monotonic() + self.timeout_seconds
                token = current_cancellation_token()

                def record_interruption(reason: str) -> str:
                    nonlocal interruption_reason
                    with interruption_lock:
                        if interruption_reason is None:
                            interruption_reason = reason
                        return interruption_reason

                if token is not None:

                    def note_cancellation(_state: object) -> None:
                        record_interruption(
                            "timeout" if time.monotonic() > deadline else "cancelled"
                        )

                    unsubscribe_cancellation = token.subscribe(note_cancellation)

                def interrupt_when_required() -> int:
                    nonlocal progress_interrupted
                    with interruption_lock:
                        if interruption_reason is not None:
                            progress_interrupted = True
                            return 1
                    if time.monotonic() > deadline:
                        record_interruption("timeout")
                        with interruption_lock:
                            progress_interrupted = True
                        return 1
                    if token is not None and token.requested:
                        record_interruption("cancelled")
                        with interruption_lock:
                            progress_interrupted = True
                        return 1
                    return 0

                connection.set_progress_handler(interrupt_when_required, 1_000)
                progress_handler_installed = True
                yield connection
            except sqlite3.Error as exc:
                operation_error = exc
                with interruption_lock:
                    reason = interruption_reason if progress_interrupted else None
                if reason == "timeout":
                    raise AncestryError(
                        "ROOTSMAGIC_QUERY_TIMEOUT",
                        "The read-only RootsMagic operation exceeded its configured timeout.",
                        "Reduce the query scope or increase query_timeout_seconds within the "
                        "documented limit.",
                        details={"timeout_seconds": self.timeout_seconds},
                    ) from exc
                if reason == "cancelled":
                    cancellation_checkpoint()
                raise
            except BaseException as exc:
                operation_error = exc
                raise
            finally:
                cleanup_error: BaseException | None = None
                try:
                    if unsubscribe_cancellation is not None:
                        unsubscribe_cancellation()
                except BaseException as exc:  # noqa: BLE001 - preserve the primary operation error
                    cleanup_error = exc
                try:
                    if progress_handler_installed:
                        connection.set_progress_handler(None, 0)
                except BaseException as exc:  # noqa: BLE001 - continue ordered cleanup
                    cleanup_error = cleanup_error or exc
                try:
                    connection.close()
                except BaseException as exc:  # noqa: BLE001 - verification must still run
                    cleanup_error = cleanup_error or exc
                try:
                    self.verify_source(selected, fingerprint)
                except BaseException as exc:  # noqa: BLE001 - preserve the primary operation error
                    cleanup_error = cleanup_error or exc
                if operation_error is None and cleanup_error is not None:
                    raise cleanup_error

    def schema(
        self,
        path: Path,
        expected: FileSnapshot | None = None,
    ) -> dict[str, tuple[str, ...]]:
        """Inspect the RootsMagic schema through the read-only source connection."""
        try:
            with self.connection(path, expected) as connection:
                return self._schema_from_connection(connection)
        except AncestryError:
            raise
        except sqlite3.Error as exc:
            self._raise_if_sqlite_length_limit(exc)
            raise AncestryError(
                "ROOTSMAGIC_INPUT_INVALID",
                "The RootsMagic input could not be inspected as a SQLite database.",
                details={"error_type": type(exc).__name__},
            ) from exc

    def inspect_schema(
        self,
        path: Path,
        expected: FileSnapshot | None = None,
    ) -> DatabaseSchema:
        """Return the immutable source schema as deterministic DTOs."""

        try:
            with self.connection(path, expected) as connection:
                return self._inspect_schema_from_connection(connection).snapshot()
        except AncestryError:
            raise
        except sqlite3.Error as exc:
            self._raise_if_sqlite_length_limit(exc)
            raise AncestryError(
                "ROOTSMAGIC_INPUT_INVALID",
                "The RootsMagic input could not be inspected as a SQLite database.",
                details={"error_type": type(exc).__name__},
            ) from exc

    def _schema_from_connection(
        self,
        connection: sqlite3.Connection,
    ) -> dict[str, tuple[str, ...]]:
        return self._inspect_schema_from_connection(connection).columns

    def _inspect_schema_from_connection(
        self,
        connection: sqlite3.Connection,
    ) -> _SchemaInspection:
        cursor = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        result: dict[str, tuple[str, ...]] = {}
        declared_types: dict[str, tuple[tuple[str, str], ...]] = {}
        # PRAGMA is denied after the authorizer is installed, so parse declared
        # CREATE TABLE SQL.
        table_count = 0
        while True:
            cancellation_checkpoint()
            row = cursor.fetchone()
            if row is None:
                break
            table_count += 1
            self.ingress.validate_record(
                FileKind.ROOTSMAGIC,
                count=1,
                byte_count=0,
                nesting=0,
                collection_items=table_count,
            )
            table_name, create_sql = row
            try:
                parsed = parse(str(create_sql), read="sqlite")[0]
                if parsed is None:
                    raise ValueError("empty CREATE TABLE expression")
                definitions = tuple(
                    column
                    for column in parsed.find_all(exp.ColumnDef)
                    if getattr(column.this, "name", None)
                )
                columns = tuple(column.this.name for column in definitions)
                column_types = tuple(
                    (
                        column.this.name,
                        column.kind.sql(dialect="sqlite").upper() if column.kind else "",
                    )
                    for column in definitions
                )
            except Exception:  # noqa: BLE001 - vendor schemas can be unusual
                columns = ()
                column_types = ()
            self.ingress.validate_record(
                FileKind.ROOTSMAGIC,
                count=1,
                byte_count=0,
                nesting=0,
                collection_items=len(columns),
            )
            result[str(table_name)] = columns
            declared_types[str(table_name)] = column_types
        return _SchemaInspection(result, declared_types)

    def declared_column_types(self, path: Path, table_name: str) -> dict[str, str]:
        """Return parsed declared types for a table in the active snapshot."""

        selected = self.ingress.normalize_path(path, FileKind.ROOTSMAGIC, absolute=True)
        active = self._operation_connection.get()
        declared_types = self._operation_declared_types.get()
        if (
            active is not None
            and self._same_path(self._operation_path.get(), selected)
            and declared_types is not None
        ):
            actual = next(
                (name for name in declared_types if name.casefold() == table_name.casefold()),
                None,
            )
            return dict(declared_types.get(actual, ())) if actual is not None else {}

        fingerprint = self.fingerprint_source(selected)
        with self.connection(selected, fingerprint) as connection:
            inspection = self._inspect_schema_from_connection(connection)
        self.verify_source(selected, fingerprint)
        actual = next(
            (
                name
                for name in inspection.declared_types
                if name.casefold() == table_name.casefold()
            ),
            None,
        )
        return dict(inspection.declared_types.get(actual, ())) if actual is not None else {}

    @staticmethod
    def _row_byte_count(values: tuple[Any, ...]) -> int:
        """Return a content-size budget without serializing or retaining values."""

        total = 0
        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                total += len(value.encode("utf-8"))
            elif isinstance(value, (bytes, bytearray, memoryview)):
                total += len(value)
            else:
                total += len(str(value).encode("utf-8"))
        return total

    @staticmethod
    def _is_sqlite_length_limit(exc: sqlite3.Error) -> bool:
        error_code = getattr(exc, "sqlite_errorcode", None)
        sqlite_too_big = getattr(sqlite3, "SQLITE_TOOBIG", None)
        return error_code == sqlite_too_big or "string or blob too big" in str(exc).casefold()

    def _raise_if_sqlite_length_limit(self, exc: sqlite3.Error) -> None:
        if not self._is_sqlite_length_limit(exc):
            return
        maximum = self.ingress.limit(FileKind.ROOTSMAGIC).max_record_bytes
        if maximum is None:
            return
        self.ingress.validate_record(
            FileKind.ROOTSMAGIC,
            count=1,
            byte_count=maximum + 1,
            nesting=0,
            collection_items=1,
        )

    def _validate_result_row(
        self,
        values: tuple[Any, ...],
        *,
        count: int,
        columns: tuple[str, ...] | None = None,
    ) -> None:
        if columns is not None:
            self.ingress.validate_structure(
                dict(zip(columns, values, strict=True)),
                FileKind.ROOTSMAGIC,
            )
        else:
            self.ingress.validate_structure(list(values), FileKind.ROOTSMAGIC)
        self.ingress.validate_record(
            FileKind.ROOTSMAGIC,
            count=count,
            byte_count=self._row_byte_count(values),
            nesting=0,
            collection_items=len(values),
        )

    def validate_sql(self, sql: str, allowed_schema: dict[str, tuple[str, ...]]) -> str:
        """Validate generated SQL against the read-only RootsMagic query policy."""
        cancellation_checkpoint()
        if not sql.strip() or "\x00" in sql:
            raise SecurityPolicyError("SQL_REJECTED", "The generated SQL is empty or malformed.")
        try:
            statements = parse(sql, read="sqlite")
        except Exception as exc:
            raise SecurityPolicyError(
                "SQL_REJECTED", "The generated SQL could not be parsed."
            ) from exc
        if len(statements) != 1:
            raise SecurityPolicyError("SQL_REJECTED", "Exactly one SQL statement is allowed.")
        cancellation_checkpoint()
        statement = statements[0]
        if not isinstance(statement, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise SecurityPolicyError("SQL_REJECTED", "Only SELECT or CTE queries are allowed.")
        for forbidden in FORBIDDEN_EXPRESSIONS:
            if statement.find(forbidden):
                raise SecurityPolicyError("SQL_REJECTED", "A forbidden SQL operation was detected.")
        allowed_tables = {name.casefold() for name in allowed_schema}
        referenced = {table.name.casefold() for table in statement.find_all(exp.Table)}
        if not referenced.issubset(allowed_tables):
            raise SecurityPolicyError(
                "SQL_TABLE_DENIED",
                "The query references a table outside the inspected RootsMagic schema.",
                details={"denied": sorted(referenced - allowed_tables)},
            )
        statement = statement.limit(self.max_rows + 1)
        return statement.sql(dialect="sqlite")

    def validate_row_limits(
        self,
        path: Path,
        schema: dict[str, tuple[str, ...]],
        expected: FileSnapshot | None = None,
    ) -> None:
        """Bound source rows before queries, exports, or provider schema use."""

        maximum = self.ingress.limit(FileKind.ROOTSMAGIC).max_records
        if maximum is None:
            return
        try:
            with self.connection(path, expected) as connection:
                self._validate_row_limits_connection(connection, schema)
        except AncestryError:
            raise
        except sqlite3.Error as exc:
            raise AncestryError(
                "ROOTSMAGIC_ROW_LIMIT_UNVERIFIED",
                "The RootsMagic row limit could not be verified safely.",
                details={"error_type": type(exc).__name__},
            ) from exc

    def _validate_row_limits_connection(
        self,
        connection: sqlite3.Connection,
        schema: dict[str, tuple[str, ...]],
    ) -> None:
        maximum = self.ingress.limit(FileKind.ROOTSMAGIC).max_records
        if maximum is None:
            return
        aggregate = 0
        for table_name in schema:
            cancellation_checkpoint()
            quoted = table_name.replace('"', '""')
            row = connection.execute(
                f'SELECT COUNT(*) FROM (SELECT 1 FROM "{quoted}" LIMIT ?)',  # noqa: S608
                (maximum + 1,),
            ).fetchone()
            count = int(row[0]) if row is not None else 0
            aggregate += count
            self.ingress.validate_record(
                FileKind.ROOTSMAGIC,
                count=count,
                byte_count=0,
                nesting=0,
                collection_items=1,
            )
            self.ingress.validate_record(
                FileKind.ROOTSMAGIC,
                count=aggregate,
                byte_count=0,
                nesting=0,
                collection_items=1,
            )

    def query(
        self,
        path: Path,
        sql: str,
        *,
        expected: SourceFingerprint | None = None,
        schema: dict[str, tuple[str, ...]] | None = None,
    ) -> QueryResult:
        """Execute a validated read-only query against the RootsMagic source."""
        selected = self.ingress.normalize_path(path, FileKind.ROOTSMAGIC, absolute=True)
        active = self._operation_connection.get()
        if active is None or not self._same_path(self._operation_path.get(), selected):
            fingerprint = expected or self.fingerprint_source(selected)
            with self.operation(selected, fingerprint) as bound_schema:
                return self.query(
                    selected,
                    sql,
                    expected=fingerprint,
                    schema=schema or bound_schema,
                )
        operation_fingerprint = expected or self._operation_fingerprint.get()
        if operation_fingerprint is None:
            raise AncestryError(
                "ROOTSMAGIC_SNAPSHOT_INVALID",
                "The RootsMagic operation snapshot is unavailable.",
            )
        query_schema = schema or self._operation_schema.get()
        if query_schema is None:
            raise AncestryError(
                "ROOTSMAGIC_SCHEMA_UNAVAILABLE",
                "The RootsMagic schema snapshot is unavailable.",
            )
        validated = self.validate_sql(sql, query_schema)
        rows: list[tuple[JsonValue, ...]] = []
        truncated = False
        try:
            cursor = active.execute(validated)
            columns = tuple(description[0] for description in cursor.description or ())
            while len(rows) <= self.max_rows:
                cancellation_checkpoint()
                row = cursor.fetchone()
                if row is None:
                    break
                values = tuple(row)
                self._validate_result_row(
                    values,
                    count=len(rows) + 1,
                    columns=columns,
                )
                if len(rows) == self.max_rows:
                    truncated = True
                    break
                rows.append(tuple(_json_safe_value(value) for value in values))
        except sqlite3.Error as exc:
            if "interrupted" in str(exc).casefold():
                raise
            if "not authorized" in str(exc).casefold():
                raise SecurityPolicyError(
                    "SQL_OPERATION_DENIED",
                    "SQLite blocked an operation forbidden by the read-only policy.",
                ) from exc
            self._raise_if_sqlite_length_limit(exc)
            raise AncestryError(
                "ROOTSMAGIC_QUERY_FAILED",
                "The read-only RootsMagic query failed.",
                details={"error_type": type(exc).__name__},
            ) from exc
        self.verify_source(selected, operation_fingerprint)
        return QueryResult(
            columns=columns,
            rows=tuple(rows),
            sql=validated,
            truncated=truncated,
            truncation=TruncationMetadata(
                row_limit=self.max_rows,
                returned_rows=len(rows),
                has_more=truncated,
            ),
        )

    def read_table(
        self,
        path: Path,
        table_name: str,
        expected: FileSnapshot | None = None,
        schema: dict[str, tuple[str, ...]] | None = None,
    ) -> list[dict[str, Any]]:
        """Read and validate normalized rows from one RootsMagic table."""
        selected = self.ingress.normalize_path(path, FileKind.ROOTSMAGIC, absolute=True)
        active = self._operation_connection.get()
        if active is None or not self._same_path(self._operation_path.get(), selected):
            fingerprint = self.fingerprint_source(selected, expected)
            with self.connection(selected, fingerprint) as connection:
                selected_schema = schema or self._schema_from_connection(connection)
                connection_token = self._operation_connection.set(connection)
                path_token = self._operation_path.set(selected)
                fingerprint_token = self._operation_fingerprint.set(fingerprint)
                schema_token = self._operation_schema.set(selected_schema)
                try:
                    return self.read_table(
                        selected,
                        table_name,
                        fingerprint.snapshot,
                        selected_schema,
                    )
                finally:
                    self._operation_schema.reset(schema_token)
                    self._operation_fingerprint.reset(fingerprint_token)
                    self._operation_path.reset(path_token)
                    self._operation_connection.reset(connection_token)
        active_schema = schema or self._operation_schema.get()
        if active_schema is None:
            raise AncestryError(
                "ROOTSMAGIC_SCHEMA_UNAVAILABLE",
                "The RootsMagic schema snapshot is unavailable.",
            )
        actual = next(
            (name for name in active_schema if name.casefold() == table_name.casefold()),
            None,
        )
        if actual is None:
            return []
        quoted = actual.replace('"', '""')
        try:
            active.row_factory = sqlite3.Row
            # The identifier is selected from the inspected schema and quoted above.
            cursor = active.execute(f'SELECT * FROM "{quoted}"')  # noqa: S608
            result: list[dict[str, Any]] = []
            while True:
                cancellation_checkpoint()
                row = cursor.fetchone()
                if row is None:
                    break
                values = tuple(row)
                columns = tuple(row.keys())
                self._validate_result_row(
                    values,
                    count=len(result) + 1,
                    columns=columns,
                )
                result.append(dict(zip(columns, values, strict=True)))
        except AncestryError:
            raise
        except sqlite3.Error as exc:
            if "interrupted" in str(exc).casefold():
                raise
            self._raise_if_sqlite_length_limit(exc)
            raise AncestryError(
                "ROOTSMAGIC_READ_FAILED",
                "The RootsMagic table could not be read safely.",
                details={"error_type": type(exc).__name__},
            ) from exc
        return result
