"""Context-aware, privacy-preserving completion for the prompt-toolkit REPL."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from prompt_toolkit.completion import CompleteEvent, Completer, Completion, ThreadedCompleter
from prompt_toolkit.document import Document

from ancestryllm.console.router import SessionRouter
from ancestryllm.core.modules import (
    BUILTIN_MODULES,
    COMMAND_SPECIFICATIONS,
    ActionSpec,
    ArgumentAction,
    ArgumentCardinality,
    ArgumentSpec,
    CompletionKind,
)
from ancestryllm.core.secrets import ENVIRONMENT_NAMES

__all__ = ["CompletionSnapshot", "create_completer"]

_MAX_FILE_COMPLETIONS = 64
_ROOT_CONTROLS = ("exit", "help", "jobs", "quit", "use")
_ACTIVE_CONTROLS = (
    "back",
    "exit",
    "help",
    "info",
    "jobs",
    "quit",
    "run",
    "set",
    "show",
    "unset",
)
_DYNAMIC_SENSITIVE_KINDS = frozenset(
    {
        CompletionKind.MODEL,
        CompletionKind.PERSON,
        CompletionKind.PROMPT,
        CompletionKind.TREE,
        CompletionKind.WORKSPACE,
    }
)


@dataclass(frozen=True, slots=True)
class CompletionSnapshot:
    """Non-sensitive names collected outside the interactive completion path."""

    profiles: tuple[str, ...] = ()
    consents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "profiles", _normalized_names(self.profiles))
        object.__setattr__(self, "consents", _normalized_names(self.consents))


@dataclass(frozen=True, slots=True)
class _InputContext:
    completed: tuple[str, ...]
    current: str
    start_position: int


def _normalized_names(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {value for value in values if value},
            key=lambda value: (value.casefold(), value),
        )
    )


def _completion_input(text: str) -> _InputContext:
    """Tokenize an unfinished line without expansion and without rejecting open quotes."""

    tokens: list[str] = []
    token: list[str] = []
    token_started = False
    token_start = len(text)
    replacement_start = len(text)
    quote: str | None = None
    escaped = False

    for index, character in enumerate(text):
        if escaped:
            token.append(character)
            escaped = False
            continue
        if character == "\\":
            if not token_started:
                token_start = index
                replacement_start = index
                token_started = True
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            else:
                token.append(character)
            continue
        if character in {"'", '"'}:
            if not token_started:
                token_start = index
                replacement_start = index + 1
                token_started = True
            quote = character
            continue
        if character.isspace():
            if token_started:
                tokens.append("".join(token))
                token.clear()
                token_started = False
            token_start = index + 1
            replacement_start = index + 1
            continue
        if not token_started:
            token_start = index
            replacement_start = index
            token_started = True
        token.append(character)

    if escaped:
        token.append("\\")
    if not token_started:
        return _InputContext(tuple(tokens), "", 0)

    # Preserve a leading quote while replacing the unfinished token contents.
    start = (
        replacement_start if quote is not None and replacement_start > token_start else token_start
    )
    return _InputContext(tuple(tokens), "".join(token), -(len(text) - start))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


class _SpecCompleter(Completer):
    def __init__(
        self,
        router: SessionRouter,
        snapshot: CompletionSnapshot,
        safe_root: Path,
    ) -> None:
        self._router = router
        self._snapshot = snapshot
        self._safe_root = safe_root.expanduser().resolve()
        configured = (
            router.context.config.data_dir,
            *router.context.config.family_tree_dirs,
        )
        excluded: list[Path] = []
        for path in configured:
            resolved = path.expanduser().resolve()
            if _is_relative_to(resolved, self._safe_root):
                excluded.append(resolved)
        self._excluded_roots = tuple(excluded)

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        del complete_event
        context = _completion_input(document.text_before_cursor)
        suggestions = self._suggest(context.completed, context.current)
        for value in _prefix_matches(suggestions, context.current)[:_MAX_FILE_COMPLETIONS]:
            yield Completion(value, start_position=context.start_position)

    def _suggest(self, completed: tuple[str, ...], current: str) -> Sequence[str]:
        if not completed:
            return self._first_tokens()

        command = completed[0].casefold()
        if command == "use":
            return self._enabled_modules() if len(completed) == 1 else ()
        if command == "help":
            return self._first_tokens() if len(completed) == 1 else ()
        if command == "show" and self._router.active_module:
            return ("actions", "options") if len(completed) == 1 else ()
        if command == "run" and self._router.active_module:
            return self._complete_run(completed, current)
        if command == "set" and self._router.active_module:
            return self._complete_set(completed, current)
        if command == "unset" and self._router.active_module:
            return tuple(self._router.module_options) if len(completed) == 1 else ()
        if command in COMMAND_SPECIFICATIONS and self._command_is_available(command):
            return self._complete_command(command, completed, current)
        return ()

    def _first_tokens(self) -> tuple[str, ...]:
        if self._router.active_module:
            return _ACTIVE_CONTROLS
        commands = {name for name in COMMAND_SPECIFICATIONS if self._command_is_available(name)}
        commands.update(_ROOT_CONTROLS)
        return _normalized_names(commands)

    def _enabled_modules(self) -> tuple[str, ...]:
        return _normalized_names(self._router.enabled_modules)

    def _command_is_available(self, command: str) -> bool:
        return command not in BUILTIN_MODULES or command in self._router.enabled_modules

    def _complete_command(
        self, command: str, completed: tuple[str, ...], current: str
    ) -> Sequence[str]:
        specification = COMMAND_SPECIFICATIONS[command]
        if len(completed) == 1:
            return tuple(action.name for action in specification.actions)
        action = _action_named(specification.actions, completed[1])
        if action is None:
            return ()
        return self._complete_arguments(action, completed[2:], current)

    def _complete_run(self, completed: tuple[str, ...], current: str) -> Sequence[str]:
        module_id = self._router.active_module
        if module_id is None:
            return ()
        actions = COMMAND_SPECIFICATIONS[module_id].actions
        if len(completed) == 1:
            selected = self._router.module_options.get("action")
            if selected and current.startswith("-"):
                action = _action_named(actions, selected)
                return self._complete_arguments(action, (), current) if action else ()
            return tuple(action.name for action in actions)
        action = _action_named(actions, completed[1])
        if action is not None:
            return self._complete_arguments(action, completed[2:], current)
        selected_action = _action_named(actions, self._router.module_options.get("action"))
        return (
            self._complete_arguments(selected_action, completed[1:], current)
            if selected_action
            else ()
        )

    def _complete_set(self, completed: tuple[str, ...], current: str) -> Sequence[str]:
        module_id = self._router.active_module
        if module_id is None:
            return ()
        actions = COMMAND_SPECIFICATIONS[module_id].actions
        selected_action = self._router.module_options.get("action")
        selected = _action_named(actions, selected_action) if selected_action else None
        action_scope = (selected,) if selected is not None else actions

        if len(completed) == 1:
            names = {"action"}
            names.update(
                argument.name
                for action in action_scope
                for argument in action.arguments
                if argument.flags
            )
            return _normalized_names(names)
        if len(completed) != 2:
            return ()
        name = completed[1].replace("-", "_").lstrip("_")
        if name == "action":
            return tuple(action.name for action in actions)
        argument = next(
            (
                item
                for action in action_scope
                for item in action.arguments
                if item.flags
                and (item.name == name or name in {_normalized_flag(flag) for flag in item.flags})
            ),
            None,
        )
        return self._values_for(argument, current) if argument else ()

    def _complete_arguments(
        self, action: ActionSpec, completed: tuple[str, ...], current: str
    ) -> Sequence[str]:
        flags = {flag: argument for argument in action.arguments for flag in argument.flags}
        used: set[str] = set()
        pending: ArgumentSpec | None = None
        positional_count = 0

        for token in completed:
            if pending is not None:
                pending = None
                continue
            argument = flags.get(token)
            if argument is not None:
                used.add(argument.name)
                if argument.action is not ArgumentAction.STORE_TRUE:
                    pending = argument
                continue
            if token.startswith("-"):
                continue
            positional_count += 1

        if pending is not None:
            return self._values_for(pending, current)

        available_flags = tuple(
            flag
            for argument in action.arguments
            if argument.name not in used
            for flag in argument.flags
        )
        if current.startswith("-"):
            return available_flags

        positional = _positional_argument(action.arguments, positional_count)
        values = self._values_for(positional, current) if positional else ()
        return (*available_flags, *values)

    def _values_for(self, argument: ArgumentSpec | None, current: str) -> Sequence[str]:
        if argument is None or argument.completion in _DYNAMIC_SENSITIVE_KINDS:
            return ()
        if argument.sensitive and argument.completion is not CompletionKind.FILE:
            return ()
        if argument.completion is CompletionKind.CHOICES:
            return argument.choices
        if argument.completion is CompletionKind.MODULE:
            return self._enabled_modules()
        if argument.completion in {CompletionKind.PROFILE, CompletionKind.PROVIDER}:
            return self._snapshot.profiles
        if argument.completion is CompletionKind.CONSENT:
            return self._snapshot.consents
        if argument.completion is CompletionKind.KEYRING_REFERENCE:
            return tuple(sorted(ENVIRONMENT_NAMES))
        if argument.completion is CompletionKind.FILE:
            return self._file_values(current)
        return ()

    def _file_values(self, prefix: str) -> tuple[str, ...]:
        relative = Path(prefix or ".")
        if relative.is_absolute() or ".." in relative.parts:
            return ()
        if any(part.startswith(".") and part not in {".", ""} for part in relative.parts):
            return ()

        parent_text = str(relative.parent)
        parent_parts = () if parent_text == "." else relative.parent.parts
        directory = self._safe_root.joinpath(*parent_parts)
        if not self._safe_directory(directory):
            return ()

        name_prefix = "" if prefix.endswith(os.sep) else relative.name
        rendered_parent = "" if parent_text == "." else parent_text + os.sep
        matches: list[str] = []
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    iterator,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError:
            return ()
        for entry in entries:
            if len(matches) >= _MAX_FILE_COMPLETIONS:
                break
            if entry.name.startswith(".") or not entry.name.casefold().startswith(
                name_prefix.casefold()
            ):
                continue
            candidate = Path(entry.path)
            try:
                if entry.is_symlink() or self._excluded(candidate.resolve()):
                    continue
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            suffix = os.sep if is_directory else ""
            matches.append(f"{rendered_parent}{entry.name}{suffix}")
        return tuple(matches)

    def _safe_directory(self, directory: Path) -> bool:
        try:
            relative = directory.relative_to(self._safe_root)
        except ValueError:
            return False
        current = self._safe_root
        if current.is_symlink():
            return False
        for part in relative.parts:
            if part.startswith(".") or part == "..":
                return False
            current = current / part
            if current.is_symlink():
                return False
        try:
            resolved = current.resolve()
            return (
                resolved.is_dir()
                and _is_relative_to(resolved, self._safe_root)
                and not self._excluded(resolved)
            )
        except OSError:
            return False

    def _excluded(self, candidate: Path) -> bool:
        return any(_is_relative_to(candidate, excluded) for excluded in self._excluded_roots)


def _action_named(actions: Sequence[ActionSpec], name: str | None) -> ActionSpec | None:
    if name is None:
        return None
    return next((action for action in actions if action.name == name), None)


def _normalized_flag(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def _positional_argument(arguments: Sequence[ArgumentSpec], consumed: int) -> ArgumentSpec | None:
    positionals = tuple(argument for argument in arguments if argument.positional)
    if not positionals:
        return None
    index = 0
    for argument in positionals:
        if index == consumed:
            return argument
        if argument.cardinality in {ArgumentCardinality.ONE_OR_MORE, ArgumentCardinality.REMAINDER}:
            return argument
        index += 1
    return None


def _prefix_matches(values: Iterable[str], prefix: str) -> tuple[str, ...]:
    normalized = _normalized_names(values)
    folded = prefix.casefold()
    return tuple(value for value in normalized if value.casefold().startswith(folded))


def create_completer(
    router: SessionRouter,
    snapshot: CompletionSnapshot,
    safe_root: Path,
) -> Completer:
    """Create the shell-facing completer with filesystem work off the UI loop."""

    return ThreadedCompleter(_SpecCompleter(router, snapshot, safe_root))
