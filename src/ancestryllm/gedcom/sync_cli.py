"""Terminal argument translation for the supported GEDCOM sync façade."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import ModuleType
from typing import Never, Sequence

from ancestryllm.core.errors import AncestryError
from ancestryllm.core.ingress import (
    FileIngressPolicy,
)
from ancestryllm.gedcom.contracts import IdentityResolver
from ancestryllm.gedcom.sync_contracts import (
    CancellationCheck,
    ResolverFactory,
    SyncCommand,
    SyncError,
    SyncExecutionResult,
    SyncRebaseCommand,
    SyncUpdateCommand,
)
from ancestryllm.gedcom.sync_manifest import (
    _snapshot_inputs_from_arguments,
)
from ancestryllm.gedcom.sync_operations import (
    _execute_typed_command,
    _with_error_contract,
)


class PlainEnglishArgumentParser(argparse.ArgumentParser):
    """Convert argparse failures into the updater's stable error contract."""

    def error(self, message: str) -> Never:
        """Raise a remediable configuration error instead of exiting abruptly."""
        del message
        raise SyncError(
            "SYNC_CONFIGURATION",
            "The command-line options are not valid.",
            "The updater cannot safely infer missing paths or synchronization intent.",
            [f"Run `{self.prog} --help` and correct the listed option."],
        )


def _build_update_parser() -> argparse.ArgumentParser:
    """Return the incremental-update command parser."""
    parser = PlainEnglishArgumentParser(
        prog="gedcom_merge.py update",
        description="Update a master GEDCOM from versioned website snapshots.",
    )
    parser.add_argument("--master", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--initialize-manifest", action="store_true")
    parser.add_argument(
        "--snapshot",
        action="append",
        required=True,
        metavar="SOURCE_ID:VENDOR=PATH",
    )
    parser.add_argument(
        "--exported-at",
        action="append",
        metavar="SOURCE_ID=ISO8601",
    )
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--quality-root-person")
    parser.add_argument("--no-quality-report", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--provider",
        default="none",
        help="Explicit provider profile or built-in provider; none keeps update network-free.",
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--consent")
    parser.add_argument("--gedcom-version", choices=("5.5.5", "5.5.1"), default="5.5.5")
    parser.add_argument("--auto", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _build_rebase_parser() -> argparse.ArgumentParser:
    """Return the explicit external-master rebase parser."""
    parser = PlainEnglishArgumentParser(
        prog="gedcom_merge.py rebase",
        description="Adopt intentional external master edits as protected manual data.",
    )
    parser.add_argument("--master", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--accept-manual-deletions", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _update_command_from_namespace(args: argparse.Namespace) -> SyncUpdateCommand:
    """Translate terminal parser state into the shared typed update command."""

    return SyncUpdateCommand(
        master=Path(args.master),
        release_root=Path(args.release_root),
        provider=str(args.provider),
        snapshots=_snapshot_inputs_from_arguments(args.snapshot, args.exported_at),
        manifest=Path(args.manifest) if args.manifest else None,
        initialize_manifest=bool(args.initialize_manifest),
        quality_root_person=args.quality_root_person,
        no_quality_report=bool(args.no_quality_report),
        dry_run=bool(args.dry_run),
        gedcom_version=str(args.gedcom_version),
        auto=bool(args.auto),
    )


def _rebase_command_from_namespace(args: argparse.Namespace) -> SyncRebaseCommand:
    """Translate terminal parser state into the shared typed rebase command."""

    return SyncRebaseCommand(
        master=Path(args.master),
        manifest=Path(args.manifest),
        release_root=Path(args.release_root),
        reason=str(args.reason),
        accept_manual_deletions=bool(args.accept_manual_deletions),
        dry_run=bool(args.dry_run),
    )


def execute(
    argv: Sequence[str],
    core: ModuleType,
    ingress: FileIngressPolicy | None = None,
    *,
    resolver_factory: ResolverFactory | None = None,
    cancellation_check: CancellationCheck | None = None,
    raise_errors: bool = False,
) -> SyncExecutionResult:
    """Parse and dispatch ``update`` or ``rebase`` without terminal I/O."""

    command_name = argv[0] if argv else ""
    policy = ingress or FileIngressPolicy()

    def parse_and_execute() -> SyncExecutionResult:
        if command_name == "update":
            args = _build_update_parser().parse_args(list(argv[1:]))
            command: SyncCommand = _update_command_from_namespace(args)
            identity_resolver: IdentityResolver | None = None
            if args.provider != "none" and args.auto:
                if resolver_factory is None:
                    raise AncestryError(
                        "LLM_SERVICE_UNAVAILABLE",
                        "Incremental LLM assistance requires the modular application service.",
                    )
                identity_resolver = resolver_factory(
                    args.provider,
                    args.model,
                    args.consent,
                )
            return _execute_typed_command(
                command,
                core,
                policy,
                identity_resolver=identity_resolver,
                cancellation_check=cancellation_check,
            )
        if command_name == "rebase":
            args = _build_rebase_parser().parse_args(list(argv[1:]))
            command = _rebase_command_from_namespace(args)
            return _execute_typed_command(
                command,
                core,
                policy,
                identity_resolver=None,
                cancellation_check=cancellation_check,
            )
        raise SyncError(
            "SYNC_CONFIGURATION",
            f"Unknown incremental command: {command_name or '(missing)'}",
            "Only update and rebase have defined provenance behavior.",
            ["Use gedcom_merge.py update --help or rebase --help."],
        )

    return _with_error_contract(parse_and_execute, raise_errors=raise_errors)
