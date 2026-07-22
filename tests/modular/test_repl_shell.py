"""Black-box tests for the default prompt_toolkit shell."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import sys
import threading
import types
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from prompt_toolkit.completion import DummyCompleter
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ancestryllm.console.router import RouteKind, RouteResult
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError


@dataclass(frozen=True)
class _FakeCompletionSnapshot:
    profiles: tuple[str, ...] = ()
    consents: tuple[str, ...] = ()


@contextmanager
def _completion_fallback(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Supply the concurrent completion seam only while it does not exist yet."""

    try:
        importlib.import_module("ancestryllm.console.completion")
    except ModuleNotFoundError as exc:
        if exc.name != "ancestryllm.console.completion":
            raise
        module = types.ModuleType("ancestryllm.console.completion")
        module.CompletionSnapshot = _FakeCompletionSnapshot
        module.create_completer = lambda *_args, **_kwargs: DummyCompleter()
        monkeypatch.setitem(sys.modules, module.__name__, module)
    yield


@pytest.fixture
def shell_module(monkeypatch: pytest.MonkeyPatch):
    with _completion_fallback(monkeypatch):
        sys.modules.pop("ancestryllm.console.shell", None)
        module = importlib.import_module("ancestryllm.console.shell")
    monkeypatch.setattr(module, "create_completer", lambda *_args, **_kwargs: DummyCompleter())
    monkeypatch.setattr(
        module, "build_completion_snapshot", lambda _context: _FakeCompletionSnapshot()
    )
    return module


def _application(
    shell_module, app_context: AppContext, pipe
) -> tuple[object, io.StringIO, io.StringIO]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    application = shell_module.ReplApplication(
        app_context,
        input=pipe,
        output=DummyOutput(),
        stdout=stdout,
        stderr=stderr,
    )
    return application, stdout, stderr


@pytest.mark.parametrize("command", ("exit", "quit"))
def test_default_shell_accepts_exit_commands_from_prompt_toolkit_pipe(
    shell_module, app_context: AppContext, command: str
) -> None:
    with create_pipe_input() as pipe:
        application, _stdout, _stderr = _application(shell_module, app_context, pipe)
        pipe.send_text(f"{command}\n")

        assert asyncio.run(application.run_async()) == 0


def test_default_shell_returns_cleanly_at_pipe_eof(shell_module, app_context: AppContext) -> None:
    with create_pipe_input() as pipe:
        application, _stdout, _stderr = _application(shell_module, app_context, pipe)
        pipe.close()

        assert asyncio.run(application.run_async()) == 0


def test_default_shell_recovers_from_interrupt_then_accepts_exit(
    shell_module, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    with create_pipe_input() as pipe:
        application, _stdout, _stderr = _application(shell_module, app_context, pipe)
        prompts = iter((KeyboardInterrupt(), "exit"))

        async def next_prompt(_prompt: str) -> str:
            result = next(prompts)
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(application.session, "prompt_async", next_prompt)

        assert asyncio.run(application.run_async()) == 0


def test_missing_rootsmagic_question_uses_multiline_editor_and_preserves_markdown(
    shell_module, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = "Compare both records.\n\n- Explain the conflict\n- Cite the stronger source"
    captured: list[argparse.Namespace] = []
    with create_pipe_input() as pipe:
        application, _stdout, _stderr = _application(shell_module, app_context, pipe)

        async def multiline_prompt(prompt: str, **kwargs: object) -> str:
            assert prompt.startswith("Natural-language question")
            assert kwargs == {"multiline": True, "prompt_continuation": "... "}
            return question

        application.multiline_session.prompt_async = multiline_prompt
        monkeypatch.setattr(
            shell_module,
            "dispatch",
            lambda namespace, _context: captured.append(namespace) or 0,
        )
        asyncio.run(
            application.execute_line(
                "rootsmagic query --tree fictional --provider none --model offline"
            )
        )

    assert len(captured) == 1
    assert captured[0].question == question
    assert list(application.history.load_history_strings()) == []


def test_missing_prompt_body_uses_multiline_editor_in_module_context(
    shell_module, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "Research $person.\n\nReturn **Markdown**."
    captured: list[argparse.Namespace] = []
    with create_pipe_input() as pipe:
        application, _stdout, _stderr = _application(shell_module, app_context, pipe)
        application.router.route("use prompts")

        async def multiline_prompt(_prompt: str, **_kwargs: object) -> str:
            return body

        application.multiline_session.prompt_async = multiline_prompt
        monkeypatch.setattr(
            shell_module,
            "dispatch",
            lambda namespace, _context: captured.append(namespace) or 0,
        )
        asyncio.run(
            application.execute_line("run save research-plan --purpose research --variable person")
        )

    assert len(captured) == 1
    assert captured[0].body == body


@pytest.mark.parametrize(
    ("value", "error_code"),
    (("", "MULTILINE_INPUT_EMPTY"), ("x" * 100_001, "MULTILINE_INPUT_TOO_LARGE")),
)
def test_multiline_editor_rejects_empty_and_oversized_input(
    shell_module,
    app_context: AppContext,
    value: str,
    error_code: str,
) -> None:
    with create_pipe_input() as pipe:
        application, _stdout, stderr = _application(shell_module, app_context, pipe)

        async def multiline_prompt(_prompt: str, **_kwargs: object) -> str:
            return value

        application.multiline_session.prompt_async = multiline_prompt
        asyncio.run(application.execute_line("rootsmagic query --tree fictional"))

    assert error_code in stderr.getvalue()


@pytest.mark.parametrize("cancelled", (EOFError(), KeyboardInterrupt()))
def test_multiline_editor_cancellation_does_not_dispatch(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: BaseException,
) -> None:
    dispatched = False
    with create_pipe_input() as pipe:
        application, _stdout, stderr = _application(shell_module, app_context, pipe)

        async def multiline_prompt(_prompt: str, **_kwargs: object) -> str:
            raise cancelled

        def dispatch(_namespace: argparse.Namespace, _context: AppContext) -> int:
            nonlocal dispatched
            dispatched = True
            return 0

        application.multiline_session.prompt_async = multiline_prompt
        monkeypatch.setattr(shell_module, "dispatch", dispatch)
        asyncio.run(application.execute_line("rootsmagic query --tree fictional"))

    assert not dispatched
    assert "MULTILINE_INPUT_CANCELLED" in stderr.getvalue()


def test_secret_entry_is_no_echo_confirmed_stored_and_never_persisted(
    shell_module, app_context: AppContext
) -> None:
    fictional_secret = "fictional-secret-value"
    with create_pipe_input() as pipe:
        application, stdout, stderr = _application(shell_module, app_context, pipe)
        prompts: list[tuple[str, bool]] = []
        values = iter((fictional_secret, fictional_secret))

        async def secret_prompt(prompt: str, *, is_password: bool) -> str:
            prompts.append((prompt, is_password))
            return next(values)

        application.secret_session.prompt_async = secret_prompt
        asyncio.run(application.execute_line("secrets set openai.api_key"))
        application.history.store_string(f"secrets set openai.api_key {fictional_secret}")

    assert app_context.secrets.get("openai.api_key") == fictional_secret
    assert prompts == [
        ("Secret value for openai.api_key: ", True),
        ("Confirm secret value: ", True),
    ]
    assert fictional_secret not in stdout.getvalue() + stderr.getvalue()
    assert list(application.history.load_history_strings()) == []
    assert fictional_secret not in application.history.path.read_text(encoding="utf-8")


def test_secret_mismatch_is_redacted_not_stored_and_not_persisted(
    shell_module, app_context: AppContext
) -> None:
    entered = iter(("fictional-first-secret", "fictional-other-secret"))
    with create_pipe_input() as pipe:
        application, stdout, stderr = _application(shell_module, app_context, pipe)

        async def secret_prompt(_prompt: str, *, is_password: bool) -> str:
            assert is_password is True
            return next(entered)

        application.secret_session.prompt_async = secret_prompt
        asyncio.run(application.execute_line("secrets set anthropic.api_key"))
        application.history.store_string("secrets set anthropic.api_key fictional-first-secret")

    assert app_context.secrets.get("anthropic.api_key") is None
    rendered = stdout.getvalue() + stderr.getvalue()
    assert "SECRET_CONFIRMATION_FAILED" in rendered
    assert "fictional-first-secret" not in rendered
    assert "fictional-other-secret" not in rendered
    assert list(application.history.load_history_strings()) == []


@pytest.mark.parametrize("cancelled", (EOFError(), KeyboardInterrupt()))
def test_secret_entry_cancellation_is_clean_and_stores_nothing(
    shell_module, app_context: AppContext, cancelled: BaseException
) -> None:
    with create_pipe_input() as pipe:
        application, _stdout, stderr = _application(shell_module, app_context, pipe)

        async def cancelled_prompt(_prompt: str, *, is_password: bool) -> str:
            assert is_password is True
            raise cancelled

        application.secret_session.prompt_async = cancelled_prompt
        asyncio.run(application.execute_line("secrets set gemini.api_key"))

    assert app_context.secrets.get("gemini.api_key") is None
    assert "SECRET_ENTRY_CANCELLED" in stderr.getvalue()


def test_unexpected_command_failures_are_sanitized(
    shell_module, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    fictional_secret = "fictional-sensitive-exception"
    app_context.secrets.register_sensitive(fictional_secret)
    with create_pipe_input() as pipe:
        application, _stdout, stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(
            type(application.router),
            "route",
            lambda _router, _command: (_ for _ in ()).throw(
                RuntimeError(f"backend leaked {fictional_secret}")
            ),
        )

        asyncio.run(application.execute_line("fictional failure"))

    rendered = stderr.getvalue()
    assert fictional_secret not in rendered
    assert "REPL_COMMAND_FAILED" in rendered


def test_shell_redacts_route_results_and_errors(
    shell_module, app_context: AppContext, monkeypatch
) -> None:
    fictional_secret = "fictional-registered-secret"
    app_context.secrets.register_sensitive(fictional_secret)
    with create_pipe_input() as pipe:
        application, stdout, stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(
            type(application.router),
            "route",
            lambda _router, _command: RouteResult(RouteKind.OUTPUT, {"result": fictional_secret}),
        )
        asyncio.run(application.execute_line("fictional output"))
        monkeypatch.setattr(
            type(application.router),
            "route",
            lambda _router, _command: (_ for _ in ()).throw(
                AncestryError("FICTIONAL_FAILURE", f"provider returned {fictional_secret}")
            ),
        )
        asyncio.run(application.execute_line("fictional error"))

    rendered = stdout.getvalue() + stderr.getvalue()
    assert fictional_secret not in rendered
    assert '"result": "[REDACTED]"' in rendered
    assert "[FICTIONAL_FAILURE] provider returned [REDACTED]" in rendered


def test_shell_dispatches_direct_commands_off_the_event_loop(
    shell_module, app_context: AppContext, monkeypatch
) -> None:
    invocation = types.SimpleNamespace(
        namespace=argparse.Namespace(command="modules", action="list")
    )
    worker_identifiers: list[int] = []

    def fake_dispatch(namespace: argparse.Namespace, context: AppContext) -> int:
        assert namespace.command == "modules"
        assert context is app_context
        worker_identifiers.append(threading.get_ident())
        return 0

    with create_pipe_input() as pipe:
        application, _stdout, _stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(
            type(application.router),
            "route",
            lambda _router, _command: RouteResult(RouteKind.EXECUTE, invocation=invocation),
        )
        monkeypatch.setattr(shell_module, "dispatch", fake_dispatch)
        loop_identifier = threading.get_ident()

        asyncio.run(application.execute_line("modules list"))

    assert worker_identifiers
    assert worker_identifiers[0] != loop_identifier


def test_main_uses_default_shell_and_preserves_one_shot_dispatch(
    shell_module, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ancestryllm.cli as cli

    calls: list[str] = []
    monkeypatch.setattr(
        shell_module,
        "run_repl",
        lambda context: calls.append(f"repl:{context is app_context}") or 17,
    )

    def one_shot(namespace: argparse.Namespace, context: AppContext) -> int:
        assert namespace.command == "modules"
        assert namespace.action == "list"
        assert context is app_context
        calls.append("one-shot")
        return 29

    monkeypatch.setattr(cli, "dispatch", one_shot)

    assert cli.main([], app_context) == 17
    assert cli.main(["modules", "list"], app_context) == 29
    assert calls == ["repl:True", "one-shot"]


def test_main_rejects_legacy_console_like_unknown_or_unsupported_options(
    app_context: AppContext, capsys: pytest.CaptureFixture[str]
) -> None:
    import ancestryllm.cli as cli

    with pytest.raises(SystemExit) as legacy_raised:
        cli.main(["--legacy-console"], app_context)
    legacy_error = capsys.readouterr().err

    with pytest.raises(SystemExit) as unsupported_raised:
        cli.main(["--unsupported-option"], app_context)
    unsupported_error = capsys.readouterr().err

    assert legacy_raised.value.code == unsupported_raised.value.code == 2
    assert "the following arguments are required: command" in legacy_error
    assert legacy_error == unsupported_error.replace("--unsupported-option", "--legacy-console")


def test_prompt_toolkit_repl_preserves_modules_list_json_schema(
    shell_module, app_context: AppContext
) -> None:
    with create_pipe_input() as pipe:
        application, stdout, _stderr = _application(shell_module, app_context, pipe)
        asyncio.run(application.execute_line("modules list --json"))

    modules = json.loads(stdout.getvalue())
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
