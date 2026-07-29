"""Public REPL boundaries for adversarial GEDCOM jobs."""

from __future__ import annotations

import asyncio
import importlib
import io
import shlex
import threading
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest
from prompt_toolkit.completion import DummyCompleter
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ancestryllm.cli import build_parser, dispatch
from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobState
from ancestryllm.gedcom import engine

GEDCOM_FIXTURE = Path(__file__).parents[1] / "fixtures" / "gedcom_merge" / "quality-source-a.ged"


@dataclass(frozen=True)
class _FakeCompletionSnapshot:
    profiles: tuple[str, ...] = ()
    consents: tuple[str, ...] = ()


@pytest.fixture
def shell_module(monkeypatch: pytest.MonkeyPatch):
    module = importlib.import_module("ancestryllm.console.shell")
    monkeypatch.setattr(module, "create_completer", lambda *_args, **_kwargs: DummyCompleter())
    monkeypatch.setattr(
        module, "build_completion_snapshot", lambda _context: _FakeCompletionSnapshot()
    )
    return module


def _application(shell_module, context: AppContext, pipe):
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


def _assert_no_staged_artifacts(directory: Path) -> None:
    assert not tuple(directory.glob(".ancestry-publish-*"))


def test_repl_gedcom_rejection_matches_cli_code_without_leaking_input(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_payload = "PRIVATE-ADVERSARIAL-GENEALOGY"
    malformed = tmp_path / "malformed.ged"
    malformed.write_text(f"0 HEAD\n{private_payload}\n0 TRLR\n", encoding="utf-8")
    output = tmp_path / "quality.md"
    output.write_text("fictional sentinel\n", encoding="utf-8")
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)
    command = " ".join(
        (
            "gedcom quality",
            shlex.quote(str(malformed)),
            "--output",
            shlex.quote(str(output)),
            "--root-person",
            shlex.quote("Maren Hollow"),
        )
    )

    cli_namespace = build_parser().parse_args(shlex.split(command))
    with pytest.raises(AncestryError) as cli_error:
        dispatch(cli_namespace, app_context, emit=lambda _value, _json: None)

    with create_pipe_input() as pipe:
        application, stdout, stderr = _application(shell_module, app_context, pipe)
        try:
            assert asyncio.run(application.execute_line(command)) is False
            snapshot = application.jobs.wait("j000001", timeout=2)
        finally:
            application.jobs.shutdown(cancel=True)

    assert snapshot.state is JobState.FAILED
    assert snapshot.error_code == cli_error.value.code == "GEDCOM_PARSE_INVALID"
    assert snapshot.error_message == cli_error.value.message
    rendered = stdout.getvalue() + stderr.getvalue() + (snapshot.error_message or "")
    assert private_payload not in rendered
    assert str(malformed) not in rendered
    assert output.read_text(encoding="utf-8") == "fictional sentinel\n"
    _assert_no_staged_artifacts(tmp_path)
    provider_call.assert_not_called()


def test_ctrl_c_cancels_real_gedcom_job_and_keeps_repl_usable(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "quality.md"
    output.write_text("fictional sentinel\n", encoding="utf-8")
    traversal_started = threading.Event()
    release_checkpoint = threading.Event()

    def pause_traversal() -> None:
        traversal_started.set()
        assert release_checkpoint.wait(2)
        cancellation_checkpoint()

    monkeypatch.setattr(engine, "cancellation_checkpoint", pause_traversal)
    command = " ".join(
        (
            "gedcom quality",
            shlex.quote(str(GEDCOM_FIXTURE)),
            "--output",
            shlex.quote(str(output)),
            "--root-person",
            shlex.quote("Maren Hollow"),
        )
    )

    with create_pipe_input() as pipe:
        application, stdout, _stderr = _application(shell_module, app_context, pipe)
        prompt_calls = 0

        async def prompt(_prompt: str) -> str:
            nonlocal prompt_calls
            prompt_calls += 1
            if prompt_calls == 1:
                return command
            if prompt_calls == 2:
                assert await asyncio.to_thread(traversal_started.wait, 2)
                raise KeyboardInterrupt
            if prompt_calls == 3:
                release_checkpoint.set()
                cancelled = await asyncio.to_thread(application.jobs.wait, "j000001", 2)
                assert cancelled.state is JobState.CANCELLED
                return "jobs list"
            return "exit"

        monkeypatch.setattr(application.session, "prompt_async", prompt)
        try:
            assert asyncio.run(application.run_async()) == 0
        finally:
            release_checkpoint.set()

    snapshot = application.jobs.get("j000001")
    assert prompt_calls == 4
    assert snapshot.state is JobState.CANCELLED
    assert snapshot.error_code == "JOB_CANCELLED"
    assert output.read_text(encoding="utf-8") == "fictional sentinel\n"
    _assert_no_staged_artifacts(tmp_path)
    assert "j000001" in stdout.getvalue()
    assert "cancelled" in stdout.getvalue()
