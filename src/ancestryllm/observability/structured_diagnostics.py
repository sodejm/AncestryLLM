"""Create and retain bounded, privacy-safe desktop diagnostic events."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from collections.abc import Mapping

DESKTOP_DIAGNOSTIC_SCHEMA_VERSION: Final = "ancestryllm.desktop-diagnostic/1"
MAX_DESKTOP_DIAGNOSTIC_EVENT_BYTES: Final = 4096
DEFAULT_DESKTOP_DIAGNOSTIC_MAX_BYTES: Final = 512 * 1024
DEFAULT_DESKTOP_DIAGNOSTIC_MAX_FILES: Final = 3

_RUN_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}$")
_METADATA_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SENSITIVE_METADATA_KEY_FRAGMENT = re.compile(
    r"content|database|email|error|exception|family|gedcom|key|message|name|path|"
    r"person|prompt|query|secret|stack|token|url"
)
_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


class DesktopDiagnosticSeverity(StrEnum):
    """Stable severity values accepted by the desktop diagnostic contract."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DesktopDiagnosticComponent(StrEnum):
    """Process layers permitted to persist desktop diagnostic events."""

    ELECTRON_MAIN = "electron-main"
    PYTHON_CORE = "python-core"
    FLASK_SIDECAR = "flask-sidecar"


DesktopDiagnosticMetadataValue = bool | int | None


@dataclass(frozen=True, slots=True)
class DesktopDiagnosticEvent:
    """One versioned event safe for local persistence and support export."""

    schema_version: str
    timestamp: str
    run_id: str
    code: str
    severity: str
    component: str
    app_version: str
    metadata: Mapping[str, DesktopDiagnosticMetadataValue]

    def as_document(self) -> dict[str, object]:
        """Return a serializable document with an ordinary metadata mapping."""

        return {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "code": self.code,
            "severity": self.severity,
            "component": self.component,
            "app_version": self.app_version,
            "metadata": dict(self.metadata),
        }


def create_desktop_diagnostic_run_id() -> str:
    """Create a non-identifying UUIDv4 correlation identifier for one desktop run."""

    return str(uuid4())


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("invalid diagnostic run identifier")
    parsed = UUID(run_id)
    if parsed.version != 4:
        raise ValueError("invalid diagnostic run identifier")


def _normalize_timestamp(now: datetime | None) -> str:
    timestamp = now or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("diagnostic timestamp must be timezone-aware")
    utc_timestamp = timestamp.astimezone(UTC)
    rendered = utc_timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if not _TIMESTAMP_PATTERN.fullmatch(rendered):
        raise ValueError("invalid diagnostic timestamp")
    return rendered


def _normalize_metadata(
    metadata: Mapping[str, DesktopDiagnosticMetadataValue] | None,
) -> Mapping[str, DesktopDiagnosticMetadataValue]:
    values = dict(metadata or {})
    if len(values) > 8:
        raise ValueError("diagnostic metadata exceeded its entry limit")
    normalized: dict[str, DesktopDiagnosticMetadataValue] = {}
    for key, value in values.items():
        if (
            not isinstance(key, str)
            or not _METADATA_KEY_PATTERN.fullmatch(key)
            or _SENSITIVE_METADATA_KEY_FRAGMENT.search(key)
        ):
            raise ValueError("diagnostic metadata contains a prohibited key")
        if isinstance(value, bool) or value is None:
            normalized[key] = value
            continue
        if isinstance(value, int) and -1_000_000_000 <= value <= 1_000_000_000:
            normalized[key] = value
            continue
        raise ValueError("diagnostic metadata contains a prohibited value")
    return MappingProxyType(normalized)


def create_desktop_diagnostic_event(
    *,
    run_id: str,
    app_version: str,
    code: str,
    severity: DesktopDiagnosticSeverity,
    component: DesktopDiagnosticComponent,
    metadata: Mapping[str, DesktopDiagnosticMetadataValue] | None = None,
    now: datetime | None = None,
) -> DesktopDiagnosticEvent:
    """Validate one event without accepting errors, paths, or free-form text."""

    _validate_run_id(run_id)
    if not _VERSION_PATTERN.fullmatch(app_version):
        raise ValueError("invalid diagnostic app version")
    if not _CODE_PATTERN.fullmatch(code):
        raise ValueError("invalid diagnostic code")
    if not isinstance(severity, DesktopDiagnosticSeverity):
        raise ValueError("invalid diagnostic severity")
    if not isinstance(component, DesktopDiagnosticComponent):
        raise ValueError("invalid diagnostic component")
    event = DesktopDiagnosticEvent(
        schema_version=DESKTOP_DIAGNOSTIC_SCHEMA_VERSION,
        timestamp=_normalize_timestamp(now),
        run_id=run_id,
        code=code,
        severity=severity.value,
        component=component.value,
        app_version=app_version,
        metadata=_normalize_metadata(metadata),
    )
    encoded = json.dumps(
        event.as_document(),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if len(encoded) > MAX_DESKTOP_DIAGNOSTIC_EVENT_BYTES:
        raise ValueError("diagnostic event exceeded its encoded size limit")
    return event


def _positive_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _is_regular_file_or_missing(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return True
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _is_owned_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


class DesktopDiagnosticWriter:
    """Persist one component-specific JSONL stream with bounded rotation."""

    def __init__(
        self,
        *,
        directory: Path,
        run_id: str,
        app_version: str,
        component: DesktopDiagnosticComponent,
        max_bytes: int = DEFAULT_DESKTOP_DIAGNOSTIC_MAX_BYTES,
        max_files: int = DEFAULT_DESKTOP_DIAGNOSTIC_MAX_FILES,
    ) -> None:
        """Configure a writer without touching the filesystem."""

        _validate_run_id(run_id)
        if not _VERSION_PATTERN.fullmatch(app_version):
            raise ValueError("invalid diagnostic app version")
        if not isinstance(component, DesktopDiagnosticComponent):
            raise ValueError("invalid diagnostic component")
        self._directory = directory
        self._run_id = run_id
        self._app_version = app_version
        self._component = component
        self._max_bytes = _positive_integer(max_bytes, "diagnostic max_bytes")
        self._max_files = _positive_integer(max_files, "diagnostic max_files")
        self._active_path = directory / f"{component.value}.jsonl"

    def write(
        self,
        code: str,
        severity: DesktopDiagnosticSeverity,
        metadata: Mapping[str, DesktopDiagnosticMetadataValue] | None = None,
    ) -> bool:
        """Attempt to persist one event; return false instead of affecting application flow."""

        try:
            event = create_desktop_diagnostic_event(
                run_id=self._run_id,
                app_version=self._app_version,
                code=code,
                severity=severity,
                component=self._component,
                metadata=metadata,
            )
            line = (
                json.dumps(event.as_document(), separators=(",", ":"), sort_keys=True)
                + "\n"
            ).encode()
            if len(line) > self._max_bytes:
                return False
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not _is_owned_directory(self._directory):
                return False
            if not _is_regular_file_or_missing(self._active_path):
                return False
            current_bytes = self._active_path.stat().st_size if self._active_path.exists() else 0
            if current_bytes + len(line) > self._max_bytes:
                self._rotate()
            descriptor = os.open(
                self._active_path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, line)
            finally:
                os.close(descriptor)
        except (OSError, TypeError, ValueError):
            return False
        return True

    def clear(self) -> bool:
        """Remove only this component's active and rotated diagnostic files."""

        try:
            if not self._directory.exists():
                return True
            if not _is_owned_directory(self._directory):
                return False
            for index in range(self._max_files):
                path = self._active_path if index == 0 else Path(f"{self._active_path}.{index}")
                if path.exists():
                    if not _is_regular_file_or_missing(path):
                        return False
                    path.unlink()
        except OSError:
            return False
        return True

    def _rotate(self) -> None:
        if self._max_files == 1:
            self._active_path.unlink(missing_ok=True)
            return
        oldest = Path(f"{self._active_path}.{self._max_files - 1}")
        if oldest.exists():
            if not _is_regular_file_or_missing(oldest):
                raise OSError("unsafe diagnostic rotation target")
            oldest.unlink()
        for index in range(self._max_files - 2, 0, -1):
            source = Path(f"{self._active_path}.{index}")
            if not source.exists():
                continue
            if not _is_regular_file_or_missing(source):
                raise OSError("unsafe diagnostic rotation source")
            source.replace(Path(f"{self._active_path}.{index + 1}"))
        if self._active_path.exists():
            if not _is_regular_file_or_missing(self._active_path):
                raise OSError("unsafe diagnostic active file")
            self._active_path.replace(Path(f"{self._active_path}.1"))


__all__ = [
    "DEFAULT_DESKTOP_DIAGNOSTIC_MAX_BYTES",
    "DEFAULT_DESKTOP_DIAGNOSTIC_MAX_FILES",
    "DESKTOP_DIAGNOSTIC_SCHEMA_VERSION",
    "MAX_DESKTOP_DIAGNOSTIC_EVENT_BYTES",
    "DesktopDiagnosticComponent",
    "DesktopDiagnosticEvent",
    "DesktopDiagnosticSeverity",
    "DesktopDiagnosticWriter",
    "create_desktop_diagnostic_event",
    "create_desktop_diagnostic_run_id",
]
