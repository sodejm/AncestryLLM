from __future__ import annotations

from ancestryllm.cli import main
from ancestryllm.console.app import AncestryConsole
from ancestryllm.core.context import AppContext
from ancestryllm.core.modules import ModuleRegistry


def test_one_shot_lists_enabled_modules(app_context: AppContext, capsys) -> None:
    assert main(["--json", "modules", "list"], app_context) == 0
    output = capsys.readouterr().out
    assert '"module_id": "gedcom"' in output
    assert '"module_id": "rootsmagic"' in output


def test_console_loads_command_sets_and_disables_redirection(app_context: AppContext) -> None:
    console = AncestryConsole(app_context)
    commands = console.get_all_commands()
    assert {"gedcom", "rootsmagic", "prompts", "people", "providers", "secrets"}.issubset(commands)
    assert console.allow_redirection is False
    assert "shell" in console.disabled_commands
    assert "run_script" in console.disabled_commands


def test_console_module_context_tracks_nonsecret_options(app_context: AppContext) -> None:
    console = AncestryConsole(app_context)
    console.onecmd_plus_hooks("use gedcom")
    console.onecmd_plus_hooks("set action subtree")
    console.onecmd_plus_hooks("set root-person Ada")
    console.onecmd_plus_hooks("set api_key forbidden")
    assert console.active_module == "gedcom"
    assert console.module_options == {"action": "subtree", "root_person": "Ada"}


def test_disabled_modules_are_not_imported(app_context: AppContext, monkeypatch) -> None:
    app_context.config.enabled_modules = {"gedcom"}
    imported: list[str] = []
    original = __import__("importlib").import_module

    def record(name: str):
        imported.append(name)
        return original(name)

    monkeypatch.setattr("ancestryllm.core.modules.importlib.import_module", record)
    loaded = ModuleRegistry(app_context).load()
    assert len(loaded) == 1
    assert imported == ["ancestryllm.console.gedcom"]
