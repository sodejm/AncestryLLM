"""Black-box tests for the default prompt_toolkit shell."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import shlex
import sqlite3
import sys
import threading
import types
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from prompt_toolkit.completion import DummyCompleter
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ancestryllm.console.router import RouteKind, RouteResult
from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.ingress import FileKind
from ancestryllm.core.jobs import JobSnapshot


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


def _set_file_limit(
    context: AppContext,
    kind: FileKind,
    **changes: int | None,
) -> None:
    current = context.config.file_ingress
    selected = replace(getattr(current, kind.value), **changes)
    context.config.file_ingress = replace(current, **{kind.value: selected})


def _background_failure(
    shell_module,
    context: AppContext,
    command: str,
) -> tuple[JobSnapshot, str]:
    with create_pipe_input() as pipe:
        application, stdout, stderr = _application(shell_module, context, pipe)
        asyncio.run(application.execute_line(command))
        failed = application.jobs.wait("j000001", timeout=2)
        application.jobs.shutdown()
    rendered = stdout.getvalue() + stderr.getvalue() + (failed.error_message or "")
    return failed, rendered


def _write_repl_rootsmagic_tree(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE PersonTable (
            PersonID INTEGER PRIMARY KEY,
            Sex TEXT,
            Living INTEGER DEFAULT 0
        );
        CREATE TABLE NameTable (
            NameID INTEGER PRIMARY KEY,
            OwnerID INTEGER,
            Given TEXT,
            Surname TEXT,
            IsPrimary INTEGER
        );
        CREATE TABLE FamilyTable (
            FamilyID INTEGER PRIMARY KEY,
            FatherID INTEGER,
            MotherID INTEGER
        );
        CREATE TABLE ChildTable (
            ChildID INTEGER,
            FamilyID INTEGER
        );
        INSERT INTO PersonTable(PersonID, Sex, Living) VALUES (1, 'U', 0);
        INSERT INTO NameTable(NameID, OwnerID, Given, Surname, IsPrimary)
            VALUES (1, 1, 'Ada', 'Example', 1);
        """
    )
    connection.commit()
    connection.close()


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


def test_default_shell_shutdown_unsubscribes_and_closes_progress(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []
    with create_pipe_input() as pipe:
        application, _stdout, _stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(
            application.progress_display,
            "close",
            lambda: closed.append(True),
        )
        pipe.send_text("exit\n")

        assert asyncio.run(application.run_async()) == 0

    application._unsubscribe_progress()
    assert closed == [True]
    assert application.jobs._listeners == []


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


def test_ctrl_c_requests_foreground_cancellation_without_terminating_repl(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()

    def fake_dispatch(
        _namespace: argparse.Namespace,
        _context: AppContext,
        *,
        emit,
    ) -> int:
        del emit
        started.set()
        for _ in range(300):
            cancellation_checkpoint()
            threading.Event().wait(0.01)
        return 0

    with create_pipe_input() as pipe:
        application, stdout, _stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(shell_module, "dispatch", fake_dispatch)
        prompts = 0

        async def next_prompt(_prompt: str) -> str:
            nonlocal prompts
            prompts += 1
            if prompts == 1:
                return "rootsmagic query --tree fictional --question 'Who is Ada?'"
            if prompts == 2:
                assert await asyncio.to_thread(started.wait, 2)
                raise KeyboardInterrupt
            job = application.jobs.list()[0]
            await asyncio.to_thread(application.jobs.wait, job.job_id, 2)
            return "exit"

        monkeypatch.setattr(application.session, "prompt_async", next_prompt)
        assert asyncio.run(application.run_async()) == 0

    snapshot = application.jobs.list()[0]
    assert prompts == 3
    assert snapshot.state.value == "cancelled"
    assert snapshot.error_code == "JOB_CANCELLED"
    assert snapshot.cancellation_requested_at is not None
    assert '"cancellation_requested": true' in stdout.getvalue()


def test_jobs_cancel_requests_cooperative_cancellation(
    shell_module,
    app_context: AppContext,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def work(reporter) -> None:
        started.set()
        assert release.wait(2)
        reporter.check_cancelled()

    with create_pipe_input() as pipe:
        application, stdout, _stderr = _application(shell_module, app_context, pipe)
        job = application.jobs.submit_with_progress("fictional active job", work)
        assert started.wait(2)
        assert asyncio.run(application.execute_line(f"jobs cancel {job.job_id}")) is False
        release.set()
        cancelled = application.jobs.wait(job.job_id, timeout=2)
        application.jobs.shutdown()

    assert cancelled.state.value == "cancelled"
    assert '"cancellation_requested": true' in stdout.getvalue()


def test_exit_with_active_jobs_requires_explicit_cancel_wait_or_stay_decision(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def cancellable(reporter) -> None:
        started.set()
        while not release.is_set():
            reporter.check_cancelled()
            threading.Event().wait(0.01)

    with create_pipe_input() as pipe:
        application, stdout, stderr = _application(shell_module, app_context, pipe)
        job = application.jobs.submit_with_progress("fictional active job", cancellable)
        try:
            assert started.wait(2)
            answers = iter(("invalid", "stay", "cancel"))

            async def answer_exit(_prompt: str) -> str:
                return next(answers)

            monkeypatch.setattr(application.session, "prompt_async", answer_exit)
            assert asyncio.run(application.execute_line("exit")) is False
            assert application.jobs.get(job.job_id).state.value == "running"
            assert asyncio.run(application.execute_line("quit")) is True
            cancelled = application.jobs.wait(job.job_id, timeout=2)
        finally:
            release.set()
            application.jobs.shutdown()

    assert cancelled.state.value == "cancelled"
    assert "REPL_EXIT_DECISION_REQUIRED" in stderr.getvalue()
    assert "Exit cancelled" in stdout.getvalue()
    assert '"cancellation_requested": [' in stdout.getvalue()


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

        def dispatch(
            namespace: argparse.Namespace,
            _context: AppContext,
            *,
            emit,
        ) -> int:
            del emit
            captured.append(namespace)
            return 0

        monkeypatch.setattr(shell_module, "dispatch", dispatch)
        asyncio.run(
            application.execute_line(
                "rootsmagic query --tree fictional --provider none --model offline"
            )
        )
        application.jobs.wait("j000001", timeout=2)
        application.jobs.shutdown()

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


def test_repl_preserves_bounded_file_error_code_without_path_or_payload(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
) -> None:
    source = tmp_path / "private prompt body.txt"
    source.write_text("fictional private payload", encoding="utf-8")
    current = app_context.config.file_ingress
    app_context.config.file_ingress = replace(
        current,
        prompt_body=replace(current.prompt_body, max_bytes=4),
    )
    with create_pipe_input() as pipe:
        application, _stdout, stderr = _application(shell_module, app_context, pipe)
        asyncio.run(
            application.execute_line(f'prompts save fixture --purpose local --body-file "{source}"')
        )
        application.jobs.shutdown()

    rendered = stderr.getvalue()
    assert "[FILE_INPUT_TOO_LARGE]" in rendered
    assert str(source) not in rendered
    assert "fictional private payload" not in rendered
    assert app_context.prompts.list() == []


@pytest.mark.parametrize("operation", ("merge", "subtree", "quality", "rootsmagic"))
def test_repl_path_normalization_matches_the_one_shot_sanitized_error(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    suffix = "rmtree" if operation == "rootsmagic" else "ged"
    private_input = Path(f"~PRIVATE-NONEXISTENT/tree.{suffix}")
    private_detail = "PRIVATE path normalization failure"
    original_expanduser = Path.expanduser

    def reject_private_user(path: Path) -> Path:
        if path == private_input:
            raise RuntimeError(private_detail)
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", reject_private_user)
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)
    output = tmp_path / "existing.out"
    output.write_bytes(b"sentinel\n")
    if operation == "rootsmagic":
        command = f"rootsmagic query --tree {shlex.quote(str(private_input))} --sql 'SELECT 1'"
    else:
        second = tmp_path / "second.ged"
        second.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
        command = f"gedcom {operation} {shlex.quote(str(private_input))}"
        if operation == "merge":
            command += f" {shlex.quote(str(second))}"
        command += f" --output {shlex.quote(str(output))}"
        if operation != "merge":
            command += " --root-person 'Fictional Example'"

    failed, rendered = _background_failure(shell_module, app_context, command)

    assert failed.state.value == "failed"
    assert failed.error_code == "FILE_INPUT_UNREADABLE"
    assert str(private_input) not in rendered
    assert private_detail not in rendered
    assert output.read_bytes() == b"sentinel\n"
    provider_call.assert_not_called()


@pytest.mark.parametrize("invalid_target", ("output", "report"))
def test_repl_rootsmagic_export_output_paths_share_stable_sanitized_error(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_target: str,
) -> None:
    tree = tmp_path / "fictional.rmtree"
    _write_repl_rootsmagic_tree(tree)
    app_context.config.family_tree_dirs = [tmp_path]
    invalid = Path(f"~PRIVATE-NONEXISTENT/{invalid_target}.ged")
    private_detail = "PRIVATE RootsMagic export normalization failure"
    original_expanduser = Path.expanduser

    def reject_private_user(path: Path) -> Path:
        if path == invalid:
            raise RuntimeError(private_detail)
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", reject_private_user)
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)
    output = tmp_path / "existing.ged"
    report = tmp_path / "existing.md"
    output.write_bytes(b"output sentinel\n")
    report.write_bytes(b"report sentinel\n")
    selected_output = invalid if invalid_target == "output" else output
    selected_report = invalid if invalid_target == "report" else report

    failed, rendered = _background_failure(
        shell_module,
        app_context,
        f"rootsmagic export --tree {shlex.quote(str(tree))} "
        f"--output {shlex.quote(str(selected_output))} "
        f"--report {shlex.quote(str(selected_report))}",
    )

    assert failed.state.value == "failed"
    assert failed.error_code == "FILE_INPUT_UNREADABLE"
    assert str(invalid) not in rendered
    assert private_detail not in rendered
    assert output.read_bytes() == b"output sentinel\n"
    assert report.read_bytes() == b"report sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))
    provider_call.assert_not_called()


def test_repl_json_schema_ingress_failure_preserves_prompt_storage(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
) -> None:
    schema = tmp_path / "private-schema.json"
    private_payload = '{"private": "fictional payload"}'
    schema.write_text(private_payload, encoding="utf-8")
    _set_file_limit(app_context, FileKind.JSON_SCHEMA, max_bytes=4)

    with create_pipe_input() as pipe:
        application, stdout, stderr = _application(shell_module, app_context, pipe)
        asyncio.run(
            application.execute_line(
                "prompts save fixture --purpose local --body safe "
                f"--schema-file {shlex.quote(str(schema))}"
            )
        )
        application.jobs.shutdown()

    rendered = stdout.getvalue() + stderr.getvalue()
    assert "[FILE_INPUT_TOO_LARGE]" in rendered
    assert str(schema) not in rendered
    assert private_payload not in rendered
    assert app_context.prompts.list() == []


@pytest.mark.parametrize("action", ("subtree", "quality"))
def test_repl_gedcom_ingress_failure_preserves_subtree_and_quality_outputs(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    action: str,
) -> None:
    source = tmp_path / f"private-{action}.ged"
    private_payload = "PRIVATE-PAYLOAD fictional genealogy"
    source.write_text(
        f"0 HEAD\n1 NOTE {private_payload}\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    output = tmp_path / f"{action}.out"
    output.write_bytes(b"sentinel\n")
    _set_file_limit(app_context, FileKind.GEDCOM, max_bytes=8)

    failed, rendered = _background_failure(
        shell_module,
        app_context,
        f"gedcom {action} {shlex.quote(str(source))} "
        f"--output {shlex.quote(str(output))} --root-person @I1@",
    )

    assert failed.state.value == "failed"
    assert failed.error_code == "FILE_INPUT_TOO_LARGE"
    assert str(source) not in rendered
    assert private_payload not in rendered
    assert output.read_bytes() == b"sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_repl_unresolved_gedcom_root_preserves_coded_sanitized_error(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
) -> None:
    source = tmp_path / "fictional-tree.ged"
    source.write_text(
        "0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    output = tmp_path / "quality.md"
    output.write_bytes(b"sentinel\n")
    requested = "PRIVATE-PAYLOAD fictional root person"

    failed, rendered = _background_failure(
        shell_module,
        app_context,
        "gedcom quality "
        f"{shlex.quote(str(source))} --output {shlex.quote(str(output))} "
        f"--root-person {shlex.quote(requested)}",
    )

    assert failed.state.value == "failed"
    assert failed.error_code == "GEDCOM_ROOT_PERSON_UNRESOLVED"
    assert failed.error_message == (
        "The requested GEDCOM root person was not found or is not unique."
    )
    assert requested not in rendered
    assert "ValueError" not in rendered
    assert output.read_bytes() == b"sentinel\n"


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_repl_sync_ingress_failure_creates_no_release_or_failure_artifact(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    operation: str,
) -> None:
    master = tmp_path / f"private-{operation}-master.ged"
    private_payload = "PRIVATE-PAYLOAD fictional sync input"
    master.write_text(
        f"0 HEAD\n1 NOTE {private_payload}\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    snapshot = tmp_path / "snapshot.ged"
    snapshot.write_text(
        "0 HEAD\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    release_root = tmp_path / "releases"
    _set_file_limit(app_context, FileKind.GEDCOM, max_bytes=8)

    if operation == "update":
        command = (
            "gedcom sync update "
            f"--master {shlex.quote(str(master))} "
            "--initialize-manifest "
            f"--snapshot fictional:other={shlex.quote(str(snapshot))} "
            f"--release-root {shlex.quote(str(release_root))} "
            "--no-quality-report"
        )
    else:
        command = (
            "gedcom sync rebase "
            f"--master {shlex.quote(str(master))} "
            f"--manifest {shlex.quote(str(manifest))} "
            f"--release-root {shlex.quote(str(release_root))} "
            "--reason fictional-regression"
        )

    failed, rendered = _background_failure(shell_module, app_context, command)

    assert failed.state.value == "failed"
    assert failed.error_code == "FILE_INPUT_TOO_LARGE"
    assert str(master) not in rendered
    assert private_payload not in rendered
    assert not release_root.exists()
    assert not list(tmp_path.glob(".gedcom-*"))
    assert not list(tmp_path.glob("failed-update-*"))


@pytest.mark.parametrize("operation", ("list", "query", "export"))
def test_repl_rootsmagic_entry_points_share_real_ingress_and_preserve_outputs(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    operation: str,
) -> None:
    tree = tmp_path / "private-fictional.rmtree"
    _write_repl_rootsmagic_tree(tree)
    tree_before = tree.read_bytes()
    output = tmp_path / "existing.ged"
    report = tmp_path / "existing.md"
    output.write_bytes(b"output sentinel\n")
    report.write_bytes(b"report sentinel\n")
    app_context.config.family_tree_dirs = [tmp_path]
    _set_file_limit(
        app_context,
        FileKind.ROOTSMAGIC,
        max_bytes=tree.stat().st_size - 1,
    )

    if operation == "list":
        with create_pipe_input() as pipe:
            application, stdout, stderr = _application(shell_module, app_context, pipe)
            asyncio.run(application.execute_line("rootsmagic list"))
            application.jobs.shutdown()
        rendered = stdout.getvalue() + stderr.getvalue()
        assert "[FILE_INPUT_TOO_LARGE]" in rendered
    else:
        query_sql = " ".join(("SELECT", "PersonID", "FROM", "PersonTable"))
        command = (
            f"rootsmagic query --tree {shlex.quote(str(tree))} --sql {shlex.quote(query_sql)}"
            if operation == "query"
            else (
                f"rootsmagic export --tree {shlex.quote(str(tree))} "
                f"--output {shlex.quote(str(output))} "
                f"--report {shlex.quote(str(report))}"
            )
        )
        failed, rendered = _background_failure(shell_module, app_context, command)
        assert failed.state.value == "failed"
        assert failed.error_code == "FILE_INPUT_TOO_LARGE"

    assert str(tree) not in rendered
    assert tree.read_bytes() == tree_before
    assert output.read_bytes() == b"output sentinel\n"
    assert report.read_bytes() == b"report sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_repl_ocr_ingress_failure_is_offline_and_payload_safe(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "private-ocr.txt"
    private_payload = "PRIVATE-PAYLOAD fictional OCR text"
    source.write_text(private_payload, encoding="utf-8")
    _set_file_limit(app_context, FileKind.OCR, max_bytes=4)
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)

    failed, rendered = _background_failure(
        shell_module,
        app_context,
        f"ocr extract --input {shlex.quote(str(source))} --provider none --model offline",
    )

    assert failed.state.value == "failed"
    assert failed.error_code == "FILE_INPUT_TOO_LARGE"
    assert str(source) not in rendered
    assert private_payload not in rendered
    provider_call.assert_not_called()


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


def test_slow_command_runs_as_inspectable_background_job_without_blocking_prompt(
    shell_module, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    worker_identifiers: list[int] = []

    def fake_dispatch(
        namespace: argparse.Namespace,
        context: AppContext,
        *,
        emit,
    ) -> int:
        assert namespace.command == "rootsmagic"
        assert namespace.action == "query"
        assert context is app_context
        worker_identifiers.append(threading.get_ident())
        started.set()
        assert release.wait(2)
        emit({"rows": 1}, False)
        return 0

    with create_pipe_input() as pipe:
        application, stdout, _stderr = _application(shell_module, app_context, pipe)
        monkeypatch.setattr(shell_module, "dispatch", fake_dispatch)
        loop_identifier = threading.get_ident()

        asyncio.run(
            application.execute_line("rootsmagic query --tree fictional --question 'Who is Ada?'")
        )
        assert started.wait(2)
        job = application.jobs.list()[0]
        assert job.state.value == "running"
        assert "j000001" in stdout.getvalue()

        asyncio.run(application.execute_line("jobs show j000001"))
        status_output = stdout.getvalue()
        assert '"progress": {' in status_output
        assert '"operation": "rootsmagic query"' in status_output
        release.set()
        completed = application.jobs.wait("j000001", timeout=2)
        application.jobs.shutdown()

    assert completed.state.value == "completed"
    assert completed.result == {"exit_code": 0, "output": [{"rows": 1}]}
    assert worker_identifiers == [worker_identifiers[0]]
    assert worker_identifiers[0] != loop_identifier


def test_malformed_gedcom_background_job_fails_with_sanitized_coded_error(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "private-malformed.ged"
    private_line = "PRIVATE-PAYLOAD malformed genealogy"
    malformed.write_text(f"0 HEAD\n{private_line}\n0 TRLR\n", encoding="utf-8")
    valid = tmp_path / "valid.ged"
    valid.write_text(
        "0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    output = tmp_path / "output.ged"
    output.write_bytes(b"sentinel\n")

    with create_pipe_input() as pipe:
        application, stdout, stderr = _application(shell_module, app_context, pipe)
        asyncio.run(
            application.execute_line(
                "gedcom merge "
                f"{shlex.quote(str(malformed))} {shlex.quote(str(valid))} "
                f"--output {shlex.quote(str(output))}"
            )
        )
        failed = application.jobs.wait("j000001", timeout=2)
        application.jobs.shutdown()

    assert failed.state.value == "failed"
    assert failed.error_code == "GEDCOM_PARSE_INVALID"
    assert output.read_bytes() == b"sentinel\n"
    rendered = stdout.getvalue() + stderr.getvalue() + (failed.error_message or "")
    assert private_line not in rendered
    assert str(malformed) not in rendered


def test_sync_service_error_marks_repl_job_failed(
    shell_module,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_manifest(
        namespace: argparse.Namespace,
        _context: AppContext,
        *,
        emit,
    ) -> int:
        del emit
        assert (namespace.command, namespace.action) == ("gedcom", "sync")
        raise AncestryError(
            "MANIFEST_INVALID",
            "The manifest has an unsupported structure.",
            exit_code=2,
        )

    monkeypatch.setattr(shell_module, "dispatch", fail_manifest)
    with create_pipe_input() as pipe:
        application, _stdout, _stderr = _application(shell_module, app_context, pipe)
        asyncio.run(application.execute_line("gedcom sync update --manifest private-manifest.json"))
        failed = application.jobs.wait("j000001", timeout=2)
        application.jobs.shutdown()

    assert failed.state.value == "failed"
    assert failed.error_code == "MANIFEST_INVALID"


@pytest.mark.parametrize("joined", (False, True))
def test_sync_resource_lock_uses_only_release_root(
    shell_module,
    tmp_path: Path,
    joined: bool,
) -> None:
    release_root = tmp_path / "releases"
    private_master = "~PRIVATE-NONEXISTENT/master.ged"
    release_argument = (
        [f"--release-root={release_root}"] if joined else ["--release-root", str(release_root)]
    )
    namespace = argparse.Namespace(
        command="gedcom",
        action="sync",
        sync_args=["update", "--master", private_master, *release_argument],
    )

    keys = shell_module.ReplApplication._resource_keys(object(), namespace)

    assert keys == (str(release_root.resolve()),)


def test_repl_sync_path_normalization_reaches_typed_ingress(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_master = Path("~PRIVATE-NONEXISTENT/master.ged")
    private_detail = "PRIVATE sync normalization failure"
    original_expanduser = Path.expanduser

    def reject_private_user(path: Path) -> Path:
        if path == private_master:
            raise RuntimeError(private_detail)
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", reject_private_user)
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)
    release_root = tmp_path / "releases"
    snapshot = tmp_path / "snapshot.ged"
    snapshot.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    command = (
        "gedcom sync update "
        f"--master {shlex.quote(str(private_master))} "
        "--initialize-manifest "
        f"--snapshot fictional:other={shlex.quote(str(snapshot))} "
        f"--release-root={shlex.quote(str(release_root))} "
        "--no-quality-report"
    )

    failed, rendered = _background_failure(shell_module, app_context, command)

    assert failed.state.value == "failed"
    assert failed.error_code == "FILE_INPUT_UNREADABLE"
    assert str(private_master) not in rendered
    assert private_detail not in rendered
    assert not release_root.exists()
    provider_call.assert_not_called()


def test_repl_sync_release_root_normalization_reaches_typed_ingress(
    shell_module,
    app_context: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_release_root = Path("~PRIVATE-NONEXISTENT/releases")
    private_detail = "PRIVATE release-root normalization failure"
    original_expanduser = Path.expanduser

    def reject_private_user(path: Path) -> Path:
        if path == private_release_root:
            raise RuntimeError(private_detail)
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", reject_private_user)
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    master.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    snapshot.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    command = (
        "gedcom sync update "
        f"--master {shlex.quote(str(master))} "
        "--initialize-manifest "
        f"--snapshot fictional:other={shlex.quote(str(snapshot))} "
        f"--release-root {shlex.quote(str(private_release_root))} "
        "--no-quality-report"
    )

    failed, rendered = _background_failure(shell_module, app_context, command)

    assert failed.state.value == "failed"
    assert failed.error_code == "FILE_INPUT_UNREADABLE"
    assert str(private_release_root) not in rendered
    assert private_detail not in rendered


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


def test_run_repl_uses_prompt_toolkit_stdout_patching(
    shell_module, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered: list[tuple[bool, bool]] = []

    @contextmanager
    def stdout_patch(*, raw: bool):
        entered.append((raw, asyncio.get_running_loop().is_running()))
        yield

    class FakeApplication:
        async def run_async(self) -> int:
            return 23

    monkeypatch.setattr(shell_module, "patch_stdout", stdout_patch)
    monkeypatch.setattr(
        shell_module,
        "ReplApplication",
        lambda _context, **_kwargs: FakeApplication(),
    )

    assert shell_module.run_repl(app_context) == 23
    assert entered == [(True, True)]


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
