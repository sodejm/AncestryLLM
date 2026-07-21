"""Contract tests for the UI-independent REPL parser and session router."""

from __future__ import annotations

import subprocess
import sys

import pytest

from ancestryllm.cli import build_parser
from ancestryllm.console.parser import parse_repl_invocation, split_repl_input
from ancestryllm.console.router import RouteKind, SessionRouter
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError


def _error_code(callable_object, *args: object) -> str:
    with pytest.raises(AncestryError) as raised:
        callable_object(*args)
    return raised.value.code


def test_parser_preserves_quotes_escapes_name_values_repeated_flags_and_booleans() -> None:
    command = (
        'prompts render "fictional timeline" '
        "--value person=Ada\\ Example --value 'place=Fiction County'"
    )
    tokens = split_repl_input(command)
    assert tokens == (
        "prompts",
        "render",
        "fictional timeline",
        "--value",
        "person=Ada Example",
        "--value",
        "place=Fiction County",
    )

    parsed = parse_repl_invocation(tokens)
    expected = build_parser().parse_args(list(tokens))
    assert parsed.namespace == expected
    assert parsed.namespace.value == ["person=Ada Example", "place=Fiction County"]

    boolean_tokens = split_repl_input(
        "providers consent fictional --profile fictional-profile --module gedcom "
        "--purpose fictional-evaluation --data-class public_genealogy "
        "--model fictional-local-model --retain-payloads"
    )
    assert parse_repl_invocation(boolean_tokens).namespace.retain_payloads is True


def test_parser_namespace_matches_one_shot_for_quoted_paths_and_repeated_flags() -> None:
    command = (
        "gedcom subtree fictional\\ input.ged --output 'fictional output.ged' "
        '--root-person "Ada Example" --generations 3'
    )
    tokens = split_repl_input(command)
    repl_namespace = parse_repl_invocation(tokens).namespace
    one_shot_namespace = build_parser().parse_args(list(tokens))

    assert repl_namespace == one_shot_namespace
    assert str(repl_namespace.input) == "fictional input.ged"
    assert str(repl_namespace.output) == "fictional output.ged"
    assert repl_namespace.root_person == "Ada Example"
    assert repl_namespace.generations == 3


@pytest.mark.parametrize(
    "command",
    (
        "gedcom merge fictional.ged --output result.ged | fictional-command",
        "gedcom merge fictional.ged --output result.ged; fictional-command",
        "gedcom merge fictional.ged --output result.ged > result.txt",
        "gedcom merge fictional.ged --output result.ged $(fictional-command)",
        "gedcom merge fictional.ged --output result.ged\nmodules",
    ),
)
def test_parser_rejects_shell_syntax(command: str) -> None:
    assert _error_code(split_repl_input, command) == "REPL_SHELL_SYNTAX_REJECTED"


@pytest.mark.parametrize(
    "command",
    (
        'gedcom subtree "fictional input.ged',
        "gedcom subtree fictional\\",
    ),
)
def test_parser_reports_malformed_quotes_or_escapes(command: str) -> None:
    assert _error_code(split_repl_input, command) == "REPL_PARSE_ERROR"


def test_router_transitions_between_root_and_active_module(app_context: AppContext) -> None:
    router = SessionRouter(app_context)

    assert router.prompt == "ancestry > "
    assert _error_code(router.route, "info") == "REPL_MODULE_REQUIRED"
    assert router.route("use gedcom").value == "Using module: gedcom"
    assert router.prompt == "ancestry(gedcom) > "

    info = router.route("info")
    assert info.kind is RouteKind.OUTPUT
    assert info.value["module_id"] == "gedcom"
    assert "subtree" in info.value["actions"]
    assert router.route("show actions").value == info.value["actions"]

    assert router.route("back").value == "Returned to the root prompt."
    assert router.active_module is None
    assert router.module_options == {}
    assert router.prompt == "ancestry > "


def test_router_set_unset_and_run_merges_options_with_explicit_arguments(
    app_context: AppContext,
) -> None:
    router = SessionRouter(app_context)
    router.route("use gedcom")
    router.route("set action subtree")
    router.route('set output "fictional output.ged"')
    router.route('set root-person "Ada Example"')
    router.route("set generations 3")
    router.route("unset generations")

    result = router.route("run subtree fictional\\ input.ged --scope descendants")

    assert result.kind is RouteKind.EXECUTE
    assert result.invocation is not None
    assert result.invocation.tokens == (
        "gedcom",
        "subtree",
        "--output",
        "fictional output.ged",
        "--root-person",
        "Ada Example",
        "fictional input.ged",
        "--scope",
        "descendants",
    )
    assert result.invocation.namespace.scope == "descendants"
    assert result.invocation.namespace.generations is None
    assert router.route("show options").value == {
        "action": "subtree",
        "output": "fictional output.ged",
        "root_person": "Ada Example",
    }


def test_router_run_translates_boolean_option_to_flag(app_context: AppContext) -> None:
    router = SessionRouter(app_context)
    router.route("use providers")
    router.route("set profile fictional-profile")
    router.route("set module gedcom")
    router.route("set purpose fictional-evaluation")
    router.route("set data-class public_genealogy")
    router.route("set model fictional-local-model")
    router.route("set retain-payloads true")

    result = router.route("run consent fictional-consent")

    assert result.kind is RouteKind.EXECUTE
    assert result.invocation is not None
    assert "--retain-payloads" in result.invocation.tokens
    assert result.invocation.namespace.retain_payloads is True
    assert result.invocation.namespace.name == "fictional-consent"
    assert result.invocation.namespace.module == ["gedcom"]


def test_router_rejects_disabled_modules_unknown_actions_options_and_secret_names(
    app_context: AppContext,
) -> None:
    app_context.config.enabled_modules = {"gedcom"}
    router = SessionRouter(app_context)

    assert _error_code(router.route, "use prompts") == "MODULE_DISABLED"
    assert _error_code(router.route, "prompts list") == "MODULE_DISABLED"
    router.route("use gedcom")
    assert _error_code(router.route, "set action fictional-action") == "REPL_ACTION_UNKNOWN"
    assert (
        _error_code(router.route, "set fictional-option fictional-value") == "REPL_OPTION_UNKNOWN"
    )
    assert (
        _error_code(router.route, "set api-key fictional-secret-value")
        == "REPL_SECRET_OPTION_REJECTED"
    )
    assert router.module_options == {}
    assert _error_code(router.route, "run fictional-action") == "REPL_ACTION_UNKNOWN"


def test_router_direct_module_commands_and_parser_failures_use_shared_contract(
    app_context: AppContext,
) -> None:
    router = SessionRouter(app_context)
    result = router.route(
        "gedcom subtree fictional.ged --output fictional-output.ged --root-person Ada"
    )

    assert result.kind is RouteKind.EXECUTE
    assert result.invocation is not None
    assert result.invocation.namespace == build_parser().parse_args(list(result.invocation.tokens))
    assert _error_code(router.route, "gedcom fictional-action") == "REPL_USAGE_ERROR"
    assert _error_code(router.route, "exit now") == "REPL_USAGE_ERROR"


def test_router_import_does_not_load_terminal_ui_dependencies() -> None:
    script = "\n".join(
        (
            "import sys",
            "import ancestryllm.console.router",
            "blocked = {'cmd2', 'rich', 'prompt_toolkit'}",
            "loaded = sorted(name for name in sys.modules if name.split('.', 1)[0] in blocked)",
            "raise SystemExit('unexpected imports: ' + ', '.join(loaded) if loaded else 0)",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
