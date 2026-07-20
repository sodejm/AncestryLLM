from __future__ import annotations

import json
import lzma
import os
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from ancestryllm.cli import main
from ancestryllm.console.app import AncestryConsole
from ancestryllm.console.presentation import PresentationAdapter, to_plain
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.modules import BUILTIN_MODULES, ModuleRegistry


@dataclass(frozen=True, slots=True)
class CommandCase:
    module: str
    action: str
    arguments: tuple[str, ...]
    expected: Any

    @property
    def tokens(self) -> list[str]:
        return [self.module, self.action, *self.arguments]


@pytest.fixture
def fictional_files(tmp_path: Path) -> dict[str, Path]:
    gedcom = tmp_path / "fictional-tree.ged"
    gedcom.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    ocr = tmp_path / "fictional-ocr.txt"
    ocr.write_text("Ada Example was born in Fiction County.", encoding="utf-8")
    schema = tmp_path / "fictional-schema.json"
    schema.write_text('{"type": "object"}', encoding="utf-8")
    return {
        "gedcom": gedcom,
        "ocr": ocr,
        "schema": schema,
        "output": tmp_path / "fictional-output.ged",
        "report": tmp_path / "fictional-report.json",
    }


@pytest.fixture
def command_cases(fictional_files: dict[str, Path]) -> tuple[CommandCase, ...]:
    gedcom = str(fictional_files["gedcom"])
    output = str(fictional_files["output"])
    report = str(fictional_files["report"])
    ocr = str(fictional_files["ocr"])
    schema = str(fictional_files["schema"])
    return (
        CommandCase("rootsmagic", "list", (), {"module": "rootsmagic", "action": "list"}),
        CommandCase(
            "rootsmagic",
            "query",
            ("--tree", "Fictional.rmtree", "--sql", "SELECT 1"),
            {"module": "rootsmagic", "action": "query"},
        ),
        CommandCase(
            "rootsmagic",
            "export",
            (
                "--tree",
                "Fictional.rmtree",
                "--output",
                output,
                "--root-person-id",
                "I1",
                "--living",
                "redact",
            ),
            {"module": "rootsmagic", "action": "export"},
        ),
        CommandCase(
            "gedcom",
            "merge",
            (gedcom, "--output", output, "--quality-report", report),
            {"module": "gedcom", "action": "merge"},
        ),
        CommandCase(
            "gedcom",
            "subtree",
            (gedcom, "--output", output, "--root-person", "Ada Example"),
            {"module": "gedcom", "action": "subtree"},
        ),
        CommandCase(
            "gedcom",
            "quality",
            (gedcom, "--output", report, "--root-person", "Ada Example"),
            {"module": "gedcom", "action": "quality"},
        ),
        CommandCase(
            "gedcom",
            "sync",
            ("update", "--manifest", "fictional-private-manifest.json", "--dry-run"),
            None,
        ),
        CommandCase(
            "ocr",
            "extract",
            ("--input", ocr, "--provider", "none", "--model", "offline"),
            {"module": "ocr", "action": "extract"},
        ),
        CommandCase("prompts", "list", (), {"module": "prompts", "action": "list"}),
        CommandCase(
            "prompts",
            "save",
            (
                "family-summary",
                "--purpose",
                "Fictional research",
                "--body",
                "Hello ${person}",
                "--variable",
                "person",
                "--schema-file",
                schema,
                "--tag",
                "fictional",
                "--tag",
                "local",
            ),
            {"module": "prompts", "action": "save"},
        ),
        CommandCase(
            "prompts",
            "show",
            ("family-summary", "--version", "1"),
            {"module": "prompts", "action": "show"},
        ),
        CommandCase(
            "prompts",
            "render",
            ("family-summary", "--value", "person=Ada Example"),
            {"module": "prompts", "action": "render"},
        ),
        CommandCase(
            "people",
            "list",
            ("--workspace", "fictional"),
            {"module": "people", "action": "list"},
        ),
        CommandCase(
            "people",
            "add",
            (
                "Zoë 示例",
                "--living-status",
                "deceased",
                "--notes",
                "",
                "--workspace",
                "fictional",
            ),
            {"module": "people", "action": "add"},
        ),
        CommandCase("providers", "list", (), {"profiles": ["fictional"], "consents": ["local"]}),
        CommandCase(
            "providers",
            "create",
            ("fictional", "--provider", "ollama", "--model", "local-model"),
            {"module": "providers", "action": "create"},
        ),
        CommandCase(
            "providers",
            "consent",
            (
                "local",
                "--profile",
                "fictional",
                "--module",
                "gedcom",
                "--module",
                "ocr",
                "--purpose",
                "merge",
                "--purpose",
                "extract",
                "--data-class",
                "public_genealogy",
                "--model",
                "local-model",
            ),
            {"module": "providers", "action": "consent"},
        ),
        CommandCase(
            "providers",
            "revoke",
            ("local",),
            "Revoked consent: local",
        ),
        CommandCase(
            "secrets", "set", ("openai.api_key",), "Stored secret reference: openai.api_key"
        ),
        CommandCase(
            "secrets",
            "delete",
            ("openai.api_key",),
            "Deleted secret reference: openai.api_key",
        ),
        CommandCase("secrets", "status", ("openai.api_key",), {"openai.api_key": False}),
    )


@pytest.fixture
def mocked_action_services(app_context: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from ancestryllm.gedcom.service import GedcomService
    from ancestryllm.ocr.service import OcrService
    from ancestryllm.rootsmagic.service import RootsMagicService

    monkeypatch.setattr(
        RootsMagicService, "list_trees", lambda _self: {"module": "rootsmagic", "action": "list"}
    )
    monkeypatch.setattr(
        RootsMagicService,
        "query_sql",
        lambda _self, *_args, **_kwargs: {"module": "rootsmagic", "action": "query"},
    )
    monkeypatch.setattr(
        RootsMagicService,
        "export",
        lambda _self, *_args, **_kwargs: {"module": "rootsmagic", "action": "export"},
    )
    monkeypatch.setattr(
        GedcomService,
        "merge",
        lambda _self, *_args, **_kwargs: {"module": "gedcom", "action": "merge"},
    )
    monkeypatch.setattr(
        GedcomService,
        "subtree",
        lambda _self, *_args, **_kwargs: {"module": "gedcom", "action": "subtree"},
    )
    monkeypatch.setattr(
        GedcomService,
        "quality",
        lambda _self, *_args, **_kwargs: {"module": "gedcom", "action": "quality"},
    )
    monkeypatch.setattr(GedcomService, "sync", lambda _self, _args: 0)
    monkeypatch.setattr(
        OcrService,
        "extract",
        lambda _self, *_args, **_kwargs: {"module": "ocr", "action": "extract"},
    )

    app_context.prompts = SimpleNamespace(
        list=lambda: {"module": "prompts", "action": "list"},
        save=lambda *_args: {"module": "prompts", "action": "save"},
        get=lambda *_args: {"module": "prompts", "action": "show"},
        render=lambda *_args: {"module": "prompts", "action": "render"},
    )
    app_context.research = SimpleNamespace(
        list_people=lambda *_args: {"module": "people", "action": "list"},
        add_person=lambda *_args: {"module": "people", "action": "add"},
    )
    app_context.provider_profiles = SimpleNamespace(
        list_profiles=lambda: ["fictional"],
        list_consents=lambda: ["local"],
        create_profile=lambda *_args: {"module": "providers", "action": "create"},
        create_consent=lambda *_args, **_kwargs: {"module": "providers", "action": "consent"},
        revoke_consent=lambda *_args: None,
        consent_grant=lambda *_args: None,
    )


def _record_rendered_values(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    rendered: list[Any] = []

    def record(_self: PresentationAdapter, value: Any, *, json_output: bool = False) -> None:
        del json_output
        rendered.append(to_plain(value))

    monkeypatch.setattr(PresentationAdapter, "render", record)
    return rendered


def test_action_matrix_covers_every_shipped_module_action(
    command_cases: tuple[CommandCase, ...],
) -> None:
    covered = {(case.module, case.action) for case in command_cases}
    shipped = {
        (module_id, action)
        for module_id, descriptor in BUILTIN_MODULES.items()
        for action in descriptor.actions
    }
    assert covered == shipped


@pytest.mark.parametrize("case_index", range(21))
def test_one_shot_and_repl_return_identical_dtos_for_every_action(
    case_index: int,
    command_cases: tuple[CommandCase, ...],
    app_context: AppContext,
    mocked_action_services: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del mocked_action_services
    case = command_cases[case_index]
    rendered = _record_rendered_values(monkeypatch)
    secret_value = "fictional-secret-value"
    monkeypatch.setattr(
        "ancestryllm.cli.getpass.getpass",
        Mock(side_effect=[secret_value, secret_value, secret_value, secret_value]),
    )

    assert main(["--json", *case.tokens], app_context) == 0
    console = AncestryConsole(app_context)
    assert console.onecmd_plus_hooks(shlex.join(case.tokens)) is False

    expected = [] if case.expected is None else [case.expected, case.expected]
    assert rendered == expected
    assert secret_value not in json.dumps(rendered, ensure_ascii=False)


def test_one_shot_lists_enabled_modules(app_context: AppContext, capsys) -> None:
    assert main(["--json", "modules", "list"], app_context) == 0
    output = capsys.readouterr().out
    assert '"module_id": "gedcom"' in output
    assert '"module_id": "rootsmagic"' in output


def test_console_control_commands_track_context_and_run_selected_action(
    app_context: AppContext, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    run_tokens = Mock(return_value=0)
    monkeypatch.setattr("ancestryllm.console.app.run_tokens", run_tokens)
    console = AncestryConsole(app_context)

    console.onecmd_plus_hooks("modules")
    console.onecmd_plus_hooks("use gedcom")
    console.onecmd_plus_hooks("info")
    console.onecmd_plus_hooks("show actions")
    console.onecmd_plus_hooks('set root-person "Ada Example"')
    console.onecmd_plus_hooks("set generations 3")
    console.onecmd_plus_hooks("set generations 4")
    console.onecmd_plus_hooks('set note ""')
    console.onecmd_plus_hooks("show options")
    console.onecmd_plus_hooks("unset note")
    console.onecmd_plus_hooks("run subtree --output fictional.ged --input fictional-input.ged")

    assert console.active_module == "gedcom"
    assert console.module_options == {"root_person": "Ada Example", "generations": "4"}
    run_tokens.assert_called_once_with(
        app_context,
        [
            "gedcom",
            "subtree",
            "--generations",
            "4",
            "--root-person",
            "Ada Example",
            "--output",
            "fictional.ged",
            "--input",
            "fictional-input.ged",
        ],
    )
    output = capsys.readouterr()
    assert "GEDCOM:" in output.out
    assert "subtree" in output.out
    assert "generations = 4" in output.out

    console.onecmd_plus_hooks("back")
    assert console.active_module is None
    assert console.module_options == {}


def test_console_rejects_invalid_context_commands_and_quoting(
    app_context: AppContext, capsys
) -> None:
    console = AncestryConsole(app_context)
    console.onecmd_plus_hooks("info")
    console.onecmd_plus_hooks("show unsupported")
    console.onecmd_plus_hooks("run")
    console.onecmd_plus_hooks("use unknown")
    console.onecmd_plus_hooks('set name "unterminated')
    output = capsys.readouterr()

    assert "Use a module first." in output.err
    assert "Module is not enabled: unknown" in output.err
    assert "No closing quotation" in output.err
    assert console.active_module is None
    assert console.module_options == {}


def test_stable_service_error_code_and_exit_are_preserved_across_adapters(
    app_context: AppContext, capsys
) -> None:
    def fail() -> None:
        raise AncestryError("PROMPT_STABLE_FAILURE", "Safe fictional failure.", exit_code=7)

    app_context.prompts = SimpleNamespace(list=fail)
    assert main(["prompts", "list"], app_context) == 7
    assert "[PROMPT_STABLE_FAILURE] Safe fictional failure." in capsys.readouterr().err

    console = AncestryConsole(app_context)
    with pytest.raises(AncestryError) as raised:
        console.do_prompts("list")
    assert raised.value.code == "PROMPT_STABLE_FAILURE"
    assert raised.value.exit_code == 7

    console.onecmd_plus_hooks("prompts list")
    repl_error = capsys.readouterr().err
    assert "Safe fictional failure." in repl_error
    assert "Traceback" not in repl_error


@pytest.mark.parametrize(
    ("arguments", "error_text"),
    (
        (["unknown"], "invalid choice"),
        (["people", "add"], "the following arguments are required"),
        (["rootsmagic", "query", "--tree", "fictional"], "one of the arguments"),
        (["providers", "create", "p", "--provider", "none", "--model", "m"], "invalid choice"),
    ),
)
def test_parser_failures_have_documented_exit_two(
    arguments: list[str], error_text: str, app_context: AppContext, capsys
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(arguments, app_context)
    assert raised.value.code == 2
    assert error_text in capsys.readouterr().err


def test_invalid_values_missing_files_and_json_parser_failures_are_sanitized(
    app_context: AppContext, tmp_path: Path, capsys
) -> None:
    assert (
        main(["prompts", "render", "fictional", "--value", "not-an-assignment"], app_context) == 1
    )
    assert "[ARGUMENT_INVALID]" in capsys.readouterr().err

    missing = tmp_path / "missing.txt"
    assert (
        main(
            [
                "ocr",
                "extract",
                "--input",
                str(missing),
                "--provider",
                "none",
                "--model",
                "offline",
            ],
            app_context,
        )
        == 2
    )
    missing_error = capsys.readouterr().err
    assert "[INPUT_ERROR]" in missing_error
    assert "Traceback" not in missing_error

    invalid_schema = tmp_path / "invalid-schema.json"
    invalid_schema.write_text("{not json", encoding="utf-8")
    assert (
        main(
            [
                "prompts",
                "save",
                "fictional",
                "--purpose",
                "local",
                "--body",
                "text",
                "--schema-file",
                str(invalid_schema),
            ],
            app_context,
        )
        == 2
    )
    assert "[INPUT_ERROR]" in capsys.readouterr().err


def test_disabled_modules_are_not_imported_or_registered(
    app_context: AppContext, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    app_context.config.enabled_modules = {"gedcom"}
    imported: list[str] = []
    original = __import__("importlib").import_module

    def record(name: str):
        imported.append(name)
        return original(name)

    monkeypatch.setattr("ancestryllm.core.modules.importlib.import_module", record)
    console = AncestryConsole(app_context)
    assert imported == ["ancestryllm.console.gedcom"]
    assert "ocr" not in console.get_all_commands()

    console.onecmd_plus_hooks("use ocr")
    assert "Module is not enabled: ocr" in capsys.readouterr().err
    assert [item.module_id for item in ModuleRegistry(app_context).descriptors()] == ["gedcom"]


def test_secret_values_never_reach_options_output_history_or_completion(
    app_context: AppContext, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    secret_value = "fictional-SUPER-SECRET-value"
    console = AncestryConsole(app_context)
    console.onecmd_plus_hooks("use gedcom")
    console.do_set(f"api_key {secret_value}")
    assert console.module_options == {}

    monkeypatch.setattr(
        "ancestryllm.cli.getpass.getpass", Mock(side_effect=[secret_value, secret_value])
    )
    console.onecmd_plus_hooks("secrets set openai.api_key")
    assert app_context.secrets.get("openai.api_key") == secret_value
    assert all(secret_value not in str(item) for item in console.history)

    console._persist_history()
    history_bytes = Path(console.persistent_history_file).read_bytes()
    assert secret_value.encode() not in history_bytes
    assert secret_value not in lzma.decompress(history_bytes).decode()

    completions = console.complete("", "", 0, 0)
    output = capsys.readouterr()
    exposed = "\n".join([output.out, output.err, json.dumps([str(item) for item in completions])])
    assert secret_value not in exposed
    assert "Traceback" not in exposed

    assert main(["--json", "secrets", "status", "openai.api_key"], app_context) == 0
    json_output = capsys.readouterr().out
    assert json.loads(json_output) == {"openai.api_key": True}
    assert secret_value not in json_output


def test_history_is_private_bounded_and_recovers_from_corruption(
    app_context: AppContext, capsys
) -> None:
    history = app_context.config.data_dir / "console_history"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_bytes(b"not compressed history")
    history.chmod(0o644)

    console = AncestryConsole(app_context)
    assert "Error decompressing persistent history data" in capsys.readouterr().err
    assert stat.S_IMODE(history.stat().st_mode) == 0o600

    statement = console.statement_parser.parse("modules")
    for _ in range(console._persistent_history_length + 5):
        console.history.append(statement)
    console._persist_history()

    recovered = AncestryConsole(app_context)
    assert len(recovered.history) == console._persistent_history_length
    assert stat.S_IMODE(history.stat().st_mode) == 0o600


def test_shell_python_scripts_redirection_pipes_and_expansions_cannot_execute(
    app_context: AppContext, tmp_path: Path, capsys
) -> None:
    console = AncestryConsole(app_context)
    marker = tmp_path / "must-not-exist"
    script = tmp_path / "unsafe.txt"
    script.write_text(f"shell touch {shlex.quote(str(marker))}\n", encoding="utf-8")
    commands = (
        f"shell touch {shlex.quote(str(marker))}",
        f"!touch {shlex.quote(str(marker))}",
        f"py open({str(marker)!r}, 'w').close()",
        f"run_script {shlex.quote(str(script))}",
        f"run_pyscript {shlex.quote(str(script))}",
        f"edit {shlex.quote(str(marker))}",
        f"modules > {shlex.quote(str(marker))}",
        f"modules | touch {shlex.quote(str(marker))}",
        f"alias create escape shell touch {shlex.quote(str(marker))}",
        "escape",
        f"macro create escape_macro shell touch {shlex.quote(str(marker))}",
        "escape_macro",
    )
    for command in commands:
        console.onecmd_plus_hooks(command)

    assert marker.exists() is False
    assert console.allow_redirection is False
    assert {"shell", "run_script", "run_pyscript", "edit", "shortcuts"}.issubset(
        console.disabled_commands
    )
    assert "py" not in console.get_all_commands()
    assert "Disabled by AncestryLLM security policy." in capsys.readouterr().err


def test_repl_handles_interrupt_then_eof_without_a_traceback(
    app_context: AppContext, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    console = AncestryConsole(app_context)
    reader = Mock(side_effect=[KeyboardInterrupt, EOFError])
    monkeypatch.setattr(console, "_read_command_line", reader)

    assert console.cmdloop() == 0
    output = capsys.readouterr()
    assert "^C" in output.out
    assert "Traceback" not in output.out + output.err
    assert reader.call_count == 2


def test_database_diagnostics_are_available_as_json(app_context: AppContext, capsys) -> None:
    assert main(["--json", "database", "diagnose"], app_context) == 0
    assert '"code": "SQLCIPHER_READY"' in capsys.readouterr().out


def test_clean_install_entry_points_and_json_smoke(tmp_path: Path) -> None:
    assert (3, 12) <= sys.version_info[:2] < (3, 15)
    repository = Path(__file__).resolve().parents[2]
    environment = tmp_path / "clean-install" / "site-packages"
    shutil.copytree(
        repository / "src" / "ancestryllm",
        environment / "ancestryllm",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    ancestry = tmp_path / "clean-install" / "bin" / "ancestry"
    ancestry.parent.mkdir()
    ancestry.write_text(
        f"#!{sys.executable}\nfrom ancestryllm.cli import main\nraise SystemExit(main())\n",
        encoding="utf-8",
    )
    ancestry.chmod(0o755)
    isolated_home = tmp_path / "fictional-home"
    isolated_home.mkdir()
    child_environment = {
        **os.environ,
        "HOME": str(isolated_home),
        "XDG_CONFIG_HOME": str(isolated_home / "config"),
        "XDG_DATA_HOME": str(isolated_home / "data"),
        "PYTHONPATH": str(environment),
    }

    for command in (
        [str(ancestry), "--json", "modules", "list"],
        [sys.executable, "-m", "ancestryllm", "--json", "modules", "list"],
    ):
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=child_environment,
            cwd=isolated_home,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert {item["module_id"] for item in payload} == set(BUILTIN_MODULES)
        assert completed.stderr == ""
