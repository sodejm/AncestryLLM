"""Public incremental update/rebase entry point."""

from __future__ import annotations

from collections.abc import Sequence

from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.gedcom import engine, incremental
from ancestryllm.gedcom.incremental import ResolverFactory


def run_sync(
    argv: Sequence[str],
    ingress: FileIngressPolicy | None = None,
    *,
    resolver_factory: ResolverFactory | None = None,
    raise_errors: bool = False,
) -> int:
    """Run an offline-first update or rebase through the migrated engine."""
    return incremental.main(
        list(argv),
        engine,
        ingress,
        resolver_factory=resolver_factory,
        raise_errors=raise_errors,
    )


__all__ = ["run_sync"]
