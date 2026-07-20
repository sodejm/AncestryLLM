"""MSFConsole-style local shell with dangerous built-ins disabled."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO, cast

import cmd2
from prompt_toolkit.shortcuts import prompt

from ancestryllm.cli import run_tokens
from ancestryllm.console.presentation import PresentationAdapter
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.modules import BUILTIN_MODULES, ModuleRegistry

_SECRET_NAME_MARKERS = (
    "api_key",
    "apikey",
    "access_key",
    "authorization",
    "credential",
    "passphrase",
    "passwd",
    "password",
    "private_key",
    "secret",
    "token",
)
_CREDENTIAL_URL = re.compile(r"://[^\s/@:]+:([^\s/@]+)@")


class _RedactingTextIO:
    """Text stream which scrubs registered values immediately before output."""

    def __init__(self, stream: TextIO, context: AppContext) -> None:
        self._stream = stream
        self._context = context

    @property
    def encoding(self) -> str:
        return self._stream.encoding or "utf-8"

    @property
    def errors(self) -> str | None:
        return self._stream.errors

    def fileno(self) -> int:
        return self._stream.fileno()

    def flush(self) -> None:
        if not self._stream.closed:
            self._stream.flush()

    def isatty(self) -> bool:
        return self._stream.isatty()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        return self._stream.write(self._context.secrets.redact(text))


def _is_secret_name(name: str) -> bool:
    normalized = name.casefold().replace("-", "_").replace(".", "_")
    return any(marker in normalized for marker in _SECRET_NAME_MARKERS)


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _credential_values(command: str) -> list[str]:
    """Extract values supplied beside secret-like names for in-memory redaction."""
    tokens = _split_command(command)
    values: list[str] = []
    if tokens and tokens[0].casefold() == "secrets":
        if len(tokens) > 3 and tokens[1].casefold() == "set":
            values.extend(tokens[3:])
        return [value for value in values if value]
    if len(tokens) > 2 and tokens[0].casefold() == "set" and _is_secret_name(tokens[1]):
        values.extend(tokens[2:])
    for index, token in enumerate(tokens[1:], start=1):
        name, separator, value = token.partition("=")
        if separator and _is_secret_name(name):
            values.append(value)
        elif _is_secret_name(token.lstrip("-")) and index + 1 < len(tokens):
            candidate = tokens[index + 1]
            if not candidate.startswith("-"):
                values.append(candidate)
        if token.casefold() == "bearer" and index + 1 < len(tokens):
            values.append(tokens[index + 1])
    values.extend(match.group(1) for match in _CREDENTIAL_URL.finditer(command))
    return [value for value in values if value]


def _history_is_sensitive(command: str) -> bool:
    if "\n" in command or "\r" in command:
        return True
    tokens = _split_command(command)
    if not tokens:
        return False
    if tokens[0].casefold() == "secrets":
        return True
    if _CREDENTIAL_URL.search(command):
        return True
    if any(token.casefold() == "bearer" for token in tokens):
        return True
    for token in tokens[1:]:
        name = token.partition("=")[0].lstrip("-")
        if _is_secret_name(name):
            return True
    return False


def _secure_history_file(path: Path) -> bool:
    """Create or repair a regular history file with owner-only permissions."""
    file_descriptor: int | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            return False
        flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        file_descriptor = os.open(path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            return False
        if hasattr(os, "fchmod"):
            os.fchmod(file_descriptor, 0o600)
        else:  # pragma: no cover - Windows lacks descriptor-based chmod
            path.chmod(0o600)
        return stat.S_IMODE(os.fstat(file_descriptor).st_mode) == 0o600
    except OSError:
        return False
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)


class AncestryConsole(cmd2.Cmd):
    intro = "AncestryLLM local research console. Type `modules` or `help`."

    def __init__(self, context: AppContext) -> None:
        history = context.config.data_dir / "console_history"
        self.context = context
        self._redacting_stdout = _RedactingTextIO(sys.stdout, context)
        self._redacting_stderr = _RedactingTextIO(sys.stderr, context)
        history_is_private = _secure_history_file(history)
        super().__init__(
            allow_cli_args=False,
            allow_redirection=False,
            auto_load_commands=False,
            include_ipy=False,
            persistent_history_file=str(history) if history_is_private else "",
            stdout=cast(TextIO, self._redacting_stdout),
        )
        self.registry = ModuleRegistry(context)
        self.active_module: str | None = None
        self.module_options: dict[str, str] = {}
        self.prompt = "ancestry > "
        for module in self.registry.load():
            self.register_command_set(module)
        for unsafe in ("shell", "py", "run_pyscript", "run_script", "edit", "shortcuts"):
            try:
                self.disable_command(unsafe, "Disabled by AncestryLLM security policy.")
            except (AttributeError, cmd2.CommandSetRegistrationError):
                pass
        self.exclude_from_history.append("secrets")
        self._remove_sensitive_history()
        if history_is_private:
            self._persist_history()
        else:
            self.perror(
                "Persistent history is disabled because owner-only permissions could not be "
                "guaranteed."
            )

    def _redact_text(self, value: str) -> str:
        return self.context.secrets.redact(value)

    def _redact_object(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, Mapping):
            return {
                self._redact_object(key): self._redact_object(item) for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact_object(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact_object(item) for item in value)
        if isinstance(value, (set, frozenset)):
            return type(value)(self._redact_object(item) for item in value)
        return self._redact_text(str(value))

    def _remove_sensitive_history(self) -> None:
        original_session_start = self.history.session_start_index
        safe_items = [
            item
            for item in self.history
            if not _history_is_sensitive(item.raw) and not _history_is_sensitive(item.expanded)
        ]
        self.history[:] = safe_items
        self.history.session_start_index = min(original_session_start, len(safe_items))

    def _persist_history(self) -> None:
        if not self.persistent_history_file:
            return
        history_path = Path(self.persistent_history_file)
        self._remove_sensitive_history()
        if not _secure_history_file(history_path):
            self.persistent_history_file = ""
            self.perror(
                "Persistent history was disabled because owner-only permissions could not be "
                "guaranteed."
            )
            return
        super()._persist_history()
        if not _secure_history_file(history_path):
            self.persistent_history_file = ""
            self.perror(
                "Persistent history was disabled after its permissions could not be repaired."
            )

    def onecmd(self, statement: cmd2.Statement | str, *, add_to_history: bool = True) -> bool:
        parsed = (
            statement
            if isinstance(statement, cmd2.Statement)
            else self._input_line_to_statement(statement)
        )
        command = parsed.raw or parsed.expanded_command_line
        for value in _credential_values(command):
            self.context.secrets.register_sensitive(value)
        keep_history = add_to_history and not _history_is_sensitive(command)
        with (
            contextlib.redirect_stdout(self._redacting_stdout),
            contextlib.redirect_stderr(self._redacting_stderr),
        ):
            secret_name = self._secret_set_name(parsed)
            if secret_name is not None:
                self._set_secret(secret_name)
                return False
            return super().onecmd(parsed, add_to_history=keep_history)

    @staticmethod
    def _secret_set_name(statement: cmd2.Statement) -> str | None:
        if statement.command.casefold() != "secrets":
            return None
        arguments = _split_command(str(statement))
        if len(arguments) == 2 and arguments[0].casefold() == "set":
            return arguments[1]
        return None

    def _set_secret(self, name: str) -> None:
        value = prompt(f"Secret value for {name}: ", is_password=True)
        self.context.secrets.register_sensitive(value)
        confirmation = prompt("Confirm secret value: ", is_password=True)
        self.context.secrets.register_sensitive(confirmation)
        if value != confirmation:
            raise AncestryError("SECRET_CONFIRMATION_FAILED", "Secret values did not match.")
        self.context.secrets.set(name, value)
        PresentationAdapter().render(f"Stored secret reference: {name}")

    def perror(self, *objects: Any, **kwargs: Any) -> None:
        super().perror(*(self._redact_object(item) for item in objects), **kwargs)

    def pexcept(self, exception: BaseException, **kwargs: Any) -> None:
        formatted_exception = self._redact_text(
            exception.render()
            if isinstance(exception, AncestryError)
            else self.format_exception(exception)
        )
        self.perror(formatted_exception, style=None, **kwargs)

    def ppaged(self, *objects: Any, **kwargs: Any) -> None:
        super().ppaged(*(self._redact_object(item) for item in objects), **kwargs)

    def add_alert(
        self,
        *,
        msg: Any | None = None,
        soft_wrap: bool = True,
        prompt: str | None = None,
    ) -> None:
        super().add_alert(
            msg=self._redact_object(msg) if msg is not None else None,
            soft_wrap=soft_wrap,
            prompt=self._redact_text(prompt) if prompt is not None else None,
        )

    def do_modules(self, _statement: cmd2.Statement) -> None:
        """List enabled built-in modules."""
        for descriptor in self.registry.descriptors():
            self.poutput(f"{descriptor.module_id:12} {descriptor.summary}")

    def do_use(self, statement: cmd2.Statement) -> None:
        """Enter a module context: use MODULE."""
        module_id = str(statement).strip()
        if module_id not in self.context.config.enabled_modules or module_id not in BUILTIN_MODULES:
            self.perror(f"Module is not enabled: {module_id}")
            return
        self.active_module = module_id
        self.module_options.clear()
        self.prompt = f"ancestry({module_id}) > "

    def do_info(self, _statement: cmd2.Statement) -> None:
        """Show active module metadata."""
        if not self.active_module:
            self.perror("Use a module first.")
            return
        descriptor = BUILTIN_MODULES[self.active_module]
        self.poutput(f"{descriptor.name}: {descriptor.summary}")
        self.poutput("Actions: " + ", ".join(descriptor.actions))

    def do_show(self, statement: cmd2.Statement) -> None:
        """Show active module actions or options."""
        target = str(statement).strip() or "options"
        if not self.active_module:
            self.perror("Use a module first.")
            return
        if target == "actions":
            self.poutput("\n".join(BUILTIN_MODULES[self.active_module].actions))
        elif target == "options":
            for name, value in sorted(self.module_options.items()):
                self.poutput(f"{name} = {value}")
        else:
            self.perror("Use `show actions` or `show options`.")

    def do_set(self, statement: cmd2.Statement | str) -> None:
        """Set an active-module option: set NAME VALUE."""
        parts = shlex.split(str(statement))
        if len(parts) < 2:
            self.perror("Usage: set NAME VALUE")
            return
        name = parts[0].replace("-", "_")
        if any(word in name.casefold() for word in ("secret", "password", "api_key", "token")):
            self.perror(
                "Secrets must be entered through `secrets set` and cannot be module options."
            )
            return
        self.module_options[name] = " ".join(parts[1:])

    def do_unset(self, statement: cmd2.Statement) -> None:
        """Unset an active-module option."""
        self.module_options.pop(str(statement).strip().replace("-", "_"), None)

    def do_run(self, statement: cmd2.Statement) -> None:
        """Run the selected module action using saved options."""
        if not self.active_module:
            self.perror("Use a module first.")
            return
        supplied = shlex.split(str(statement))
        action = supplied[0] if supplied else self.module_options.get("action")
        if not action:
            self.perror("Set `action` or pass an action to `run`.")
            return
        tokens = [self.active_module, action]
        for name, value in sorted(self.module_options.items()):
            if name == "action":
                continue
            tokens.extend(["--" + name.replace("_", "-"), value])
        tokens.extend(supplied[1:] if supplied else [])
        try:
            run_tokens(self.context, tokens)
        except SystemExit:
            return

    def do_back(self, _statement: cmd2.Statement) -> None:
        """Leave the active module context."""
        self.active_module = None
        self.module_options.clear()
        self.prompt = "ancestry > "
