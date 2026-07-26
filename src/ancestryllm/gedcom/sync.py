"""Public incremental update/rebase entry point."""

from __future__ import annotations

from collections.abc import Sequence

from ancestryllm.gedcom import engine, incremental
from ancestryllm.gedcom.incremental import ResolverFactory


def run_sync(
    argv: Sequence[str],
    *,
    resolver_factory: ResolverFactory | None = None,
) -> int:
    """Run an offline-first update or rebase through the migrated engine."""
    return incremental.main(list(argv), engine, resolver_factory=resolver_factory)


__all__ = ["run_sync"]
