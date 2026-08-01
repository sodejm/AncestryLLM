"""Rich-backed rendering shared by terminal adapters."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, TextIO, cast

from rich.console import Console
from rich.text import Text

from ancestryllm.application.dto import ErrorEnvelope
from ancestryllm.application.errors import error_envelope
from ancestryllm.application.results import (
    CommandResult,
    ErrorResult,
    MarkdownResult,
    SuccessResult,
    TableResult,
    WarningResult,
)
from ancestryllm.core.errors import AncestryError


def to_plain(value: Any) -> Any:
    """Convert supported results to serializable values without rendering them."""

    if isinstance(value, CommandResult):
        return value.to_serializable()
    if is_dataclass(value):
        return {key: to_plain(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, Path):
        return str(value)
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
        return cls(Console(file=file, force_terminal=False, color_system=None, highlight=False))

    def render(self, value: Any, *, json_output: bool = False) -> None:
        plain = to_plain(value)
        if json_output:
            self.console.file.write(json.dumps(plain, indent=2, sort_keys=True))
            self.console.file.write("\n")
        elif isinstance(value, MarkdownResult):
            self.console.file.write(value.markdown)
        elif isinstance(value, SuccessResult):
            self.console.print(value.message)
        elif isinstance(value, TableResult):
            self._render_table(value)
        elif isinstance(value, WarningResult):
            self.console.print(Text(f"[{value.code}] {value.message}", style="bold yellow"))
        elif isinstance(value, ErrorResult):
            self._render_error_envelope(value.error)
        else:
            self._render_plain(plain)

    def render_error(self, error: AncestryError) -> None:
        self.render(ErrorResult(error_envelope(error)))

    def _render_table(self, result: TableResult) -> None:
        for row in result.rows:
            item = dict(zip(result.columns, row, strict=True))
            self.console.print(json.dumps(item, sort_keys=True))

    def _render_error_envelope(self, error: ErrorEnvelope) -> None:
        message = f"[{error.code}] {error.message}"
        if error.remediation:
            message += f"\nHow to fix: {error.remediation}"
        self.console.print(Text(message, style="bold red"))

    def _render_plain(self, plain: Any) -> None:
        if isinstance(plain, str):
            self.console.print(plain)
        elif isinstance(plain, list):
            for item in plain:
                self.console.print(
                    item if isinstance(item, str) else json.dumps(item, sort_keys=True)
                )
        else:
            self.console.print(json.dumps(plain, indent=2, sort_keys=True))


__all__ = ["PresentationAdapter", "to_plain"]
