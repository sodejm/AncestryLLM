"""Public incremental update/rebase entry point."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.gedcom import engine, incremental
from ancestryllm.gedcom.contracts import IdentityResolver
from ancestryllm.gedcom.incremental import (
    SOURCE_ID_RE,
    SUPPORTED_VENDORS,
    CancellationCheck,
    ResolverFactory,
    SyncCommand,
    SyncExecutionResult,
    SyncRebaseCommand,
    SyncSnapshotInput,
    SyncUpdateCommand,
)


def execute_sync(
    argv: Sequence[str],
    ingress: FileIngressPolicy | None = None,
    *,
    resolver_factory: ResolverFactory | None = None,
    cancellation_check: CancellationCheck | None = None,
    raise_errors: bool = False,
) -> SyncExecutionResult:
    """Return one structured sync result without writing to terminal streams."""

    return incremental.execute(
        list(argv),
        engine,
        ingress,
        resolver_factory=resolver_factory,
        cancellation_check=cancellation_check,
        raise_errors=raise_errors,
    )


def execute_sync_command(
    command: SyncCommand,
    ingress: FileIngressPolicy | None = None,
    *,
    identity_resolver: IdentityResolver | None = None,
    cancellation_check: CancellationCheck | None = None,
    raise_errors: bool = False,
) -> SyncExecutionResult:
    """Execute one typed service command without synthesizing terminal arguments."""

    return incremental.execute_command(
        command,
        engine,
        ingress,
        identity_resolver=identity_resolver,
        cancellation_check=cancellation_check,
        raise_errors=raise_errors,
    )


def _render_result(result: SyncExecutionResult) -> None:
    """Preserve the legacy console transcript outside the sync kernel."""

    if result.committed:
        try:
            if result.output:
                print(result.output, end="")
        except BaseException:  # noqa: BLE001 - rendering cannot invalidate an immutable commit
            return
    elif result.output:
        print(result.output, end="")
    if result.error:
        print(result.error, end="", file=sys.stderr)


def run_sync(
    argv: Sequence[str],
    ingress: FileIngressPolicy | None = None,
    *,
    resolver_factory: ResolverFactory | None = None,
    cancellation_check: CancellationCheck | None = None,
    raise_errors: bool = False,
) -> int:
    """Run sync and render its compatibility transcript for console callers."""

    result = execute_sync(
        argv,
        ingress,
        resolver_factory=resolver_factory,
        cancellation_check=cancellation_check,
        raise_errors=raise_errors,
    )
    _render_result(result)
    return result.exit_code


__all__ = [
    "SOURCE_ID_RE",
    "SUPPORTED_VENDORS",
    "SyncCommand",
    "SyncExecutionResult",
    "SyncRebaseCommand",
    "SyncSnapshotInput",
    "SyncUpdateCommand",
    "execute_sync",
    "execute_sync_command",
    "run_sync",
]
