"""Argument translation helpers shared by focused command-family handlers."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Final, TypeVar, cast
from uuid import UUID

from ancestryllm.application.dto import JSONValue
from ancestryllm.application.executor import CommandInvocation
from ancestryllm.application.results import StructuredResult
from ancestryllm.core.commands import ModuleDescriptor
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.llm.policy import ConsentGrant

_MISSING: Final = object()
_T = TypeVar("_T", str, int, bool)


def _typed(
    invocation: CommandInvocation,
    name: str,
    expected: type[_T],
    default: _T | object = _MISSING,
) -> _T:
    value = invocation.argument(name, default)
    if expected is int:
        valid = isinstance(value, int) and not isinstance(value, bool)
    else:
        valid = isinstance(value, expected)
    if not valid:
        raise AncestryError(
            "ARGUMENT_INVALID",
            f"Command argument {name!r} must be {expected.__name__}.",
            exit_code=2,
        )
    return cast(_T, value)


def text(
    invocation: CommandInvocation,
    name: str,
    default: str | object = _MISSING,
) -> str:
    return _typed(invocation, name, str, default)


def optional_text(invocation: CommandInvocation, name: str) -> str | None:
    value = invocation.argument(name, None)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AncestryError(
            "ARGUMENT_INVALID",
            f"Command argument {name!r} must be text or null.",
            exit_code=2,
        )
    return value


def integer(
    invocation: CommandInvocation,
    name: str,
    default: int | object = _MISSING,
) -> int:
    return _typed(invocation, name, int, default)


def optional_integer(invocation: CommandInvocation, name: str) -> int | None:
    value = invocation.argument(name, None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AncestryError(
            "ARGUMENT_INVALID",
            f"Command argument {name!r} must be an integer or null.",
            exit_code=2,
        )
    return value


def number(invocation: CommandInvocation, name: str) -> float | None:
    value = invocation.argument(name, None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AncestryError(
            "ARGUMENT_INVALID",
            f"Command argument {name!r} must be a number or null.",
            exit_code=2,
        )
    return float(value)


def boolean(
    invocation: CommandInvocation,
    name: str,
    default: bool | object = _MISSING,
) -> bool:
    return _typed(invocation, name, bool, default)


def text_values(
    invocation: CommandInvocation,
    name: str,
    default: tuple[str, ...] | object = _MISSING,
) -> tuple[str, ...]:
    value = invocation.argument(name, default)
    if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
        raise AncestryError(
            "ARGUMENT_INVALID",
            f"Command argument {name!r} must be a text sequence.",
            exit_code=2,
        )
    return value


def path(invocation: CommandInvocation, name: str) -> Path:
    return Path(text(invocation, name))


def optional_path(invocation: CommandInvocation, name: str) -> Path | None:
    value = optional_text(invocation, name)
    return Path(value) if value is not None else None


def key_values(values: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise AncestryError("ARGUMENT_INVALID", f"Expected NAME=VALUE, received {raw!r}.")
        name, value = raw.split("=", 1)
        result[name] = value
    return result


def consent(context: AppContext, name: str | None) -> ConsentGrant | None:
    return context.provider_profiles.consent_grant(name) if name else None


def descriptor_payload(descriptor: ModuleDescriptor) -> dict[str, object]:
    """Preserve the established modules-list JSON contract."""

    return {
        "module_id": descriptor.module_id,
        "name": descriptor.name,
        "summary": descriptor.summary,
        "actions": descriptor.actions,
        "implementation": descriptor.implementation,
        "configuration": descriptor.configuration,
        "required_services": descriptor.required_services,
    }


def _serializable_value(value: object) -> object:
    """Normalize established service values before they cross the command boundary."""

    if is_dataclass(value):
        return {
            str(key): _serializable_value(item) for key, item in asdict(cast(Any, value)).items()
        }
    if isinstance(value, Path | UUID):
        return str(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return _serializable_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _serializable_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serializable_value(item) for item in value]
    table = getattr(value, "__table__", None)
    columns = getattr(table, "columns", None)
    if columns is not None:
        return {
            str(column.name): _serializable_value(getattr(value, column.name)) for column in columns
        }
    return value


def structured_result(value: object) -> StructuredResult:
    """Declare a strict structured result for a legacy service return value."""

    return StructuredResult(cast(JSONValue, _serializable_value(value)))


__all__ = [
    "boolean",
    "consent",
    "descriptor_payload",
    "integer",
    "key_values",
    "number",
    "optional_integer",
    "optional_path",
    "optional_text",
    "path",
    "structured_result",
    "text",
    "text_values",
]
