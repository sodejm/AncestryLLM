from __future__ import annotations

import json
from pathlib import Path

import pytest

from ancestryllm import cli
from ancestryllm.core.context import AppContext
from ancestryllm.core.modules import (
    BUILTIN_MODULES,
    COMMAND_SPECIFICATIONS,
    ArgumentCardinality,
    ArgumentType,
    CompletionKind,
    ModuleRegistry,
)


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
