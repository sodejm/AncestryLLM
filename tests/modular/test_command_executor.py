"""Contracts for the shared CLI/REPL command-execution boundary."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import ancestryllm.cli as cli_module
import ancestryllm.console.shell as shell_module
from ancestryllm.application._secrets import SecretGrantRegistry
from ancestryllm.application.executor import (
    CommandArgument,
    CommandExecutor,
    CommandInvocation,
    CommandOutcome,
)
from ancestryllm.core.commands import COMMAND_SPECIFICATIONS, DispatchKey
from ancestryllm.core.errors import AncestryError
from ancestryllm.execution.runtime import create_command_executor
from ancestryllm.terminal.parser import build_parser, invocation_from_namespace

if TYPE_CHECKING:
    from ancestryllm.core.context import AppContext

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ancestryllm"
FORBIDDEN_EXECUTOR_IMPORTS = (
    "argparse",
    "click",
    "electron",
    "fastapi",
    "prompt_toolkit",
    "pydantic",
    "rich",
)


def test_invocation_rejects_duplicate_argument_names() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        CommandInvocation(
            DispatchKey("modules", "list"),
            (
                CommandArgument("name", "first"),
                CommandArgument("name", "second"),
            ),
        )


def test_executor_registry_rejects_duplicate_dispatch_keys() -> None:
    key = DispatchKey("modules", "list")

    def handler(_invocation: CommandInvocation) -> CommandOutcome:
        return CommandOutcome()

    with pytest.raises(ValueError, match="Duplicate command handler"):
        CommandExecutor(((key, handler), (key, handler)))


def test_executor_returns_stable_coded_error_for_unknown_route() -> None:
    executor = CommandExecutor(())

    with pytest.raises(AncestryError) as raised:
        executor.execute(CommandInvocation(DispatchKey("unknown", "action")))

    assert raised.value.code == "COMMAND_UNKNOWN"
    assert raised.value.exit_code == 2
    assert raised.value.details == {}


def test_runtime_registry_covers_every_declared_command_route_once(
    app_context: AppContext,
) -> None:
    expected = tuple(
        sorted(
            route.key
            for specification in COMMAND_SPECIFICATIONS.values()
            for route in specification.routes
        )
    )

    executor = create_command_executor(app_context, SecretGrantRegistry())

    assert executor.dispatch_keys == expected
    assert len(executor.dispatch_keys) == len(set(executor.dispatch_keys))


def test_terminal_translation_removes_host_path_objects() -> None:
    namespace = build_parser().parse_args(
        [
            "gedcom",
            "merge",
            "first.ged",
            "second.ged",
            "--output",
            "merged.ged",
        ]
    )

    invocation = invocation_from_namespace(namespace)
    arguments = {argument.name: argument.value for argument in invocation.arguments}

    assert arguments["inputs"] == ("first.ged", "second.ged")
    assert arguments["output"] == "merged.ged"
    assert not any(
        isinstance(value, Path)
        or (isinstance(value, tuple) and any(isinstance(item, Path) for item in value))
        for value in arguments.values()
    )


def test_terminal_translation_rejects_mismatched_dispatch_metadata() -> None:
    namespace = argparse.Namespace(
        command="modules",
        action="list",
        dispatch_key=DispatchKey("modules", "enable"),
        config=None,
        json=False,
    )

    with pytest.raises(AncestryError) as raised:
        invocation_from_namespace(namespace)

    assert raised.value.code == "COMMAND_METADATA_INVALID"
    assert raised.value.exit_code == 2


def test_secret_capabilities_are_scoped_single_use_and_revocable() -> None:
    registry = SecretGrantRegistry()
    first = registry.issue("openai.api_key", "fictional-one-time-value")

    assert registry.consume(first, "openai.api_key") == "fictional-one-time-value"
    with pytest.raises(AncestryError, match="missing, expired, or out of scope") as reused:
        registry.consume(first, "openai.api_key")
    assert reused.value.code == "SECRET_GRANT_INVALID"

    second = registry.issue("anthropic.api_key", "fictional-revoked-value")
    registry.revoke_all()
    with pytest.raises(AncestryError) as revoked:
        registry.consume(second, "anthropic.api_key")
    assert revoked.value.code == "SECRET_GRANT_INVALID"


def test_secret_value_never_enters_the_transport_neutral_invocation() -> None:
    registry = SecretGrantRegistry()
    grant = registry.issue("openai.api_key", "fictional-write-only-value")
    namespace = build_parser().parse_args(["secrets", "set", "openai.api_key"])

    invocation = invocation_from_namespace(namespace, secret_grant=grant)

    assert invocation.secret_grant == grant
    assert "fictional-write-only-value" not in repr(invocation)
    assert all(argument.value != "fictional-write-only-value" for argument in invocation.arguments)


def test_cli_and_repl_share_the_same_terminal_dispatch_function() -> None:
    assert cli_module._terminal_dispatch is shell_module.dispatch

    cli_tree = ast.parse((PACKAGE_ROOT / "cli.py").read_text(encoding="utf-8"))
    console_trees = (
        ast.parse(path.read_text(encoding="utf-8"))
        for path in (PACKAGE_ROOT / "console").glob("*.py")
    )
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("ancestryllm.console")
        for node in ast.walk(cli_tree)
    )
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("ancestryllm.cli")
        for tree in console_trees
        for node in ast.walk(tree)
    )


def test_focused_executors_have_no_transport_frameworks_or_print_calls() -> None:
    violations: list[str] = []
    for path in sorted((PACKAGE_ROOT / "execution").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations.extend(
                    f"{path.name}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith(FORBIDDEN_EXECUTOR_IMPORTS)
                )
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module.startswith(FORBIDDEN_EXECUTOR_IMPORTS):
                    violations.append(f"{path.name}: from {node.module}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                violations.append(f"{path.name}: print")

    assert violations == []
