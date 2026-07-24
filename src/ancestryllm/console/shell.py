"""Default asynchronous prompt_toolkit REPL."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path
from typing import TextIO, cast

from prompt_toolkit import PromptSession
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.input import Input
from prompt_toolkit.output import Output

from ancestryllm.cli import dispatch
from ancestryllm.console.completion import CompletionSnapshot, create_completer
from ancestryllm.console.history import SecureHistory
from ancestryllm.console.multiline import AsyncPrompt, MultilineEditor
from ancestryllm.console.parser import split_repl_input
from ancestryllm.console.presentation import PresentationAdapter, to_plain
from ancestryllm.console.router import RouteKind, RouteResult, SessionRouter
from ancestryllm.console.security import (
    RedactingTextIO,
    credential_values,
    history_is_sensitive,
    redact_object,
)
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager

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
    ) -> None:
        self.context = context
        self.router = SessionRouter(context)
        self.safe_root = (safe_root or Path.cwd()).resolve()
        self.stdout = RedactingTextIO(stdout or sys.stdout, context)
        self.stderr = RedactingTextIO(stderr or sys.stderr, context)
        self.presenter = PresentationAdapter.for_file(cast(TextIO, self.stdout))
        self.error_presenter = PresentationAdapter.for_file(cast(TextIO, self.stderr))
        self.jobs = jobs or JobManager(redact=context.secrets.redact)
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
        self.multiline_editor = MultilineEditor(cast(AsyncPrompt, self.multiline_session))

    async def run_async(self) -> int:
        try:
            if not self.history.persistent:
                self.error_presenter.render(
                    "Persistent history is disabled because owner-only permissions could not be guaranteed."
                )
            while True:
                try:
                    command = await self.session.prompt_async(self.router.prompt)
                except EOFError:
                    return 0
                except KeyboardInterrupt:
                    continue
                if await self.execute_line(command):
                    return 0
        finally:
            await asyncio.to_thread(self.jobs.shutdown, wait=True)

    async def execute_line(self, command: str) -> bool:
        for value in credential_values(command):
            self.context.secrets.register_sensitive(value)
        try:
            if self._handle_job_control(command):
                return False
            result = await self._route(command)
            if result.kind is RouteKind.EXIT:
                return True
            if result.kind is RouteKind.EMPTY:
                return False
            if result.kind is RouteKind.OUTPUT:
                self.presenter.render(redact_object(result.value, self.context.secrets.redact))
                return False
            if result.invocation is None:
                raise AncestryError("REPL_ROUTE_INVALID", "The routed command had no invocation.")
            namespace = result.invocation.namespace
            if namespace.command == "secrets" and namespace.action == "set":
                await self._set_secret(namespace.name)
            elif self._should_background(namespace):
                snapshot = self.jobs.submit(
                    f"{namespace.command} {namespace.action}",
                    lambda: self._dispatch_job(namespace),
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
                AncestryError("INPUT_ERROR", self.context.secrets.redact(str(exc)), exit_code=2)
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
        raise AncestryError(
            "REPL_USAGE_ERROR",
            "Usage: jobs [list|show JOB_ID]",
            exit_code=2,
        )

    @staticmethod
    def _should_background(namespace: argparse.Namespace) -> bool:
        return (namespace.command, namespace.action) in _BACKGROUND_ACTIONS

    def _dispatch_job(self, namespace: argparse.Namespace) -> dict[str, object]:
        output: list[object] = []

        def capture(value: object, _json_output: bool = False) -> None:
            plain = to_plain(value)
            output.append(redact_object(plain, self.context.secrets.redact))

        exit_code = dispatch(namespace, self.context, emit=capture)
        return {"exit_code": exit_code, "output": output}

    def _resource_keys(self, namespace: argparse.Namespace) -> tuple[str, ...]:
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
            for index, token in enumerate(forwarded[:-1]):
                if token in {"--manifest", "--output", "--master"}:
                    values.append(forwarded[index + 1])
        return tuple(
            sorted(
                {
                    str(Path(value).expanduser().resolve())
                    for value in values
                    if isinstance(value, (str, Path))
                }
            )
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

    async def _set_secret(self, name: str) -> None:
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
        await asyncio.to_thread(self.context.secrets.set, name, value)
        self.presenter.render(f"Stored secret reference: {name}")


def run_repl(context: AppContext | None = None) -> int:
    """Run the asynchronous shell from the synchronous console entry point."""

    return asyncio.run(ReplApplication(context or AppContext.build()).run_async())
