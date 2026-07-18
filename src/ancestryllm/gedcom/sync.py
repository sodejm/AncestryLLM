"""Public incremental update/rebase entry point."""

from __future__ import annotations

from collections.abc import Sequence

from ancestryllm.gedcom import engine, incremental


def run_sync(argv: Sequence[str]) -> int:
    """Run an offline-first update or rebase through the migrated engine."""
    arguments = list(argv)
    if arguments and arguments[0] == "update" and "--ai-backend" not in arguments:
        arguments.extend(["--ai-backend", "none"])
    return incremental.main(arguments, engine)


__all__ = ["run_sync"]
