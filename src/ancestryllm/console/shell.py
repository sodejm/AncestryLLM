"""Default asynchronous prompt_toolkit REPL."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO, cast

from prompt_toolkit import PromptSession
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.patch_stdout import patch_stdout

from ancestryllm.application._compat import _CurrentProgressAdapter
from ancestryllm.application.results import CommandResult, FileArtifactResult
from ancestryllm.console.completion import CompletionSnapshot, create_completer
from ancestryllm.console.history import SecureHistory
from ancestryllm.console.multiline import AsyncPrompt, MultilineEditor
from ancestryllm.console.parser import split_repl_input
from ancestryllm.console.presentation import PresentationAdapter, to_plain
from ancestryllm.console.progress import JobProgressDisplay
from ancestryllm.console.router import RouteKind, RouteResult, SessionRouter
from ancestryllm.console.security import (
    RedactingTextIO,
    credential_values,
    history_is_sensitive,
    redact_object,
)
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager, JobReporter
from ancestryllm.terminal.dispatch import dispatch

if TYPE_CHECKING:
    import argparse

    from prompt_toolkit.input import Input
    from prompt_toolkit.output import Output

_BACKGROUND_ACTIONS = frozenset(
    {
        ("rootsmagic", "query"),
        ("rootsmagic", "export"),
        ("gedcom", "merge"),
        ("gedcom", "subtree"),
        ("gedcom", "quality"),
        ("gedcom", "sync"),
        ("ocr", "extract"),
        ("database", "backup"),
    }
)


def _item_name(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(value, (tuple, list)) and value and isinstance(value[0], str):
        return value[0]
    return str(value)


def build_completion_snapshot(context: AppContext) -> CompletionSnapshot:
    """Read non-sensitive metadata once, outside the completion callback."""

    try:
        profiles = tuple(
            sorted(_item_name(item) for item in context.provider_profiles.list_profiles())
        )
        consents = tuple(
            sorted(_item_name(item) for item in context.provider_profiles.list_consents())
        )
    except (AncestryError, OSError, ValueError):
        profiles = ()
        consents = ()
    return CompletionSnapshot(profiles=profiles, consents=consents)


class ReplApplication:
    """Coordinate prompt input, session routing, execution, and presentation."""

    def __init__(
        self,
        context: AppContext,
        *,
        safe_root: Path | None = None,
        input: Input | None = None,
        output: Output | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        jobs: JobManager | None = None,
        owns_context: bool = False,
    ) -> None:
        self.context = context
        self._owns_context = owns_context
        self._owned_context_closed = False
        self.router = SessionRouter(context)
        self.safe_root = (safe_root or Path.cwd()).resolve()
        self.stdout = RedactingTextIO(stdout or sys.stdout, context)
        self.stderr = RedactingTextIO(stderr or sys.stderr, context)
        self.presenter = PresentationAdapter.for_file(cast("TextIO", self.stdout))
        self.error_presenter = PresentationAdapter.for_file(cast("TextIO", self.stderr))
        self.progress_display = JobProgressDisplay(self.presenter.console)
        self.jobs = jobs or JobManager(redact=context.secrets.redact)
        self._unsubscribe_progress = self.jobs.subscribe(self.progress_display.handle)
        self.history = SecureHistory(
            context.config.data_dir / "repl_history",
            is_sensitive=lambda command: history_is_sensitive(command, self.router.active_module),
        )
        self.session: PromptSession[str] = PromptSession(
            history=self.history,
            completer=create_completer(
                self.router,
                build_completion_snapshot(context),
                self.safe_root,
            ),
            complete_while_typing=False,
            enable_history_search=True,
            input=input,
            output=output,
        )
        self.secret_session: PromptSession[str] = PromptSession(
            history=DummyHistory(),
            complete_while_typing=False,
            input=input,
            output=output,
        )
        self.multiline_session: PromptSession[str] = PromptSession(
            history=DummyHistory(),
            complete_while_typing=False,
            input=input,
            output=output,
        )
        self.multiline_editor = MultilineEditor(cast("AsyncPrompt", self.multiline_session))

    async def run_async(self) -> int:
        try:
            result = await self._run_prompt_loop()
        except BaseException:
            try:
                await self._shutdown()
            except BaseException:  # noqa: BLE001, S110 - preserve the prompt failure
                pass
            raise
        await self._shutdown()
        return result

    async def _run_prompt_loop(self) -> int:
        if not self.history.persistent:
            self.error_presenter.render(
                "Persistent history is disabled because owner-only permissions could not be guaranteed."
            )
        while True:
            try:
                command = await self.session.prompt_async(self.router.prompt)
            except EOFError:
                if await self._confirm_exit("EOF"):
                    return 0
                continue
            except KeyboardInterrupt:
                self._cancel_foreground()
                continue
            if await self.execute_line(command):
                return 0

    async def _shutdown(self) -> None:
        """Drain workers before closing resources, even if this task is cancelled."""

        shutdown_task = asyncio.create_task(asyncio.to_thread(self.jobs.shutdown, wait=True))
        pending_cancellation: asyncio.CancelledError | None = None
        while not shutdown_task.done():
            try:
                await asyncio.shield(shutdown_task)
            except asyncio.CancelledError as exc:
                if pending_cancellation is None:
                    pending_cancellation = exc
            except BaseException:  # noqa: BLE001 - re-raised after resource closure
                break

        shutdown_error: BaseException | None = None
        try:
            shutdown_task.result()
        except BaseException as exc:  # noqa: BLE001 - later resources must still close
            shutdown_error = exc

        close_error: BaseException | None = None
        close_callbacks = [self._unsubscribe_progress, self.progress_display.close]
        if self._owns_context:
            close_callbacks.append(self._close_owned_context)
        for close in close_callbacks:
            try:
                close()
            except BaseException as exc:  # noqa: BLE001 - later resources must still close
                if close_error is None:
                    close_error = exc

        if pending_cancellation is not None:
            raise pending_cancellation
        if shutdown_error is not None:
            raise shutdown_error
        if close_error is not None:
            raise close_error

    def _close_owned_context(self) -> None:
        if self._owned_context_closed:
            return
        self._owned_context_closed = True
        self.context.close()

    async def execute_line(self, command: str) -> bool:
        for value in credential_values(command):
            self.context.secrets.register_sensitive(value)
        try:
            if self._handle_job_control(command):
                return False
            result = await self._route(command)
            if result.kind is RouteKind.EXIT:
                return await self._confirm_exit(str(result.value or "exit"))
            if result.kind is RouteKind.EMPTY:
                return False
            if result.kind is RouteKind.OUTPUT:
                self.presenter.render(redact_object(result.value, self.context.secrets.redact))
                return False
            if result.invocation is None:
                raise AncestryError("REPL_ROUTE_INVALID", "The routed command had no invocation.")
            namespace = result.invocation.namespace
            if namespace.command == "secrets" and namespace.action == "set":
                await self._set_secret(namespace)
            elif self._should_background(namespace):
                snapshot = self.jobs.submit_with_progress(
                    f"{namespace.command} {namespace.action}",
                    lambda reporter: self._dispatch_job(namespace, reporter),
                    resource_keys=self._resource_keys(namespace),
                )
                self.presenter.render(
                    {
                        "job_id": snapshot.job_id,
                        "name": snapshot.name,
                        "state": snapshot.state,
                    }
                )
            else:
                await asyncio.to_thread(self._dispatch, namespace)
        except AncestryError as exc:
            self.error_presenter.render_error(
                AncestryError(
                    exc.code,
                    self.context.secrets.redact(exc.message),
                    self.context.secrets.redact(exc.remediation) if exc.remediation else None,
                    exc.exit_code,
                    redact_object(exc.details, self.context.secrets.redact),
                )
            )
        except (OSError, ValueError) as exc:
            self.error_presenter.render_error(
                AncestryError(
                    "INPUT_ERROR",
                    "The command input could not be processed safely.",
                    exit_code=2,
                    details={"error_type": type(exc).__name__},
                )
            )
        except Exception as exc:  # noqa: BLE001 - terminal boundary must sanitize failures
            self.error_presenter.render_error(
                AncestryError(
                    "REPL_COMMAND_FAILED",
                    "The interactive command failed.",
                    details={"error_type": type(exc).__name__},
                )
            )
        return False

    def _handle_job_control(self, command: str) -> bool:
        tokens = split_repl_input(command)
        if not tokens or tokens[0].casefold() != "jobs":
            return False
        if len(tokens) == 1 or tokens == ("jobs", "list"):
            self.presenter.render(self.jobs.list())
            return True
        if len(tokens) == 3 and tokens[1].casefold() == "show":
            self.presenter.render(self.jobs.get(tokens[2]))
            return True
        if len(tokens) == 3 and tokens[1].casefold() == "cancel":
            snapshot = self.jobs.cancel(tokens[2])
            self.presenter.render(
                {
                    "job_id": snapshot.job_id,
                    "state": snapshot.state,
                    "cancellation_requested": snapshot.cancellation_requested_at is not None,
                    "cancellation_pending": snapshot.cancellation_pending,
                }
            )
            return True
        raise AncestryError(
            "REPL_USAGE_ERROR",
            "Usage: jobs [list|show JOB_ID|cancel JOB_ID]",
            exit_code=2,
        )

    def _cancel_foreground(self) -> None:
        snapshot = self.jobs.cancel_foreground()
        if snapshot is None:
            self.presenter.render("No active background job to cancel; Ctrl-C acknowledged.")
            return
        self.presenter.render(
            {
                "job_id": snapshot.job_id,
                "state": snapshot.state,
                "cancellation_requested": True,
                "cancellation_pending": snapshot.cancellation_pending,
            }
        )

    async def _confirm_exit(self, trigger: str) -> bool:
        active = self.jobs.active()
        if not active:
            return True
        job_ids = ", ".join(snapshot.job_id for snapshot in active)
        prompt = f"{len(active)} active job(s) ({job_ids}). Choose wait, cancel, or stay [w/c/s]: "
        while True:
            try:
                answer = (await self.session.prompt_async(prompt)).strip().casefold()
            except KeyboardInterrupt:
                self.presenter.render("Exit cancelled; active jobs are still running.")
                return False
            except EOFError:
                self.presenter.render(
                    "Input closed with active jobs; waiting for safe shutdown before exit."
                )
                return True
            if answer in {"w", "wait"}:
                self.presenter.render("Waiting for active jobs before exit.")
                return True
            if answer in {"c", "cancel"}:
                cancelled = self.jobs.cancel_all()
                self.presenter.render(
                    {
                        "exit": trigger,
                        "cancellation_requested": [item.job_id for item in cancelled],
                    }
                )
                return True
            if answer in {"s", "stay"}:
                self.presenter.render("Exit cancelled; active jobs are still running.")
                return False
            self.error_presenter.render_error(
                AncestryError(
                    "REPL_EXIT_DECISION_REQUIRED",
                    "Choose wait, cancel, or stay before exiting with active jobs.",
                    exit_code=2,
                )
            )

    @staticmethod
    def _should_background(namespace: argparse.Namespace) -> bool:
        return (namespace.command, namespace.action) in _BACKGROUND_ACTIONS

    def _dispatch_job(
        self,
        namespace: argparse.Namespace,
        reporter: JobReporter,
    ) -> dict[str, object]:
        output: list[object] = []
        artifact_count = 0
        reporter.update(f"{namespace.command} {namespace.action}")

        def capture(result: CommandResult, _json_output: bool = False) -> None:
            nonlocal artifact_count
            reporter.check_cancelled()
            plain = to_plain(result)
            output.append(redact_object(plain, self.context.secrets.redact))
            if isinstance(result, FileArtifactResult):
                artifact_count += 1 + len(result.related_artifacts)

        exit_code = dispatch(
            namespace,
            self.context,
            emit=capture,
            progress=_CurrentProgressAdapter(reporter),
        )
        reporter.check_cancelled()
        if exit_code != 0:
            raise AncestryError(
                "COMMAND_EXIT_NONZERO",
                "The background command did not complete successfully.",
                "Review the command and retry after correcting the failure.",
                exit_code=exit_code,
                details={"exit_code": exit_code},
            )
        if artifact_count:
            noun = "artifact reference" if artifact_count == 1 else "artifact references"
            reporter.set_outcome(
                f"Saved {artifact_count} {noun}.",
                next_action=(f"Run jobs show {reporter.job_id} to inspect the saved {noun}."),
            )
        elif output:
            result_count = len(output)
            noun = "command result" if result_count == 1 else "command results"
            reporter.set_outcome(
                f"Saved {result_count} {noun}.",
                next_action=f"Run jobs show {reporter.job_id} to inspect the saved result.",
            )
        else:
            reporter.set_outcome(
                "The command completed without a saved result.",
                next_action=(f"Run jobs show {reporter.job_id} to inspect the retained job state."),
            )
        return {"exit_code": exit_code, "output": output}

    def _resource_keys(self, namespace: argparse.Namespace) -> tuple[str, ...]:
        def resource_key(value: str | Path) -> str:
            try:
                return str(Path(value).expanduser().resolve())
            except (OSError, RuntimeError, UnicodeError, ValueError):
                # Dispatch owns stable typed path errors. A malformed path must
                # not fail early merely because the job lock is best-effort.
                return str(value)

        values: list[object] = []
        action = (namespace.command, namespace.action)
        if action in {
            ("rootsmagic", "export"),
            ("gedcom", "merge"),
            ("gedcom", "subtree"),
            ("gedcom", "quality"),
        }:
            values.append(namespace.output)
        elif action == ("database", "backup"):
            values.extend((self.context.database.path, namespace.destination))
        elif action == ("gedcom", "sync"):
            forwarded = list(namespace.sync_args)
            for index, argument in enumerate(forwarded):
                if argument == "--release-root" and index + 1 < len(forwarded):
                    values.append(forwarded[index + 1])
                elif argument.startswith("--release-root="):
                    values.append(argument.partition("=")[2])
        return tuple(
            sorted({resource_key(value) for value in values if isinstance(value, (str, Path))})
        )

    async def _route(self, command: str) -> RouteResult:
        tokens = split_repl_input(command)
        target = self._multiline_target(tokens)
        if target is None:
            return self.router.route(command)
        option, prompt = target
        value = await self.multiline_editor.read(prompt)
        return self.router.route_tokens((*tokens, option, value))

    def _multiline_target(self, tokens: tuple[str, ...]) -> tuple[str, str] | None:
        if not tokens:
            return None
        module: str | None = None
        action: str | None = None
        supplied = tokens
        if len(tokens) >= 2 and tokens[0] in {"rootsmagic", "prompts"}:
            module, action = tokens[:2]
        elif self.router.active_module in {"rootsmagic", "prompts"} and tokens[0] == "run":
            module = self.router.active_module
            action = tokens[1] if len(tokens) >= 2 else self.router.module_options.get("action")

        if module == "rootsmagic" and action == "query":
            configured = self.router.module_options
            if (
                "--sql" not in supplied
                and "--question" not in supplied
                and "sql" not in configured
                and "question" not in configured
            ):
                return "--question", "Natural-language question (Esc+Enter to submit):\n"
        if module == "prompts" and action == "save":
            configured = self.router.module_options
            if (
                "--body" not in supplied
                and "--body-file" not in supplied
                and "body" not in configured
                and "body_file" not in configured
            ):
                return "--body", "Prompt body (Esc+Enter to submit):\n"
        return None

    def _dispatch(self, namespace: argparse.Namespace) -> int:
        with (
            contextlib.redirect_stdout(self.stdout),
            contextlib.redirect_stderr(self.stderr),
        ):
            return dispatch(namespace, self.context)

    def _dispatch_secret(self, namespace: argparse.Namespace, value: str) -> int:
        with (
            contextlib.redirect_stdout(self.stdout),
            contextlib.redirect_stderr(self.stderr),
        ):
            return dispatch(namespace, self.context, secret_value=value)

    async def _set_secret(self, namespace: argparse.Namespace) -> None:
        name = getattr(namespace, "name", None)
        if not isinstance(name, str):
            raise AncestryError(
                "ARGUMENT_INVALID",
                "Missing required command argument: name.",
                exit_code=2,
            )
        try:
            value = await self.secret_session.prompt_async(
                f"Secret value for {name}: ",
                is_password=True,
            )
            self.context.secrets.register_sensitive(value)
            confirmation = await self.secret_session.prompt_async(
                "Confirm secret value: ",
                is_password=True,
            )
        except (EOFError, KeyboardInterrupt) as exc:
            raise AncestryError(
                "SECRET_ENTRY_CANCELLED",
                "Secret entry was cancelled; no value was stored.",
            ) from exc
        self.context.secrets.register_sensitive(confirmation)
        if value != confirmation:
            raise AncestryError("SECRET_CONFIRMATION_FAILED", "Secret values did not match.")
        await asyncio.to_thread(self._dispatch_secret, namespace, value)


def run_repl(context: AppContext | None = None) -> int:
    """Run the asynchronous shell from the synchronous console entry point."""

    async def run() -> int:
        selected_context = context or AppContext.build()
        with patch_stdout(raw=True):
            return await ReplApplication(
                selected_context,
                owns_context=context is None,
            ).run_async()

    return asyncio.run(run())
