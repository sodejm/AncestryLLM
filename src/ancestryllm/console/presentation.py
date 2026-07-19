"""Rich-backed rendering isolated from application services and command dispatch."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, TextIO, cast

from rich.console import Console
from rich.text import Text

from ancestryllm.core.errors import AncestryError


def to_plain(value: Any) -> Any:
    """Convert supported DTOs to serializable values without presentation concerns."""
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
    """Render DTOs and stable errors through an injected Rich console."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    @classmethod
    def for_file(cls, file: TextIO) -> PresentationAdapter:
        return cls(Console(file=file, force_terminal=False, color_system=None, highlight=False))

    def render(self, value: Any, *, json_output: bool = False) -> None:
        plain = to_plain(value)
        if json_output:
            self.console.print(json.dumps(plain, indent=2, sort_keys=True))
        elif isinstance(plain, str):
            self.console.print(plain)
        elif isinstance(plain, list):
            for item in plain:
                self.console.print(
                    item if isinstance(item, str) else json.dumps(item, sort_keys=True)
                )
        else:
            self.console.print(json.dumps(plain, indent=2, sort_keys=True))

    def render_error(self, error: AncestryError) -> None:
        self.console.print(Text(error.render(), style="bold red"))
