"""Prompt-loop end-to-end coverage for the default interactive REPL."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import shlex
import socket
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest
from prompt_toolkit.completion import DummyCompleter
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ancestryllm.core.context import AppContext


@dataclass(frozen=True)
class _CompletionSnapshot:
    profiles: tuple[str, ...] = ()
    consents: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ReplCase:
    module: str
    action: str
    arguments: tuple[str, ...] = ()

    @property
    def direct_command(self) -> str:
        return shlex.join((self.module, self.action, *self.arguments))

    @property
    def contextual_command(self) -> str:
        return shlex.join(("run", self.action, *self.arguments))


_BUILTIN_REPL_CASES = (
    _ReplCase("rootsmagic", "list"),
    _ReplCase("gedcom", "sync", ("update", "--dry-run")),
    _ReplCase(
        "ocr",
        "extract",
        ("--input", "fictional-ocr.txt", "--provider", "none"),
    ),
    _ReplCase("prompts", "list"),
    _ReplCase("people", "list", ("--workspace", "fictional")),
    _ReplCase("providers", "list"),
    _ReplCase("secrets", "status"),
)


@pytest.fixture
def shell_module(monkeypatch: pytest.MonkeyPatch):
    module = importlib.import_module("ancestryllm.console.shell")
    monkeypatch.setattr(module, "create_completer", lambda *_args, **_kwargs: DummyCompleter())
    monkeypatch.setattr(module, "build_completion_snapshot", lambda _context: _CompletionSnapshot())
    return module


def _application(
    shell_module,
    context: AppContext,
    pipe,
) -> tuple[object, io.StringIO, io.StringIO]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    application = shell_module.ReplApplication(
        context,
        input=pipe,
        output=DummyOutput(),
        stdout=stdout,
        stderr=stderr,
    )
    return application, stdout, stderr


def _scripted_prompt(commands: Iterator[str], prompts: list[str]):
    async def prompt(label: str, **_kwargs: object) -> str:
        prompts.append(label)
        return next(commands)

    return prompt


def _install_forbidden_provider_sdks(
    monkeypatch: pytest.MonkeyPatch,
    constructed: list[str],
) -> None:
    """Make every optional SDK importable while rejecting adapter construction."""

    for module_name, constructor_name in (
        ("ollama", "Client"),
        ("openai", "OpenAI"),
        ("anthropic", "Anthropic"),
    ):
        module = ModuleType(module_name)

        def forbidden(*_args: Any, _name: str = module_name, **_kwargs: Any) -> object:
            constructed.append(_name)
            raise AssertionError(f"provider adapter constructed {_name}")

        setattr(module, constructor_name, forbidden)
        monkeypatch.setitem(sys.modules, module_name, module)

    google = ModuleType("google")
    genai = ModuleType("google.genai")

    def forbidden_gemini(*_args: Any, **_kwargs: Any) -> object:
        constructed.append("google.genai")
        raise AssertionError("provider adapter constructed google.genai")

    genai.Client = forbidden_gemini  # type: ignore[attr-defined]
    google.genai = genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)


def _reject_network_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("provider path attempted network access")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)


def test_prompt_loop_executes_direct_and_contextual_commands_for_all_builtins(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each built-in accepts equivalent direct and active-module prompt input."""

    dispatched: list[argparse.Namespace] = []
    commands: list[str] = []
    for case in _BUILTIN_REPL_CASES:
        commands.extend(
            (case.direct_command, f"use {case.module}", case.contextual_command, "back")
        )
    commands.append("exit")
    prompts: list[str] = []

    def fake_dispatch(namespace: argparse.Namespace, context: AppContext) -> int:
        assert context is app_context
        dispatched.append(namespace)
        return 0

    with create_pipe_input() as pipe:
        application, _stdout, stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(application, "_should_background", lambda _namespace: False)
        monkeypatch.setattr(shell_module, "dispatch", fake_dispatch)
        application.session.prompt_async = _scripted_prompt(iter(commands), prompts)

        assert asyncio.run(application.run_async()) == 0

    assert len(dispatched) == len(_BUILTIN_REPL_CASES) * 2
    for index, case in enumerate(_BUILTIN_REPL_CASES):
        direct, contextual = dispatched[index * 2 : index * 2 + 2]
        assert (direct.command, direct.action) == (case.module, case.action)
        assert vars(direct) == vars(contextual)
        assert prompts[index * 4 + 2] == f"ancestry({case.module}) > "
    assert stderr.getvalue() == ""


def test_prompt_loop_preserves_json_output_schema(shell_module, app_context: AppContext) -> None:
    prompts: list[str] = []
    with create_pipe_input() as pipe:
        application, stdout, _stderr = _application(shell_module, app_context, pipe)
        application.session.prompt_async = _scripted_prompt(
            iter(("modules list --json", "exit")), prompts
        )

        assert asyncio.run(application.run_async()) == 0

    modules = json.loads(stdout.getvalue())
    assert {item["module_id"] for item in modules} == {
        "rootsmagic",
        "gedcom",
        "ocr",
        "prompts",
        "people",
        "providers",
        "secrets",
    }
    assert prompts == ["ancestry > ", "ancestry > "]


def test_prompt_loop_preserves_explicit_offline_and_consent_arguments(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[argparse.Namespace] = []

    def fake_dispatch(namespace: argparse.Namespace, context: AppContext) -> int:
        assert context is app_context
        dispatched.append(namespace)
        return 0

    with create_pipe_input() as pipe:
        application, _stdout, _stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(application, "_should_background", lambda _namespace: False)
        monkeypatch.setattr(shell_module, "dispatch", fake_dispatch)
        application.session.prompt_async = _scripted_prompt(
            iter(
                (
                    "rootsmagic query --tree fictional --question 'Who is Ada?' "
                    "--provider none --model offline --consent fictional-consent",
                    "providers consent fictional-consent --profile fictional-profile "
                    "--module rootsmagic --purpose fictional-research "
                    "--data-class public_genealogy --model offline",
                    "exit",
                )
            ),
            [],
        )

        assert asyncio.run(application.run_async()) == 0

    query, consent = dispatched
    assert (query.provider, query.model, query.consent) == ("none", "offline", "fictional-consent")
    assert (
        consent.profile,
        consent.module,
        consent.purpose,
        consent.data_class,
        consent.model,
    ) == (
        "fictional-profile",
        ["rootsmagic"],
        ["fictional-research"],
        ["public_genealogy"],
        ["offline"],
    )


def test_prompt_loop_provider_none_never_constructs_an_adapter_or_uses_network(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A real offline REPL request stays offline even with every SDK credential present."""

    source = tmp_path / "fictional-ocr.txt"
    source.write_text("Fictional Ada Example, born 1901.\n", encoding="utf-8")
    for secret_name in (
        "openai.api_key",
        "anthropic.api_key",
        "gemini.api_key",
        "openrouter.api_key",
    ):
        app_context.secrets.set(secret_name, f"configured-{secret_name}")
    constructed: list[str] = []

    with create_pipe_input() as pipe:
        application, _stdout, stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(application, "_should_background", lambda _namespace: False)
        _install_forbidden_provider_sdks(monkeypatch, constructed)
        _reject_network_entry_points(monkeypatch)
        application.session.prompt_async = _scripted_prompt(
            iter(
                (
                    "ocr extract "
                    f"--input {shlex.quote(str(source))} --provider none --model offline",
                    "exit",
                )
            ),
            [],
        )

        assert asyncio.run(application.run_async()) == 0

    assert constructed == []
    assert "[PROVIDER_DISABLED]" in stderr.getvalue()


def test_prompt_loop_rejects_unconsented_remote_profile_before_adapter_or_network(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Missing cloud consent must fail before a remote provider adapter is built."""

    source = tmp_path / "fictional-ocr.txt"
    source.write_text("Fictional Ada Example, born 1901.\n", encoding="utf-8")
    app_context.secrets.set("openai.api_key", "configured-but-never-sent")
    app_context.provider_profiles.create_profile(
        "fictional-remote-profile",
        "openai",
        "fictional-model",
    )
    constructed: list[str] = []

    with create_pipe_input() as pipe:
        application, _stdout, stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(application, "_should_background", lambda _namespace: False)
        _install_forbidden_provider_sdks(monkeypatch, constructed)
        _reject_network_entry_points(monkeypatch)
        application.session.prompt_async = _scripted_prompt(
            iter(
                (
                    "ocr extract "
                    f"--input {shlex.quote(str(source))} --provider fictional-remote-profile",
                    "exit",
                )
            ),
            [],
        )

        assert asyncio.run(application.run_async()) == 0

    assert constructed == []
    assert "[CLOUD_CONSENT_REQUIRED]" in stderr.getvalue()


def test_prompt_loop_rejects_shell_syntax_and_redacts_sensitive_history(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fictional_secret = "fictional-secret-value"

    def fake_dispatch(namespace: argparse.Namespace, context: AppContext) -> int:
        assert (namespace.command, namespace.action) == ("providers", "create")
        assert context is app_context
        return 0

    with create_pipe_input() as pipe:
        application, stdout, stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(shell_module, "dispatch", fake_dispatch)
        pipe.send_text(
            "help\n"
            "providers create fictional-profile --provider ollama --model fictional-model "
            f"--setting api_key={fictional_secret}\n"
            "modules | fictional-command\n"
            "exit\n"
        )

        assert asyncio.run(application.run_async()) == 0

    history = list(application.history.load_history_strings())
    rendered = stdout.getvalue() + stderr.getvalue()
    assert "help" in history
    assert fictional_secret not in application.history.path.read_text(encoding="utf-8")
    assert fictional_secret not in rendered
    assert "REPL_SHELL_SYNTAX_REJECTED" in stderr.getvalue()


def test_slow_fake_provider_keeps_prompt_loop_responsive_and_cancellable(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    dispatched: list[argparse.Namespace] = []

    def fake_provider_dispatch(
        namespace: argparse.Namespace,
        context: AppContext,
        *,
        emit,
    ) -> int:
        assert context is app_context
        assert (namespace.command, namespace.action) == ("rootsmagic", "query")
        assert namespace.provider == "fictional-provider"
        dispatched.append(namespace)
        started.set()
        assert release.wait(2)
        emit({"provider": namespace.provider, "answer": "fictional"}, False)
        return 0

    with create_pipe_input() as pipe:
        application, stdout, _stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(shell_module, "dispatch", fake_provider_dispatch)
        prompt_count = 0

        async def prompt(_label: str, **_kwargs: object) -> str:
            nonlocal prompt_count
            prompt_count += 1
            if prompt_count == 1:
                return (
                    "rootsmagic query --tree fictional --question 'Who is Ada?' "
                    "--provider fictional-provider --consent fictional-consent"
                )
            if prompt_count == 2:
                assert await asyncio.to_thread(started.wait, 2)
                return "jobs show j000001"
            if prompt_count == 3:
                return "jobs cancel j000001"
            assert prompt_count == 4
            assert application.jobs.get("j000001").cancellation_requested_at is not None
            release.set()
            cancelled = await asyncio.to_thread(application.jobs.wait, "j000001", 2)
            assert cancelled.state.value == "cancelled"
            return "exit"

        application.session.prompt_async = prompt
        assert asyncio.run(application.run_async()) == 0

    assert len(dispatched) == 1
    assert '"operation": "rootsmagic query"' in stdout.getvalue()
    assert '"cancellation_requested": true' in stdout.getvalue()
