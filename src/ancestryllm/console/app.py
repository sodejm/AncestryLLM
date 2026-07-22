"""Compatibility entry point for the default prompt-toolkit/Rich REPL."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TextIO

from prompt_toolkit.input import Input
from prompt_toolkit.output import Output

from ancestryllm.console.shell import ReplApplication, run_repl
from ancestryllm.core.context import AppContext


class AncestryConsole(ReplApplication):
    """Backward-compatible console facade backed by the default REPL implementation."""

    def __init__(
        self,
        context: AppContext,
        *,
        safe_root: Path | None = None,
        input: Input | None = None,
        output: Output | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        super().__init__(
            context,
            safe_root=safe_root,
            input=input,
            output=output,
            stdout=stdout,
            stderr=stderr,
        )

    def cmdloop(self) -> int:
        """Run the prompt-toolkit REPL for callers still importing ``AncestryConsole``."""

        return asyncio.run(self.run_async())


__all__ = ["AncestryConsole", "run_repl"]
