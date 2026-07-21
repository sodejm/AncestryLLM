"""UI-independent state and routing for the interactive console."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ancestryllm.console.parser import ParsedInvocation, parse_repl_invocation, split_repl_input
from ancestryllm.console.security import is_secret_name
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.modules import (
    BUILTIN_MODULES,
    COMMAND_SPECIFICATIONS,
    ActionSpec,
    ArgumentAction,
    ArgumentSpec,
    ModuleRegistry,
)


class RouteKind(str, Enum):
    OUTPUT = "output"
    EXECUTE = "execute"
    EXIT = "exit"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class RouteResult:
    kind: RouteKind
    value: Any = None
    invocation: ParsedInvocation | None = None


@dataclass(slots=True)
class SessionRouter:
    """Own active-module state without importing a terminal UI library."""

    context: AppContext
    active_module: str | None = None
    module_options: dict[str, str] = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        return f"ancestry({self.active_module}) > " if self.active_module else "ancestry > "

    @property
    def enabled_modules(self) -> tuple[str, ...]:
        return tuple(item.module_id for item in ModuleRegistry(self.context).descriptors())

    def route(self, command: str) -> RouteResult:
        tokens = split_repl_input(command)
        if not tokens:
            return RouteResult(RouteKind.EMPTY)

        control = tokens[0].casefold()
        if control in {"exit", "quit"}:
            self._require_count(tokens, 1, control)
            return RouteResult(RouteKind.EXIT)
        if control == "help":
            return RouteResult(RouteKind.OUTPUT, self._help(tokens[1:]))
        if control == "modules" and len(tokens) == 1:
            return RouteResult(
                RouteKind.OUTPUT,
                [
                    {"module_id": item.module_id, "name": item.name, "summary": item.summary}
                    for item in ModuleRegistry(self.context).descriptors()
                ],
            )
        if control == "use":
            return self._use(tokens)
        if control == "back":
            return self._back(tokens)
        if control == "info":
            return self._info(tokens)
        if control == "show":
            return self._show(tokens)
        if control == "set":
            return self._set(tokens)
        if control == "unset":
            return self._unset(tokens)
        if control == "run":
            return self._run(tokens)

        command_name = tokens[0]
        if command_name in BUILTIN_MODULES and command_name not in self.enabled_modules:
            raise AncestryError(
                "MODULE_DISABLED",
                f"Module is not enabled: {command_name}",
                "Enable the module explicitly before using it in the interactive console.",
                exit_code=2,
            )
        invocation = parse_repl_invocation(tokens)
        return RouteResult(RouteKind.EXECUTE, invocation=invocation)

    @staticmethod
    def _require_count(tokens: tuple[str, ...], count: int, command: str) -> None:
        if len(tokens) != count:
            raise AncestryError(
                "REPL_USAGE_ERROR",
                f"Usage: {command}",
                exit_code=2,
            )

    def _require_active(self) -> str:
        if self.active_module is None:
            raise AncestryError(
                "REPL_MODULE_REQUIRED",
                "Use a module first.",
                "Run `modules`, then `use MODULE`.",
                exit_code=2,
            )
        return self.active_module

    def _use(self, tokens: tuple[str, ...]) -> RouteResult:
        if len(tokens) != 2:
            raise AncestryError("REPL_USAGE_ERROR", "Usage: use MODULE", exit_code=2)
        module_id = tokens[1]
        if module_id not in self.enabled_modules or module_id not in BUILTIN_MODULES:
            raise AncestryError(
                "MODULE_DISABLED", f"Module is not enabled: {module_id}", exit_code=2
            )
        self.active_module = module_id
        self.module_options.clear()
        return RouteResult(RouteKind.OUTPUT, f"Using module: {module_id}")

    def _back(self, tokens: tuple[str, ...]) -> RouteResult:
        self._require_count(tokens, 1, "back")
        self.active_module = None
        self.module_options.clear()
        return RouteResult(RouteKind.OUTPUT, "Returned to the root prompt.")

    def _info(self, tokens: tuple[str, ...]) -> RouteResult:
        self._require_count(tokens, 1, "info")
        module_id = self._require_active()
        descriptor = BUILTIN_MODULES[module_id]
        return RouteResult(
            RouteKind.OUTPUT,
            {
                "module_id": descriptor.module_id,
                "name": descriptor.name,
                "summary": descriptor.summary,
                "actions": list(descriptor.actions),
            },
        )

    def _show(self, tokens: tuple[str, ...]) -> RouteResult:
        module_id = self._require_active()
        if len(tokens) > 2:
            raise AncestryError("REPL_USAGE_ERROR", "Usage: show [actions|options]", exit_code=2)
        target = tokens[1].casefold() if len(tokens) == 2 else "options"
        if target == "actions":
            return RouteResult(RouteKind.OUTPUT, list(BUILTIN_MODULES[module_id].actions))
        if target == "options":
            return RouteResult(RouteKind.OUTPUT, dict(sorted(self.module_options.items())))
        raise AncestryError("REPL_USAGE_ERROR", "Usage: show [actions|options]", exit_code=2)

    def _set(self, tokens: tuple[str, ...]) -> RouteResult:
        module_id = self._require_active()
        if len(tokens) < 3:
            raise AncestryError("REPL_USAGE_ERROR", "Usage: set NAME VALUE", exit_code=2)
        name = tokens[1].replace("-", "_").lstrip("_")
        value = " ".join(tokens[2:])
        if is_secret_name(name):
            raise AncestryError(
                "REPL_SECRET_OPTION_REJECTED",
                "Secrets must be entered through `secrets set` and cannot be module options.",
                exit_code=2,
            )
        if name == "action":
            if value not in BUILTIN_MODULES[module_id].actions:
                raise AncestryError(
                    "REPL_ACTION_UNKNOWN", f"Unknown {module_id} action: {value}", exit_code=2
                )
        else:
            self._find_option(module_id, name)
        self.module_options[name] = value
        return RouteResult(RouteKind.OUTPUT, f"Set {name}.")

    def _unset(self, tokens: tuple[str, ...]) -> RouteResult:
        self._require_active()
        if len(tokens) != 2:
            raise AncestryError("REPL_USAGE_ERROR", "Usage: unset NAME", exit_code=2)
        name = tokens[1].replace("-", "_").lstrip("_")
        self.module_options.pop(name, None)
        return RouteResult(RouteKind.OUTPUT, f"Unset {name}.")

    def _run(self, tokens: tuple[str, ...]) -> RouteResult:
        module_id = self._require_active()
        supplied = list(tokens[1:])
        action = supplied.pop(0) if supplied else self.module_options.get("action")
        if not action:
            raise AncestryError(
                "REPL_ACTION_REQUIRED",
                "Set `action` or pass an action to `run`.",
                exit_code=2,
            )
        action_spec = self._action(module_id, action)
        invocation_tokens = [module_id, action]
        for name, value in sorted(self.module_options.items()):
            if name == "action":
                continue
            argument = self._find_option(module_id, name, action_spec)
            flag = argument.flags[0]
            if argument.action is ArgumentAction.STORE_TRUE:
                if value.casefold() in {"1", "true", "yes", "on"}:
                    invocation_tokens.append(flag)
                elif value.casefold() not in {"0", "false", "no", "off"}:
                    raise AncestryError(
                        "REPL_BOOLEAN_INVALID",
                        f"Expected true or false for {name}, received {value!r}.",
                        exit_code=2,
                    )
            else:
                invocation_tokens.extend((flag, value))
        invocation_tokens.extend(supplied)
        invocation = parse_repl_invocation(invocation_tokens)
        return RouteResult(RouteKind.EXECUTE, invocation=invocation)

    @staticmethod
    def _action(module_id: str, action_name: str) -> ActionSpec:
        for action in COMMAND_SPECIFICATIONS[module_id].actions:
            if action.name == action_name:
                return action
        raise AncestryError(
            "REPL_ACTION_UNKNOWN", f"Unknown {module_id} action: {action_name}", exit_code=2
        )

    @staticmethod
    def _find_option(module_id: str, name: str, action: ActionSpec | None = None) -> ArgumentSpec:
        actions = (action,) if action is not None else COMMAND_SPECIFICATIONS[module_id].actions
        for candidate_action in actions:
            for argument in candidate_action.arguments:
                normalized_flags = {flag.lstrip("-").replace("-", "_") for flag in argument.flags}
                if argument.flags and (argument.name == name or name in normalized_flags):
                    return argument
        raise AncestryError(
            "REPL_OPTION_UNKNOWN", f"Unknown {module_id} option: {name}", exit_code=2
        )

    def _help(self, tokens: tuple[str, ...]) -> str:
        if not tokens:
            return (
                "Root commands: modules, use MODULE, help [COMMAND], exit, quit. "
                "Enabled module commands can also be run directly."
            )
        command = tokens[0]
        if command in COMMAND_SPECIFICATIONS:
            specification = COMMAND_SPECIFICATIONS[command]
            actions = ", ".join(action.name for action in specification.actions)
            return f"{specification.name}: {specification.help}\nActions: {actions}"
        if self.active_module and command in {"info", "show", "set", "unset", "run", "back"}:
            return "Module commands: info, show [actions|options], set NAME VALUE, unset NAME, run [ACTION], back."
        raise AncestryError(
            "REPL_HELP_UNKNOWN", f"No help is available for: {command}", exit_code=2
        )
