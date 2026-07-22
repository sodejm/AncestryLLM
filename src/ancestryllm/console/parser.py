"""Strict, non-shell parsing for interactive commands."""

from __future__ import annotations

import argparse
import contextlib
import io
import shlex
from dataclasses import dataclass
from typing import Sequence

from ancestryllm.core.errors import AncestryError


@dataclass(frozen=True, slots=True)
class ParsedInvocation:
    """A validated CLI invocation produced from interactive input."""

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

    from ancestryllm.cli import build_parser

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
