"""Verify stable transport-neutral command specifications and derived CLI help."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ancestryllm import cli
from ancestryllm.console.parser import parse_repl_invocation
from ancestryllm.core import modules as legacy_modules
from ancestryllm.core.commands import (
    BUILTIN_MODULES,
    COMMAND_SPECIFICATIONS,
    ArgumentCardinality,
    ArgumentType,
    CompletionKind,
    DispatchKey,
)
from ancestryllm.core.context import AppContext
from ancestryllm.core.modules import ModuleRegistry


def _argument(command: str, action: str, name: str):
    action_spec = next(
        item for item in COMMAND_SPECIFICATIONS[command].actions if item.name == action
    )
    return next(item for item in action_spec.arguments if item.name == name)


def test_builtin_descriptors_derive_actions_from_transport_neutral_specs() -> None:
    assert set(BUILTIN_MODULES) < set(COMMAND_SPECIFICATIONS)
    for module_id, descriptor in BUILTIN_MODULES.items():
        assert descriptor.command is COMMAND_SPECIFICATIONS[module_id]
        assert descriptor.actions == tuple(action.name for action in descriptor.command.actions)

    output = _argument("gedcom", "merge", "output")
    assert output.flags == ("--output", "-o")
    assert output.value_type is ArgumentType.PATH
    assert output.required is True
    assert output.sensitive is True
    assert output.completion is CompletionKind.FILE

    inputs = _argument("gedcom", "merge", "inputs")
    assert inputs.positional is True
    assert inputs.cardinality is ArgumentCardinality.ONE_OR_MORE

    profile = _argument("rootsmagic", "export", "profile")
    assert profile.default == "portable"
    assert profile.choices == ("portable", "preservation")
    assert profile.help


def test_legacy_core_modules_exports_retain_020_contract_identity() -> None:
    assert legacy_modules.COMMAND_SPECIFICATIONS is COMMAND_SPECIFICATIONS
    assert legacy_modules.BUILTIN_MODULES is BUILTIN_MODULES
    assert legacy_modules.DispatchKey is DispatchKey


def test_command_specs_expose_one_complete_stable_dispatch_registry() -> None:
    routes = [
        route for specification in COMMAND_SPECIFICATIONS.values() for route in specification.routes
    ]
    assert len(routes) == sum(
        len(specification.actions) for specification in COMMAND_SPECIFICATIONS.values()
    )
    assert len({route.key for route in routes}) == len(routes)
    assert all(route.key.value == f"{route.key.command}.{route.key.action}" for route in routes)
    assert COMMAND_SPECIFICATIONS["gedcom"].route("merge").key == DispatchKey("gedcom", "merge")
    with pytest.raises(KeyError, match="missing"):
        COMMAND_SPECIFICATIONS["gedcom"].route("missing")


def test_cli_and_repl_parsers_attach_the_same_dispatch_key() -> None:
    tokens = ["gedcom", "merge", "a.ged", "b.ged", "--output", "merged.ged"]
    cli_namespace = cli.build_parser().parse_args(tokens)
    repl_namespace = parse_repl_invocation(tokens).namespace
    expected = DispatchKey("gedcom", "merge")
    assert cli_namespace.dispatch_key == expected
    assert repl_namespace.dispatch_key == expected


def test_importing_command_specs_does_not_load_ui_or_web_frameworks() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import ancestryllm.core.commands; "
                "forbidden=('argparse','click','prompt_toolkit','rich','fastapi','pydantic'); "
                "loaded=sorted(name for name in sys.modules "
                "if name.split('.', 1)[0] in forbidden); "
                "assert not loaded, loaded"
            ),
        ],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_build_parser_preserves_argument_types_defaults_and_groups() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["gedcom", "merge", "a.ged", "b.ged", "-o", "merged.ged"])
    assert args.inputs == [Path("a.ged"), Path("b.ged")]
    assert args.output == Path("merged.ged")
    assert args.gedcom_version == "5.5.5"
    assert args.provider == "none"
    assert args.model == ""
    assert args.similarity_threshold == 78

    prompt_args = parser.parse_args(
        ["prompts", "save", "timeline", "--purpose", "research", "--body", "Hello"]
    )
    assert prompt_args.variable == []
    assert prompt_args.tag == []

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "rootsmagic",
                "query",
                "--tree",
                "sample",
                "--sql",
                "select 1",
                "--question",
                "Who?",
            ]
        )


def test_cli_help_is_rendered_from_command_specifications(capsys) -> None:
    specification = COMMAND_SPECIFICATIONS["rootsmagic"]
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args(["rootsmagic", "--help"])
    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    for action in specification.actions:
        assert action.name in help_text
        assert action.help in help_text

    export = next(action for action in specification.actions if action.name == "export")
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["rootsmagic", "export", "--help"])
    help_text = capsys.readouterr().out
    for argument in export.arguments:
        assert argument.help in help_text


def test_cli_help_explains_console_entrypoint_and_action_safe_example(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args(["--help"])
    assert raised.value.code == 0
    root_help = capsys.readouterr().out
    assert "Run `ancestry` with no arguments to start the interactive console." in root_help
    assert "ancestry --json database diagnose" in root_help

    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args(["gedcom", "merge", "--help"])
    assert raised.value.code == 0
    action_help = capsys.readouterr().out
    assert "INPUTS" in action_help
    assert "--output" in action_help
    assert "default: 5.5.5" in action_help
    assert "5.5.1" in action_help
    assert "Example: ancestry gedcom merge input.ged --output output.ged" in action_help


def test_reading_specs_and_building_help_do_not_load_disabled_modules(
    app_context: AppContext, monkeypatch
) -> None:
    del monkeypatch
    app_context.config.enabled_modules = {"gedcom"}
    registry = ModuleRegistry(app_context)
    assert [descriptor.command.name for descriptor in registry.descriptors()] == ["gedcom"]
    cli.build_parser().format_help()


def test_modules_json_keeps_legacy_descriptor_shape(app_context: AppContext, capsys) -> None:
    assert cli.main(["--json", "modules", "list"], app_context) == 0
    modules = json.loads(capsys.readouterr().out)
    gedcom = next(module for module in modules if module["module_id"] == "gedcom")
    assert set(gedcom) == {
        "module_id",
        "name",
        "summary",
        "actions",
        "implementation",
        "configuration",
        "required_services",
    }
    assert gedcom["actions"] == ["merge", "subtree", "quality", "sync"]


def test_cli_reference_includes_read_only_database_diagnostics() -> None:
    documentation = (Path(__file__).parents[2] / "docs" / "CLI.md").read_text(encoding="utf-8")

    assert "`database` | `backup DESTINATION`, `diagnose`" in documentation
    assert "`database diagnose`" in documentation
    assert "[setup diagnostics](SETUP_DIAGNOSTICS.md)" in documentation
