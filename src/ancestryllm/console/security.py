"""Shared interactive-console security helpers without UI dependencies."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from typing import Any, TextIO

from ancestryllm.core.modules import COMMAND_SPECIFICATIONS

SECRET_NAME_MARKERS = (
    "api_key",
    "apikey",
    "access_key",
    "authorization",
    "credential",
    "passphrase",
    "passwd",
    "password",
    "private_key",
    "secret",
    "token",
)
_CREDENTIAL_URL = re.compile(r"://[^\s/@:]+:([^\s/@]+)@")


def is_secret_name(name: str) -> bool:
    """Return whether a name could identify credential material."""

    normalized = name.casefold().replace("-", "_").replace(".", "_")
    return any(marker in normalized for marker in SECRET_NAME_MARKERS)


def split_command_safely(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def credential_values(command: str) -> list[str]:
    """Extract accidentally supplied credentials so output can redact them."""

    tokens = split_command_safely(command)
    values: list[str] = []
    if tokens and tokens[0].casefold() == "secrets":
        if len(tokens) > 3 and tokens[1].casefold() == "set":
            values.extend(tokens[3:])
        return [value for value in values if value]
    if len(tokens) > 2 and tokens[0].casefold() == "set" and is_secret_name(tokens[1]):
        values.extend(tokens[2:])
    for index, token in enumerate(tokens[1:], start=1):
        name, separator, value = token.partition("=")
        if separator and is_secret_name(name):
            values.append(value)
        elif is_secret_name(token.lstrip("-")) and index + 1 < len(tokens):
            candidate = tokens[index + 1]
            if not candidate.startswith("-"):
                values.append(candidate)
        if token.casefold() == "bearer" and index + 1 < len(tokens):
            values.append(tokens[index + 1])
    values.extend(match.group(1) for match in _CREDENTIAL_URL.finditer(command))
    return [value for value in values if value]


def history_is_sensitive(command: str, active_module: str | None = None) -> bool:
    """Return whether an interactive line must stay out of all history."""

    if "\n" in command or "\r" in command:
        return True
    tokens = split_command_safely(command)
    if not tokens:
        return False
    if tokens[0].casefold() == "secrets" or _CREDENTIAL_URL.search(command):
        return True
    if any(token.casefold() == "bearer" for token in tokens):
        return True
    if any(is_secret_name(token.partition("=")[0].lstrip("-")) for token in tokens[1:]):
        return True

    if tokens[0].casefold() == "set" and active_module and len(tokens) > 2:
        name = tokens[1].replace("-", "_").lstrip("_")
        for candidate_action in COMMAND_SPECIFICATIONS[active_module].actions:
            for argument in candidate_action.arguments:
                flags = {flag.lstrip("-").replace("-", "_") for flag in argument.flags}
                if argument.sensitive and (name == argument.name or name in flags):
                    return True

    module_id: str | None = None
    action_name: str | None = None
    arguments: list[str] = []
    if tokens[0] in COMMAND_SPECIFICATIONS and len(tokens) > 1:
        module_id, action_name, arguments = tokens[0], tokens[1], tokens[2:]
    elif tokens[0].casefold() == "run" and active_module and len(tokens) > 1:
        module_id, action_name, arguments = active_module, tokens[1], tokens[2:]
    if module_id and action_name:
        action = next(
            (
                item
                for item in COMMAND_SPECIFICATIONS[module_id].actions
                if item.name == action_name
            ),
            None,
        )
        if action is not None:
            sensitive_flags = {
                flag
                for argument in action.arguments
                if argument.sensitive
                for flag in argument.flags
            }
            if sensitive_flags.intersection(arguments):
                return True
            if any(argument.sensitive and argument.positional for argument in action.arguments):
                positional_values = [value for value in arguments if not value.startswith("-")]
                if positional_values:
                    return True
    return False


class RedactingTextIO:
    """Scrub registered sensitive values immediately before terminal output."""

    def __init__(self, stream: TextIO, context: Any) -> None:
        self._stream = stream
        self._context = context

    @property
    def encoding(self) -> str:
        return self._stream.encoding or "utf-8"

    @property
    def errors(self) -> str | None:
        return self._stream.errors

    def fileno(self) -> int:
        return self._stream.fileno()

    def flush(self) -> None:
        if not self._stream.closed:
            self._stream.flush()

    def isatty(self) -> bool:
        return self._stream.isatty()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        return self._stream.write(self._context.secrets.redact(text))


def redact_object(value: Any, redact: Any) -> Any:
    """Recursively redact values before presentation."""

    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {
            redact_object(key, redact): redact_object(item, redact) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_object(item, redact) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_object(item, redact) for item in value)
    if isinstance(value, (set, frozenset)):
        return type(value)(redact_object(item, redact) for item in value)
    return value
