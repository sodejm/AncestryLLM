"""Composition root that launches the implemented prompt-toolkit REPL."""

from __future__ import annotations

from ancestryllm.core.context import AppContext


def run_repl(context: AppContext) -> int:
    """Resolve the REPL lazily so one-shot CLI startup stays adapter-neutral."""

    from ancestryllm.console.shell import run_repl as run_console

    return run_console(context)


__all__ = ["run_repl"]
