"""Bounded, typed, race-aware reads for every public file-ingress boundary."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import stat
import sys
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, fields
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Callable

from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.errors import ConfigurationError, FileIngressError
from ancestryllm.core.publication import cleanup_open_path

_ARCHIVE_SIGNATURES = (
    b"PK\x03\x04",  # ZIP
    b"\x1f\x8b",  # gzip
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
)
_READ_CHUNK_BYTES = 1024 * 1024
_MAX_READLINE_LIMIT = sys.maxsize - 2


def _reject_json_constant(_constant: str) -> object:
    raise ValueError("non-finite JSON constant")


class FileKind(str, Enum):
    """Supported public input classes."""

    CONFIG = "config"
    GEDCOM = "gedcom"
    ROOTSMAGIC = "rootsmagic"
    OCR = "ocr"
    MANIFEST = "manifest"
    JSON_SCHEMA = "json_schema"
    PROMPT_BODY = "prompt_body"


@dataclass(frozen=True, slots=True)
class FileLimit:
    """Resource budgets for one input class."""

    max_bytes: int
    max_line_bytes: int | None = None
    max_records: int | None = None
    max_record_bytes: int | None = None
    max_nesting: int | None = None
    max_collection_items: int | None = None


@dataclass(frozen=True, slots=True)
class FileIngressLimits:
    """All file limits, configurable only through ``config.toml``."""

    config: FileLimit = FileLimit(
        max_bytes=1_048_576,
        max_line_bytes=65_536,
        max_records=20_000,
        max_nesting=16,
        max_collection_items=20_000,
    )
    gedcom: FileLimit = FileLimit(
        max_bytes=536_870_912,
        max_line_bytes=1_048_576,
        max_records=5_000_000,
        max_record_bytes=16_777_216,
        max_nesting=99,
        max_collection_items=250_000,
    )
    rootsmagic: FileLimit = FileLimit(
        max_bytes=8_589_934_592,
        max_records=5_000_000,
        max_record_bytes=16_777_216,
        max_collection_items=50_000,
    )
    ocr: FileLimit = FileLimit(
        max_bytes=5_000_000,
        max_line_bytes=1_048_576,
        max_records=100_000,
    )
    manifest: FileLimit = FileLimit(
        max_bytes=33_554_432,
        max_line_bytes=1_048_576,
        max_records=500_000,
        max_nesting=64,
        max_collection_items=2_000_000,
    )
    json_schema: FileLimit = FileLimit(
        max_bytes=2_097_152,
        max_line_bytes=262_144,
        max_records=50_000,
        max_nesting=64,
        max_collection_items=100_000,
    )
    prompt_body: FileLimit = FileLimit(
        max_bytes=1_048_576,
        max_line_bytes=262_144,
        max_records=50_000,
    )

    @classmethod
    def from_mapping(cls, value: object) -> FileIngressLimits:
        """Parse strict per-kind overrides from the normal configuration boundary."""

        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ConfigurationError(
                "CONFIG_INVALID",
                "The file_ingress configuration must contain per-format tables.",
                exit_code=2,
            )
        defaults = cls()
        known_kinds = {item.name for item in fields(cls)}
        unknown_kinds = [key for key in value if key not in known_kinds]
        if unknown_kinds:
            raise ConfigurationError(
                "CONFIG_INVALID",
                "The file_ingress configuration contains an unknown input class.",
                exit_code=2,
                details={"unknown_count": len(unknown_kinds)},
            )
        selected: dict[str, FileLimit] = {}
        for kind in fields(cls):
            default = getattr(defaults, kind.name)
            raw = value.get(kind.name, {})
            if not isinstance(raw, Mapping):
                raise ConfigurationError(
                    "CONFIG_INVALID",
                    f"The {kind.name} file-ingress limits must be a table.",
                    exit_code=2,
                )
            known_limits = {item.name for item in fields(FileLimit)}
            unknown_limits = [key for key in raw if key not in known_limits]
            if unknown_limits:
                raise ConfigurationError(
                    "CONFIG_INVALID",
                    f"The {kind.name} file-ingress table contains an unknown limit.",
                    exit_code=2,
                    details={"unknown_count": len(unknown_limits)},
                )
            overrides: dict[str, int | None] = {}
            for limit_field in fields(FileLimit):
                item = raw.get(limit_field.name, getattr(default, limit_field.name))
                if item is not None and (
                    isinstance(item, bool) or not isinstance(item, int) or item < 1
                ):
                    raise ConfigurationError(
                        "CONFIG_INVALID",
                        f"The {kind.name}.{limit_field.name} file-ingress limit must be a positive integer.",
                        exit_code=2,
                    )
                if (
                    limit_field.name == "max_line_bytes"
                    and item is not None
                    and item > _MAX_READLINE_LIMIT
                ):
                    raise ConfigurationError(
                        "CONFIG_INVALID",
                        f"The {kind.name}.max_line_bytes file-ingress limit exceeds the platform-safe maximum.",
                        exit_code=2,
                    )
                overrides[limit_field.name] = item
            maximum_bytes = overrides["max_bytes"]
            assert isinstance(maximum_bytes, int)
            selected[kind.name] = FileLimit(
                max_bytes=maximum_bytes,
                max_line_bytes=overrides["max_line_bytes"],
                max_records=overrides["max_records"],
                max_record_bytes=overrides["max_record_bytes"],
                max_nesting=overrides["max_nesting"],
                max_collection_items=overrides["max_collection_items"],
            )
        return cls(**selected)

    def to_mapping(self) -> dict[str, dict[str, int]]:
        """Return TOML-serializable limits without null entries."""

        return {
            kind.name: {
                key: int(value)
                for key, value in asdict(getattr(self, kind.name)).items()
                if value is not None
            }
            for kind in fields(self)
        }


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """Identity captured before a file is consumed."""

    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    link_count: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> FileSnapshot:
        return cls(
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            value.st_nlink,
        )


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """One verified file identity and its content digest."""

    snapshot: FileSnapshot
    sha256: str


@dataclass(frozen=True, slots=True)
class TextLine:
    """One decoded physical line and its source-encoding byte length."""

    text: str
    byte_count: int


class _BoundedRawReader(io.RawIOBase):
    """Expose at most one configured source-byte budget to buffered decoders."""

    def __init__(
        self,
        source: BinaryIO,
        maximum: int,
        too_large: Callable[[], FileIngressError],
    ) -> None:
        super().__init__()
        self._source = source
        self._maximum = maximum
        self._too_large = too_large
        self._consumed = 0

    def readable(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._source.fileno()

    def _check_size(self) -> None:
        if os.fstat(self.fileno()).st_size > self._maximum:
            raise self._too_large()

    def readinto(self, buffer: Any) -> int:
        self._check_size()
        remaining = self._maximum - self._consumed
        if remaining <= 0:
            if self._source.read(1):
                raise self._too_large()
            return 0
        view = memoryview(buffer)[:remaining]
        chunk = self._source.read(len(view))
        if not chunk:
            return 0
        count = len(chunk)
        view[:count] = chunk
        self._consumed += count
        self._check_size()
        return count

    def close(self) -> None:
        if not self.closed:
            try:
                self._source.close()
            finally:
                super().close()


class FileIngressPolicy:
    """Apply deterministic file limits without exposing file paths or payloads."""

    def __init__(self, limits: FileIngressLimits | None = None) -> None:
        self.limits = limits or FileIngressLimits()

    def limit(self, kind: FileKind) -> FileLimit:
        return {
            FileKind.CONFIG: self.limits.config,
            FileKind.GEDCOM: self.limits.gedcom,
            FileKind.ROOTSMAGIC: self.limits.rootsmagic,
            FileKind.OCR: self.limits.ocr,
            FileKind.MANIFEST: self.limits.manifest,
            FileKind.JSON_SCHEMA: self.limits.json_schema,
            FileKind.PROMPT_BODY: self.limits.prompt_body,
        }[kind]

    @staticmethod
    def _error(
        code: str,
        message: str,
        kind: FileKind,
        *,
        limit_name: str | None = None,
        limit: int | None = None,
        error_type: str | None = None,
    ) -> FileIngressError:
        details: dict[str, object] = {"input_class": kind.value}
        if limit_name is not None:
            details["limit_name"] = limit_name
        if limit is not None:
            details["limit"] = limit
        if error_type is not None:
            details["error_type"] = error_type
        return FileIngressError(code, message, details=details)

    def _validate_stat(self, value: os.stat_result, kind: FileKind) -> FileSnapshot:
        if not stat.S_ISREG(value.st_mode):
            raise self._error(
                "FILE_INPUT_NOT_REGULAR",
                f"The {kind.value} input must be a regular file.",
                kind,
            )
        if value.st_ino <= 0:
            raise self._error(
                "FILE_INPUT_UNREADABLE",
                f"The {kind.value} input does not provide a reliable filesystem identity.",
                kind,
            )
        if kind is FileKind.ROOTSMAGIC and value.st_nlink != 1:
            raise self._error(
                "FILE_INPUT_CHANGED",
                "The rootsmagic input cannot be consumed safely while it has filesystem aliases.",
                kind,
            )
        maximum = self.limit(kind).max_bytes
        if value.st_size > maximum:
            raise self._error(
                "FILE_INPUT_TOO_LARGE",
                f"The {kind.value} input exceeds the configured byte limit ({maximum}).",
                kind,
                limit_name="max_bytes",
                limit=maximum,
            )
        return FileSnapshot.from_stat(value)

    def normalize_path(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        absolute: bool = False,
        resolve: bool = False,
    ) -> Path:
        """Normalize one user pathname without exposing unsafe spellings."""

        try:
            selected = Path(path).expanduser()
            if resolve:
                return selected.resolve(strict=False)
            if absolute:
                return selected.absolute()
            return selected
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise self._error(
                "FILE_INPUT_UNREADABLE",
                f"The {kind.value} input could not be opened safely.",
                kind,
                error_type=type(exc).__name__,
            ) from exc

    def _selected_path(self, path: str | Path, kind: FileKind) -> Path:
        return self.normalize_path(path, kind)

    def _open_descriptor(
        self,
        path: Path,
        kind: FileKind,
        *,
        expected: FileSnapshot | None = None,
    ) -> tuple[int, FileSnapshot]:
        try:
            preflight = self._validate_stat(os.lstat(path), kind)
        except FileIngressError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._error(
                "FILE_INPUT_UNREADABLE",
                f"The {kind.value} input could not be opened safely.",
                kind,
                error_type=type(exc).__name__,
            ) from exc
        if expected is not None and preflight != expected:
            raise self._error(
                "FILE_INPUT_CHANGED",
                f"The {kind.value} input changed before it could be consumed.",
                kind,
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            descriptor = os.open(path, flags)
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._error(
                "FILE_INPUT_UNREADABLE",
                f"The {kind.value} input could not be opened safely.",
                kind,
                error_type=type(exc).__name__,
            ) from exc
        try:
            snapshot = self._validate_stat(os.fstat(descriptor), kind)
            if snapshot != preflight:
                raise self._error(
                    "FILE_INPUT_CHANGED",
                    f"The {kind.value} input changed before it could be consumed.",
                    kind,
                )
            if expected is not None and snapshot != expected:
                raise self._error(
                    "FILE_INPUT_CHANGED",
                    f"The {kind.value} input changed before it could be consumed.",
                    kind,
                )
            try:
                prefix = os.read(descriptor, min(16, self.limit(kind).max_bytes))
                os.lseek(descriptor, 0, os.SEEK_SET)
            except OSError as exc:
                raise self._error(
                    "FILE_INPUT_IO",
                    f"The {kind.value} input could not be read completely.",
                    kind,
                    error_type=type(exc).__name__,
                ) from exc
            if any(prefix.startswith(signature) for signature in _ARCHIVE_SIGNATURES):
                raise self._error(
                    "FILE_ARCHIVE_UNSUPPORTED",
                    f"Compressed or archived {kind.value} input is not supported.",
                    kind,
                )
            if kind is FileKind.ROOTSMAGIC and not prefix.startswith(b"SQLite format 3\x00"):
                raise self._error(
                    "FILE_FORMAT_INVALID",
                    "The rootsmagic input is not a supported SQLite database.",
                    kind,
                )
            return descriptor, snapshot
        except Exception:
            os.close(descriptor)
            raise

    def inspect(self, path: str | Path, kind: FileKind) -> FileSnapshot:
        """Run the cheapest safe bound before decoding or opening SQLite."""

        selected = self._selected_path(path, kind)
        descriptor, snapshot = self._open_descriptor(selected, kind)
        os.close(descriptor)
        return snapshot

    def assert_unchanged(self, path: str | Path, kind: FileKind, expected: FileSnapshot) -> None:
        """Reject replacement, growth, truncation, or in-place modification."""

        selected = self._selected_path(path, kind)
        try:
            current = FileSnapshot.from_stat(os.lstat(selected))
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._error(
                "FILE_INPUT_CHANGED",
                f"The {kind.value} input changed while it was being consumed.",
                kind,
                error_type=type(exc).__name__,
            ) from exc
        if current != expected:
            raise self._error(
                "FILE_INPUT_CHANGED",
                f"The {kind.value} input changed while it was being consumed.",
                kind,
            )

    @staticmethod
    def _line_encoding(prefix: bytes) -> tuple[str, str, int]:
        if prefix.startswith(b"\xff\xfe"):
            return "utf-16", "utf-16-le", 2
        if prefix.startswith(b"\xfe\xff"):
            return "utf-16", "utf-16-be", 2
        return "utf-8-sig", "utf-8", 3 if prefix.startswith(b"\xef\xbb\xbf") else 0

    def _too_large(self, kind: FileKind) -> FileIngressError:
        maximum = self.limit(kind).max_bytes
        return self._error(
            "FILE_INPUT_TOO_LARGE",
            f"The {kind.value} input exceeds the configured byte limit ({maximum}).",
            kind,
            limit_name="max_bytes",
            limit=maximum,
        )

    def _bounded_chunks(self, handle: BinaryIO, kind: FileKind) -> Iterator[bytes]:
        """Yield source bytes without consuming or exposing more than the budget."""

        maximum = self.limit(kind).max_bytes
        consumed = 0
        while consumed < maximum:
            cancellation_checkpoint()
            if os.fstat(handle.fileno()).st_size > maximum:
                raise self._too_large(kind)
            chunk = handle.read(min(_READ_CHUNK_BYTES, maximum - consumed))
            if not chunk:
                break
            consumed += len(chunk)
            if os.fstat(handle.fileno()).st_size > maximum:
                raise self._too_large(kind)
            yield chunk
        if os.fstat(handle.fileno()).st_size > maximum:
            raise self._too_large(kind)
        if consumed == maximum and handle.read(1):
            raise self._too_large(kind)

    def iter_text_line_items(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        count_lines_as_records: bool = True,
        expected: FileSnapshot | None = None,
    ) -> Iterator[TextLine]:
        """Yield decoded lines and source byte sizes while enforcing budgets."""

        selected = self._selected_path(path, kind)
        before = expected or self.inspect(selected, kind)
        descriptor, opened = self._open_descriptor(selected, kind, expected=before)
        source: BinaryIO = os.fdopen(descriptor, "rb", buffering=0, closefd=True)
        bounded = _BoundedRawReader(
            source,
            self.limit(kind).max_bytes,
            lambda: self._too_large(kind),
        )
        binary = io.BufferedReader(bounded)
        try:
            prefix = binary.peek(4)[:4]
            if prefix.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
                raise self._error(
                    "FILE_ENCODING_INVALID",
                    f"The {kind.value} input is not valid supported Unicode text.",
                    kind,
                    error_type="UnsupportedEncoding",
                )
            if kind is not FileKind.GEDCOM and prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
                raise self._error(
                    "FILE_ENCODING_INVALID",
                    f"The {kind.value} input is not valid supported Unicode text.",
                    kind,
                    error_type="UnsupportedEncoding",
                )
            text_encoding, byte_encoding, first_line_prefix_bytes = self._line_encoding(prefix)
            text = io.TextIOWrapper(binary, encoding=text_encoding, errors="strict", newline="")
            maximum_line = self.limit(kind).max_line_bytes
            maximum_records = self.limit(kind).max_records
            record_count = 0
            read_limit = (maximum_line + 2) if maximum_line is not None else -1
            try:
                while True:
                    cancellation_checkpoint()
                    raw_line = text.readline(read_limit)
                    if not raw_line:
                        break
                    if "\x00" in raw_line:
                        raise self._error(
                            "FILE_NUL_BYTE_UNSUPPORTED",
                            f"The {kind.value} input contains an unsupported NUL byte.",
                            kind,
                        )
                    line_bytes = len(raw_line.encode(byte_encoding)) + first_line_prefix_bytes
                    first_line_prefix_bytes = 0
                    if maximum_line is not None:
                        if line_bytes > maximum_line:
                            raise self._error(
                                "FILE_LINE_TOO_LONG",
                                f"A physical line in the {kind.value} input exceeds the configured byte limit ({maximum_line}).",
                                kind,
                                limit_name="max_line_bytes",
                                limit=maximum_line,
                            )
                        if len(raw_line) == read_limit and not raw_line.endswith(("\n", "\r")):
                            raise self._error(
                                "FILE_LINE_TOO_LONG",
                                f"A physical line in the {kind.value} input exceeds the configured byte limit ({maximum_line}).",
                                kind,
                                limit_name="max_line_bytes",
                                limit=maximum_line,
                            )
                    if count_lines_as_records:
                        record_count += 1
                        if maximum_records is not None and record_count > maximum_records:
                            raise self._error(
                                "FILE_RECORD_LIMIT_EXCEEDED",
                                f"The {kind.value} input exceeds the configured record limit ({maximum_records}).",
                                kind,
                                limit_name="max_records",
                                limit=maximum_records,
                            )
                    yield TextLine(raw_line, line_bytes)
                current = FileSnapshot.from_stat(os.fstat(text.buffer.fileno()))
                if current != opened:
                    raise self._error(
                        "FILE_INPUT_CHANGED",
                        f"The {kind.value} input changed while it was being consumed.",
                        kind,
                    )
                self.assert_unchanged(selected, kind, opened)
            except UnicodeError as exc:
                raise self._error(
                    "FILE_ENCODING_INVALID",
                    f"The {kind.value} input is not valid supported Unicode text.",
                    kind,
                    error_type=type(exc).__name__,
                ) from exc
            except OSError as exc:
                raise self._error(
                    "FILE_INPUT_IO",
                    f"The {kind.value} input could not be read completely.",
                    kind,
                    error_type=type(exc).__name__,
                ) from exc
            finally:
                text.close()
        except FileIngressError:
            raise
        except OSError as exc:
            raise self._error(
                "FILE_INPUT_IO",
                f"The {kind.value} input could not be read completely.",
                kind,
                error_type=type(exc).__name__,
            ) from exc
        finally:
            if not binary.closed:
                binary.close()

    def iter_text_lines(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        count_lines_as_records: bool = True,
        expected: FileSnapshot | None = None,
    ) -> Iterator[str]:
        """Yield decoded physical lines while enforcing byte and line budgets."""

        for item in self.iter_text_line_items(
            path,
            kind,
            count_lines_as_records=count_lines_as_records,
            expected=expected,
        ):
            yield item.text

    def validate_record(
        self,
        kind: FileKind,
        *,
        count: int,
        byte_count: int,
        nesting: int,
        collection_items: int,
    ) -> None:
        """Check a parsed record against count, size, nesting, and item budgets."""

        limit = self.limit(kind)
        if limit.max_records is not None and count > limit.max_records:
            raise self._error(
                "FILE_RECORD_LIMIT_EXCEEDED",
                f"The {kind.value} input exceeds the configured record limit ({limit.max_records}).",
                kind,
                limit_name="max_records",
                limit=limit.max_records,
            )
        if limit.max_record_bytes is not None and byte_count > limit.max_record_bytes:
            raise self._error(
                "FILE_RECORD_TOO_LARGE",
                f"A logical record in the {kind.value} input exceeds the configured byte limit ({limit.max_record_bytes}).",
                kind,
                limit_name="max_record_bytes",
                limit=limit.max_record_bytes,
            )
        if limit.max_nesting is not None and nesting > limit.max_nesting:
            raise self._error(
                "FILE_NESTING_LIMIT_EXCEEDED",
                f"The {kind.value} input exceeds the configured nesting limit ({limit.max_nesting}).",
                kind,
                limit_name="max_nesting",
                limit=limit.max_nesting,
            )
        if limit.max_collection_items is not None and collection_items > limit.max_collection_items:
            raise self._error(
                "FILE_COLLECTION_LIMIT_EXCEEDED",
                f"A logical record in the {kind.value} input exceeds the configured collection limit ({limit.max_collection_items}).",
                kind,
                limit_name="max_collection_items",
                limit=limit.max_collection_items,
            )

    def read_text(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        allow_empty: bool = True,
        expected: FileSnapshot | None = None,
    ) -> str:
        value = "".join(self.iter_text_lines(path, kind, expected=expected))
        if not allow_empty and not value:
            raise self._error(
                "FILE_INPUT_EMPTY",
                f"The {kind.value} input must not be empty.",
                kind,
            )
        return value

    def read_json(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        require_object: bool = False,
        expected: FileSnapshot | None = None,
    ) -> object:
        """Read and bound a JSON document without including parser payloads in errors."""

        text = self.read_text(path, kind, allow_empty=False, expected=expected)
        self._validate_json_nesting(text, kind)
        try:
            value = json.loads(
                text,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise self._error(
                "FILE_JSON_INVALID",
                f"The {kind.value} input is not valid JSON.",
                kind,
                error_type=type(exc).__name__,
            ) from exc
        except RecursionError as exc:
            maximum = self.limit(kind).max_nesting
            raise self._error(
                "FILE_NESTING_LIMIT_EXCEEDED",
                f"The {kind.value} input exceeds the configured nesting limit ({maximum}).",
                kind,
                limit_name="max_nesting",
                limit=maximum,
            ) from exc
        pending = [value]
        while pending:
            cancellation_checkpoint()
            item = pending.pop()
            if isinstance(item, float) and not math.isfinite(item):
                raise self._error(
                    "FILE_JSON_INVALID",
                    f"The {kind.value} input is not valid JSON.",
                    kind,
                    error_type="NonFiniteNumber",
                )
            if isinstance(item, dict):
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        if require_object and not isinstance(value, dict):
            raise self._error(
                "FILE_JSON_TYPE_INVALID",
                f"The {kind.value} input must contain one JSON object.",
                kind,
            )
        self.validate_structure(value, kind)
        return value

    def _validate_json_nesting(self, text: str, kind: FileKind) -> None:
        """Reject excessive JSON container depth before recursive parser work."""

        maximum = self.limit(kind).max_nesting
        if maximum is None:
            return
        depth = 0
        in_string = False
        escaped = False
        for character in text:
            cancellation_checkpoint()
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character in "[{":
                depth += 1
                if depth > maximum:
                    raise self._error(
                        "FILE_NESTING_LIMIT_EXCEEDED",
                        f"The {kind.value} input exceeds the configured nesting limit ({maximum}).",
                        kind,
                        limit_name="max_nesting",
                        limit=maximum,
                    )
            elif character in "]}":
                depth = max(0, depth - 1)

    def validate_toml_nesting(self, text: str, kind: FileKind = FileKind.CONFIG) -> None:
        """Reject excessive TOML array/inline-table depth before parser work."""

        maximum = self.limit(kind).max_nesting
        if maximum is None:
            return
        depth = 0
        index = 0
        quote: str | None = None
        escaped = False
        comment = False
        while index < len(text):
            cancellation_checkpoint()
            character = text[index]
            if comment:
                if character in "\r\n":
                    comment = False
                index += 1
                continue
            if quote is not None:
                delimiter = quote[-1]
                multiline = len(quote) == 3
                if delimiter == '"' and escaped:
                    escaped = False
                    index += 1
                    continue
                if delimiter == '"' and character == "\\":
                    escaped = True
                    index += 1
                    continue
                if multiline and text.startswith(quote, index):
                    quote = None
                    index += 3
                    continue
                if not multiline and character == delimiter:
                    quote = None
                index += 1
                continue
            if text.startswith('"""', index):
                quote = '"""'
                index += 3
                continue
            if text.startswith("'''", index):
                quote = "'''"
                index += 3
                continue
            if character in "\"'":
                quote = character
            elif character == "#":
                comment = True
            elif character in "[{":
                depth += 1
                if depth > maximum:
                    raise self._error(
                        "FILE_NESTING_LIMIT_EXCEEDED",
                        f"The {kind.value} input exceeds the configured nesting limit ({maximum}).",
                        kind,
                        limit_name="max_nesting",
                        limit=maximum,
                    )
            elif character in "]}":
                depth = max(0, depth - 1)
            index += 1

    def validate_structure(
        self,
        value: object,
        kind: FileKind,
        *,
        root_container_implicit: bool = False,
    ) -> None:
        """Bound container nesting and aggregate members in parsed data.

        JSON's root container is explicit and counts toward the nesting budget.
        TOML's document mapping is parser-created, so callers may exclude only
        that implicit root while still counting tables, arrays, and inline
        tables. Scalar leaves and mapping keys never add a nesting level.
        """

        limit = self.limit(kind)
        root_parent_depth = -1 if root_container_implicit and isinstance(value, (dict, list)) else 0
        pending: list[tuple[object, int]] = [(value, root_parent_depth)]
        collection_items = 0
        while pending:
            cancellation_checkpoint()
            item, parent_depth = pending.pop()
            if isinstance(item, str):
                if "\x00" in item:
                    raise self._error(
                        "FILE_NUL_BYTE_UNSUPPORTED",
                        f"The {kind.value} input contains an unsupported NUL byte.",
                        kind,
                    )
            elif isinstance(item, dict):
                depth = parent_depth + 1
                if limit.max_nesting is not None and depth > limit.max_nesting:
                    raise self._error(
                        "FILE_NESTING_LIMIT_EXCEEDED",
                        f"The {kind.value} input exceeds the configured nesting limit ({limit.max_nesting}).",
                        kind,
                        limit_name="max_nesting",
                        limit=limit.max_nesting,
                    )
                collection_items += len(item)
                pending.extend((child, depth) for child in item.keys())
                pending.extend((child, depth) for child in item.values())
            elif isinstance(item, list):
                depth = parent_depth + 1
                if limit.max_nesting is not None and depth > limit.max_nesting:
                    raise self._error(
                        "FILE_NESTING_LIMIT_EXCEEDED",
                        f"The {kind.value} input exceeds the configured nesting limit ({limit.max_nesting}).",
                        kind,
                        limit_name="max_nesting",
                        limit=limit.max_nesting,
                    )
                collection_items += len(item)
                pending.extend((child, depth) for child in item)
            if (
                limit.max_collection_items is not None
                and collection_items > limit.max_collection_items
            ):
                raise self._error(
                    "FILE_COLLECTION_LIMIT_EXCEEDED",
                    f"The {kind.value} input exceeds the configured collection limit ({limit.max_collection_items}).",
                    kind,
                    limit_name="max_collection_items",
                    limit=limit.max_collection_items,
                )

    def sha256(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        expected: FileSnapshot | None = None,
    ) -> tuple[str, FileSnapshot]:
        """Hash a bounded file and reject any replacement or in-place change."""

        selected = self._selected_path(path, kind)
        before = expected or self.inspect(selected, kind)
        descriptor, opened = self._open_descriptor(selected, kind, expected=before)
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                for chunk in self._bounded_chunks(handle, kind):
                    digest.update(chunk)
                current = FileSnapshot.from_stat(os.fstat(handle.fileno()))
            if current != opened:
                raise self._error(
                    "FILE_INPUT_CHANGED",
                    f"The {kind.value} input changed while it was being consumed.",
                    kind,
                )
            self.assert_unchanged(selected, kind, opened)
        except FileIngressError:
            raise
        except OSError as exc:
            raise self._error(
                "FILE_INPUT_IO",
                f"The {kind.value} input could not be read completely.",
                kind,
                error_type=type(exc).__name__,
            ) from exc
        return digest.hexdigest(), opened

    def fingerprint(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        expected: FileSnapshot | None = None,
    ) -> FileFingerprint:
        """Return a digest tied to the exact identity consumed."""

        digest, snapshot = self.sha256(path, kind, expected=expected)
        return FileFingerprint(snapshot=snapshot, sha256=digest)

    def verify(
        self,
        path: str | Path,
        kind: FileKind,
        expected: FileFingerprint,
    ) -> None:
        """Re-read a file and require both identity and content to match."""

        current = self.fingerprint(path, kind, expected=expected.snapshot)
        if current.sha256 != expected.sha256:
            raise self._error(
                "FILE_INPUT_CHANGED",
                f"The {kind.value} input changed while it was being consumed.",
                kind,
            )

    def copy_to(
        self,
        path: str | Path,
        destination: str | Path,
        kind: FileKind,
        *,
        expected: FileFingerprint,
    ) -> None:
        """Copy only the verified source identity and reject mid-copy changes."""

        selected = self._selected_path(path, kind)
        target = Path(destination)
        descriptor, opened = self._open_descriptor(
            selected,
            kind,
            expected=expected.snapshot,
        )
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as source:
                with target.open("xb", buffering=0) as output:
                    try:
                        for chunk in self._bounded_chunks(source, kind):
                            digest.update(chunk)
                            remaining = memoryview(chunk)
                            while remaining:
                                written = output.write(remaining)
                                if written is None or written <= 0:
                                    raise OSError("The destination write made no progress.")
                                remaining = remaining[written:]
                        output.flush()
                        os.fsync(output.fileno())
                        current = FileSnapshot.from_stat(os.fstat(source.fileno()))
                        if current != opened or digest.hexdigest() != expected.sha256:
                            raise self._error(
                                "FILE_INPUT_CHANGED",
                                f"The {kind.value} input changed while it was being consumed.",
                                kind,
                            )
                        self.assert_unchanged(selected, kind, opened)
                    except BaseException:
                        cleanup_open_path(target, output.fileno())
                        raise
        except (FileExistsError, FileIngressError):
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._error(
                "FILE_INPUT_IO",
                f"The {kind.value} input could not be copied safely.",
                kind,
                error_type=type(exc).__name__,
            ) from exc


__all__ = [
    "FileFingerprint",
    "FileIngressLimits",
    "FileIngressPolicy",
    "FileKind",
    "FileLimit",
    "FileSnapshot",
    "TextLine",
    "cleanup_open_path",
]
