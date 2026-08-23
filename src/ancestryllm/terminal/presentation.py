"""Rich-backed rendering shared by terminal adapters."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, cast

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ancestryllm.application.errors import error_envelope
from ancestryllm.application.results import (
    CommandResult,
    ErrorResult,
    FileArtifactResult,
    MarkdownResult,
    StructuredResult,
    SuccessResult,
    TableResult,
    WarningResult,
)

if TYPE_CHECKING:
    from ancestryllm.application.dto import ErrorEnvelope
    from ancestryllm.core.errors import AncestryError


def to_plain(value: Any) -> Any:
    """Convert supported results to serializable values without rendering them."""

    if isinstance(value, CommandResult):
        return value.to_serializable()
    if is_dataclass(value):
        return {key: to_plain(item) for key, item in asdict(cast("Any", value)).items()}
    if isinstance(value, Path):
        raise TypeError("Presentation values must not contain Path host objects.")
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_plain(item) for item in value]
    if hasattr(value, "__table__"):
        return {
            column.name: to_plain(getattr(value, column.name)) for column in value.__table__.columns
        }
    return value


class PresentationAdapter:
    """Render results and stable errors through an injected Rich console."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    @classmethod
    def for_file(cls, file: TextIO) -> PresentationAdapter:
        """Create a presentation adapter for a generated file artifact."""
        return cls(Console(file=file, force_terminal=False, color_system=None, highlight=False))

    def render(self, value: Any, *, json_output: bool = False) -> None:
        """Render a typed application result for terminal or JSON presentation."""
        if json_output:
            self._print_text(json.dumps(to_plain(value), indent=2, sort_keys=True))
        elif isinstance(value, MarkdownResult):
            self._print_text(value.markdown, end="")
        elif isinstance(value, SuccessResult):
            self._print_text(value.message)
        elif isinstance(value, TableResult):
            self._render_table(value)
        elif isinstance(value, WarningResult):
            self.console.print(Text(f"[{value.code}] {value.message}", style="bold yellow"))
        elif isinstance(value, ErrorResult):
            self._render_error_envelope(value.error)
        elif isinstance(value, StructuredResult):
            self._render_structured(value)
        elif isinstance(value, FileArtifactResult):
            self._render_file_artifact(value)
        elif isinstance(value, CommandResult):
            raise TypeError(f"Unsupported CommandResult type: {type(value).__name__}.")
        else:
            self._render_plain(to_plain(value))

    def render_error(self, error: AncestryError) -> None:
        """Render a sanitized coded error through the presentation adapter."""
        self.render(ErrorResult(error_envelope(error)))

    def _render_table(self, result: TableResult) -> None:
        table = Table(
            box=box.ROUNDED,
            border_style="bright_black",
            header_style="bold cyan",
            highlight=False,
        )
        for column in result.columns:
            table.add_column(_table_heading(column), overflow="fold")
        for row in result.rows:
            table.add_row(*(Text(_table_cell(cell)) for cell in row))
        self.console.print(table)

    def _render_error_envelope(self, error: ErrorEnvelope) -> None:
        message = f"[{error.code}] {error.message}"
        if error.remediation:
            message += f"\nHow to fix: {error.remediation}"
        self.console.print(Text(message, style="bold red"))

    def _render_structured(self, result: StructuredResult) -> None:
        self._render_plain(result.to_serializable())

    def _render_file_artifact(self, result: FileArtifactResult) -> None:
        self._render_plain(result.to_serializable())

    def _render_plain(self, plain: Any) -> None:
        if isinstance(plain, str):
            self._print_text(plain)
        elif isinstance(plain, list):
            for item in plain:
                self._print_text(
                    item if isinstance(item, str) else json.dumps(item, sort_keys=True)
                )
        else:
            self._print_text(json.dumps(plain, indent=2, sort_keys=True))

    def _print_text(self, value: str, *, end: str = "\n") -> None:
        """Render literal application text without interpreting Rich markup."""

        self.console.print(
            Text(value),
            end=end,
            no_wrap=True,
            overflow="ignore",
            crop=False,
        )


def _table_heading(column: str) -> str:
    """Turn a stable snake-case field name into a readable table heading."""

    acronyms = {"id": "ID", "ids": "IDs", "json": "JSON", "url": "URL"}
    return " ".join(acronyms.get(word.casefold(), word.capitalize()) for word in column.split("_"))


def _table_cell(value: Any) -> str:
    """Format one JSON-compatible table cell for compact human display."""

    if value is None or value == [] or value == {}:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(_table_cell(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={_table_cell(item)}" for key, item in sorted(value.items()))
    return str(value)


__all__ = ["PresentationAdapter", "to_plain"]
