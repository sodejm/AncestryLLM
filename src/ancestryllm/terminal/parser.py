"""Generated terminal parsing shared by the CLI and REPL adapters."""

from __future__ import annotations

import argparse
import contextlib
import io
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ancestryllm import __version__
from ancestryllm.application.dto import SecretGrantRef
from ancestryllm.application.executor import CommandArgument, CommandInvocation, CommandValue
from ancestryllm.core.commands import (
    COMMAND_SPECIFICATIONS,
    GLOBAL_ARGUMENTS,
    ActionSpec,
    ArgumentAction,
    ArgumentCardinality,
    ArgumentSpec,
    ArgumentType,
    DispatchKey,
)
from ancestryllm.core.errors import AncestryError

_ARGUMENT_TYPES: dict[ArgumentType, type[str] | type[int] | type[float] | type[Path]] = {
    ArgumentType.STRING: str,
    ArgumentType.INTEGER: int,
    ArgumentType.NUMBER: float,
    ArgumentType.PATH: Path,
}

_ARGUMENT_CARDINALITIES: dict[ArgumentCardinality, str] = {
    ArgumentCardinality.OPTIONAL: "?",
    ArgumentCardinality.ONE_OR_MORE: "+",
    ArgumentCardinality.REMAINDER: argparse.REMAINDER,
}

_CONTROL_FIELDS = frozenset({"command", "action", "config", "json", "dispatch_key"})


def _add_argument(target: Any, specification: ArgumentSpec) -> None:
    names = specification.flags or (specification.name,)
    options: dict[str, Any] = {"help": specification.help}
    if specification.action is not ArgumentAction.STORE:
        options["action"] = specification.action.value
    else:
        options["type"] = _ARGUMENT_TYPES[specification.value_type]
    if specification.required and specification.flags:
        options["required"] = True
    if specification.default is not None or specification.action is ArgumentAction.STORE_TRUE:
        options["default"] = (
            list(specification.default)
            if isinstance(specification.default, tuple)
            else specification.default
        )
    if specification.choices:
        options["choices"] = specification.choices
    if specification.cardinality is not None:
        options["nargs"] = _ARGUMENT_CARDINALITIES[specification.cardinality]
    if specification.metavar is not None:
        options["metavar"] = specification.metavar
    target.add_argument(*names, **options)


def _add_action_arguments(parser: argparse.ArgumentParser, specification: ActionSpec) -> None:
    grouped_arguments: dict[str, Any] = {}
    for group in specification.exclusive_groups:
        target = parser.add_mutually_exclusive_group(required=group.required)
        for argument_name in group.arguments:
            grouped_arguments[argument_name] = target
    for argument in specification.arguments:
        _add_argument(grouped_arguments.get(argument.name, parser), argument)


def build_parser() -> argparse.ArgumentParser:
    """Build the single parser generated from the shared command specifications."""

    parser = argparse.ArgumentParser(
        prog="ancestry",
        description="Unified one-shot command line and entry point for the interactive console.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    for argument in GLOBAL_ARGUMENTS:
        _add_argument(parser, argument)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in COMMAND_SPECIFICATIONS.values():
        command_parser = commands.add_parser(command.name, help=command.help)
        actions = command_parser.add_subparsers(dest="action", required=True)
        for route in command.routes:
            action_parser = actions.add_parser(route.action.name, help=route.action.help)
            action_parser.set_defaults(dispatch_key=route.key)
            _add_action_arguments(action_parser, route.action)
    return parser


def _normalize_value(value: object) -> CommandValue:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if isinstance(item, Path):
                normalized.append(str(item))
            elif isinstance(item, str):
                normalized.append(item)
            else:
                raise AncestryError(
                    "ARGUMENT_INVALID",
                    "Command argument sequences may contain text or path values only.",
                    exit_code=2,
                )
        return tuple(normalized)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise AncestryError(
        "ARGUMENT_INVALID",
        f"Unsupported parsed command argument type: {type(value).__name__}.",
        exit_code=2,
    )


def invocation_from_namespace(
    namespace: argparse.Namespace,
    *,
    secret_grant: SecretGrantRef | None = None,
) -> CommandInvocation:
    """Translate parser state into a transport-neutral application invocation."""

    command = getattr(namespace, "command", None)
    action = getattr(namespace, "action", None)
    if not isinstance(command, str) or not isinstance(action, str):
        raise AncestryError("COMMAND_UNKNOWN", "Unknown command.", exit_code=2)
    expected_key = DispatchKey(command, action)
    parsed_key = getattr(namespace, "dispatch_key", expected_key)
    if parsed_key != expected_key:
        raise AncestryError(
            "COMMAND_METADATA_INVALID",
            "Parsed command metadata does not match the selected action.",
            exit_code=2,
        )
    arguments = tuple(
        CommandArgument(name, _normalize_value(value))
        for name, value in sorted(vars(namespace).items())
        if name not in _CONTROL_FIELDS
    )
    return CommandInvocation(
        key=expected_key,
        arguments=arguments,
        json_output=bool(getattr(namespace, "json", False)),
        secret_grant=secret_grant,
    )


@dataclass(frozen=True, slots=True)
class ParsedInvocation:
    """A validated terminal invocation produced from interactive input."""

    tokens: tuple[str, ...]
    namespace: argparse.Namespace


def _reject_shell_syntax(command: str) -> None:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character in {"|", ";", "&", "<", ">", "`", "\n", "\r"} or character == "$":
            raise AncestryError(
                "REPL_SHELL_SYNTAX_REJECTED",
                f"Shell syntax is not supported at character {index + 1}.",
                "Enter an AncestryLLM command directly; pipes, redirects, expansion, and scripts are disabled.",
                exit_code=2,
            )


def split_repl_input(command: str) -> tuple[str, ...]:
    """Split one interactive line without expansion or shell execution."""

    _reject_shell_syntax(command)
    try:
        return tuple(shlex.split(command, comments=False, posix=True))
    except ValueError as exc:
        raise AncestryError(
            "REPL_PARSE_ERROR",
            "The command contains an incomplete quote or escape sequence.",
            "Close the quoted value or remove the trailing escape character.",
            exit_code=2,
        ) from exc


def parse_repl_invocation(tokens: Sequence[str]) -> ParsedInvocation:
    """Validate tokens with the same generated parser used by the one-shot CLI."""

    normalized_tokens = list(tokens)
    if "--json" in normalized_tokens[1:]:
        normalized_tokens.remove("--json")
        normalized_tokens.insert(0, "--json")
    parser = build_parser()
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            namespace = parser.parse_args(normalized_tokens)
    except SystemExit as exc:
        rendered = (stderr.getvalue() or stdout.getvalue()).strip()
        detail = rendered.splitlines()[-1] if rendered else "Invalid command arguments."
        raise AncestryError(
            "REPL_USAGE_ERROR",
            detail,
            "Use `help` or inspect the one-shot command help for the accepted arguments.",
            exit_code=2 if exc.code else 0,
        ) from exc
    return ParsedInvocation(tuple(normalized_tokens), namespace)


__all__ = [
    "ParsedInvocation",
    "build_parser",
    "invocation_from_namespace",
    "parse_repl_invocation",
    "split_repl_input",
]
