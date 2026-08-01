"""Declared, transport-neutral command result contracts."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

import ancestryllm.application.dto as application_dto
from ancestryllm.application.dto import (
    ArtifactRef,
    ErrorEnvelope,
    JSONValue,
)


class ResultKind(StrEnum):
    """Stable semantic categories available to every presentation adapter."""

    SUCCESS = "success"
    TABLE = "table"
    MARKDOWN = "markdown"
    FILE_ARTIFACT = "file_artifact"
    WARNING = "warning"
    ERROR = "error"
    STRUCTURED = "structured"


def _validate_text(label: str, value: str) -> None:
    if len(value) > application_dto.MAX_TEXT_LENGTH:
        raise ValueError(f"{label} exceeds its bounded length.")
    if "\x00" in value:
        raise ValueError(f"{label} contains a null character.")


def _copy_json(value: object, *, location: str = "result") -> JSONValue:
    """Validate and defensively copy one strict-JSON-compatible value."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{location} numbers must be finite.")
        return value
    if isinstance(value, list):
        return [_copy_json(item, location=f"{location}[]") for item in value]
    if isinstance(value, dict):
        copied: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{location} object keys must be strings.")
            copied[key] = _copy_json(item, location=f"{location}.{key}")
        return copied
    raise TypeError(f"{location} must be JSON-compatible; got {type(value).__name__}.")


class CommandResult(ABC):
    """One semantic command result with an adapter-independent JSON value."""

    __slots__ = ()
    kind: ClassVar[ResultKind]

    @abstractmethod
    def to_serializable(self) -> JSONValue:
        """Return a strict-JSON value without presentation-layer objects."""


@dataclass(frozen=True, slots=True)
class SuccessResult(CommandResult):
    """A successful human-readable status message."""

    kind: ClassVar[ResultKind] = ResultKind.SUCCESS
    message: str

    def __post_init__(self) -> None:
        _validate_text("Success message", self.message)

    def to_serializable(self) -> JSONValue:
        return self.message


@dataclass(frozen=True, slots=True)
class TableResult(CommandResult):
    """Tabular records with stable column order and JSON-compatible cells."""

    kind: ClassVar[ResultKind] = ResultKind.TABLE
    columns: tuple[str, ...]
    rows: tuple[tuple[JSONValue, ...], ...]

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError("Table results require at least one column.")
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("Table result columns must be unique.")
        for column in self.columns:
            if not column:
                raise ValueError("Table result columns must not be empty.")
            _validate_text("Table column", column)
        normalized_rows: list[tuple[JSONValue, ...]] = []
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError("Table result rows must match the declared columns.")
            normalized_rows.append(tuple(_copy_json(cell, location="table cell") for cell in row))
        object.__setattr__(self, "rows", tuple(normalized_rows))

    def to_serializable(self) -> JSONValue:
        return [
            {
                column: _copy_json(cell, location="table cell")
                for column, cell in zip(self.columns, row, strict=True)
            }
            for row in self.rows
        ]


@dataclass(frozen=True, slots=True)
class StructuredResult(CommandResult):
    """Explicit compatibility result for an already normalized JSON value."""

    kind: ClassVar[ResultKind] = ResultKind.STRUCTURED
    value: JSONValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _copy_json(self.value))

    def to_serializable(self) -> JSONValue:
        return _copy_json(self.value)


@dataclass(frozen=True, slots=True)
class MarkdownResult(CommandResult):
    """Markdown text with an optional equivalent structured representation."""

    kind: ClassVar[ResultKind] = ResultKind.MARKDOWN
    markdown: str
    structured: StructuredResult | None = None

    def __post_init__(self) -> None:
        _validate_text("Markdown result", self.markdown)

    def to_serializable(self) -> JSONValue:
        if self.structured is not None:
            return self.structured.to_serializable()
        return self.markdown


@dataclass(frozen=True, slots=True)
class FileArtifactResult(CommandResult):
    """An opaque file artifact reference without a host filesystem path."""

    kind: ClassVar[ResultKind] = ResultKind.FILE_ARTIFACT
    artifact: ArtifactRef

    def to_serializable(self) -> JSONValue:
        return self.artifact.to_serializable()


@dataclass(frozen=True, slots=True)
class WarningResult(CommandResult):
    """A stable coded warning that does not change command success status."""

    kind: ClassVar[ResultKind] = ResultKind.WARNING
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not all(
            character.isalnum() or character in "._:-" for character in self.code
        ):
            raise ValueError("Warning codes must be non-empty stable identifiers.")
        _validate_text("Warning code", self.code)
        _validate_text("Warning message", self.message)

    def to_serializable(self) -> JSONValue:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class ErrorResult(CommandResult):
    """A stable application error envelope."""

    kind: ClassVar[ResultKind] = ResultKind.ERROR
    error: ErrorEnvelope

    def to_serializable(self) -> JSONValue:
        return self.error.to_serializable()


__all__ = [
    "CommandResult",
    "ErrorResult",
    "FileArtifactResult",
    "MarkdownResult",
    "ResultKind",
    "StructuredResult",
    "SuccessResult",
    "TableResult",
    "WarningResult",
]
