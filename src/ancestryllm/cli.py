"""Unified one-shot command line and entry point for the interactive console."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ancestryllm.core.config import AppConfig
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.execution.common import descriptor_payload
from ancestryllm.terminal.dispatch import dispatch as _terminal_dispatch
from ancestryllm.terminal.entrypoint import run_repl
from ancestryllm.terminal.parser import build_parser
from ancestryllm.terminal.presentation import PresentationAdapter

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ancestryllm.application.results import CommandResult
    from ancestryllm.core.commands import ModuleDescriptor

__all__ = ["build_parser", "dispatch", "main", "run_tokens"]


def _descriptor_payload(descriptor: ModuleDescriptor) -> dict[str, object]:
    """Preserve the established modules-list JSON contract."""

    return descriptor_payload(descriptor)


def _emit(result: CommandResult, json_output: bool = False) -> None:
    PresentationAdapter().render(result, json_output=json_output)


def dispatch(
    args: argparse.Namespace,
    context: AppContext,
    *,
    emit: Callable[[CommandResult, bool], None] = _emit,
) -> int:
    """Preserve the shipped CLI seam while delegating to the shared executor."""

    secret_value: str | None = None
    if args.command == "secrets" and args.action == "set":
        secret_value = getpass.getpass(f"Secret value for {args.name}: ")
        context.secrets.register_sensitive(secret_value)
        confirmation = getpass.getpass("Confirm secret value: ")
        context.secrets.register_sensitive(confirmation)
        if secret_value != confirmation:
            raise AncestryError(
                "SECRET_CONFIRMATION_FAILED",
                "Secret values did not match.",
            )
    return _terminal_dispatch(
        args,
        context,
        emit=emit,
        secret_value=secret_value,
    )


def run_tokens(context: AppContext, tokens: Sequence[str]) -> int:
    """Execute parsed CLI tokens through the canonical command boundary."""
    parser = build_parser()
    args = parser.parse_args(list(tokens))
    return dispatch(args, context)


def main(argv: Sequence[str] | None = None, context: AppContext | None = None) -> int:
    """Run the CLI command and return its exit status."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        repl_options: argparse.Namespace | None = argparse.Namespace(config=None, json=False)
    else:
        repl_parser = argparse.ArgumentParser(add_help=False)
        repl_parser.add_argument("--config", type=Path)
        repl_parser.add_argument("--json", action="store_true")
        candidate, remaining = repl_parser.parse_known_args(arguments)
        repl_options = candidate if not remaining else None
    parser = build_parser()
    selected_context: AppContext | None = None
    owns_context = False
    result = 2
    unhandled_error = False
    try:
        args = repl_options or parser.parse_args(arguments)
        if repl_options is not None and args.json:
            raise AncestryError(
                "ARGUMENT_INVALID",
                "--json requires a one-shot command.",
                exit_code=2,
            )
        if context is None:
            selected_context = AppContext.build(
                AppConfig.load(args.config) if args.config else None
            )
            owns_context = True
        else:
            selected_context = context
        if repl_options is not None:
            result = run_repl(selected_context)
        else:
            result = dispatch(args, selected_context)
    except AncestryError as exc:
        PresentationAdapter.for_file(sys.stderr).render_error(exc)
        result = exc.exit_code
    except (OSError, OverflowError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        PresentationAdapter.for_file(sys.stderr).render_error(
            AncestryError(
                "INPUT_ERROR",
                "The command input could not be processed safely.",
                exit_code=2,
                details={"error_type": type(exc).__name__},
            )
        )
        result = 2
    except BaseException:
        unhandled_error = True
        raise
    finally:
        if owns_context and selected_context is not None:
            try:
                selected_context.close()
            except Exception as exc:  # noqa: BLE001 - terminal boundary must sanitize cleanup
                if not unhandled_error:
                    PresentationAdapter.for_file(sys.stderr).render_error(
                        AncestryError(
                            "INPUT_ERROR",
                            "The application could not shut down cleanly.",
                            exit_code=2,
                            details={"error_type": type(exc).__name__},
                        )
                    )
                    result = 2
    return result
