"""Transport-neutral help rendered from the shared command specifications."""

from __future__ import annotations

from collections.abc import Sequence

from ancestryllm.core.commands import (
    ActionSpec,
    ArgumentAction,
    ArgumentCardinality,
    ArgumentSpec,
    CommandSpec,
)

__all__ = [
    "argument_help",
    "argument_metavar",
    "render_action_example",
    "render_action_help",
    "render_command_help",
    "render_root_help",
]


def render_root_help() -> str:
    """Return safe first-run guidance shared by terminal adapters."""

    return (
        "Run `ancestry` with no arguments to start the interactive console.\n"
        "Safe next steps:\n"
        "  ancestry --json database diagnose\n"
        "  ancestry modules list\n"
        "In the console, run `help MODULE` or `help MODULE ACTION` for nested help."
    )


def argument_metavar(specification: ArgumentSpec) -> str:
    """Return the stable, non-sensitive placeholder for an argument."""

    return specification.metavar or specification.name.upper()


def argument_help(specification: ArgumentSpec) -> str:
    """Add declared defaults and choices without duplicating command metadata."""

    details: list[str] = []
    if _has_display_default(specification.default):
        default = specification.default
        rendered = ", ".join(default) if isinstance(default, tuple) else str(default)
        details.append(f"default: {rendered}")
    if specification.choices:
        details.append(f"choices: {', '.join(specification.choices)}")
    if not details:
        return specification.help
    return f"{specification.help} ({'; '.join(details)})"


def _has_display_default(default: object) -> bool:
    return default is not None and default is not False and default != "" and default != ()


def _display_name(specification: ArgumentSpec, *, include_value: bool = True) -> str:
    metavar = argument_metavar(specification)
    if specification.positional:
        if specification.cardinality in {
            ArgumentCardinality.ONE_OR_MORE,
            ArgumentCardinality.REMAINDER,
        }:
            return f"{metavar}..."
        if specification.cardinality is ArgumentCardinality.OPTIONAL:
            return f"[{metavar}]"
        return metavar
    flag = specification.flags[0]
    if specification.action is not ArgumentAction.STORE or not include_value:
        return flag
    return f"{flag} {metavar}"


def _required_arguments(action: ActionSpec) -> tuple[ArgumentSpec, ...]:
    return tuple(
        argument
        for argument in action.arguments
        if (
            argument.positional
            and argument.cardinality
            not in {
                ArgumentCardinality.OPTIONAL,
                ArgumentCardinality.REMAINDER,
            }
        )
        or argument.required
    )


def _required_groups(action: ActionSpec) -> tuple[tuple[ArgumentSpec, ...], ...]:
    arguments_by_name = {argument.name: argument for argument in action.arguments}
    return tuple(
        tuple(arguments_by_name[name] for name in group.arguments if name in arguments_by_name)
        for group in action.exclusive_groups
        if group.required
    )


def _safe_example_value(specification: ArgumentSpec) -> str:
    if specification.example is not None:
        return specification.example
    metavar = argument_metavar(specification).casefold().replace("_", "-")
    if specification.choices:
        return specification.choices[0]
    return f"<{metavar}>"


def render_action_example(command: str, action: ActionSpec) -> str:
    """Build a symbolic example using only values declared safe in the specs."""

    parts = [command, action.name]
    for argument in _required_arguments(action):
        value = _safe_example_value(argument)
        if argument.positional:
            parts.append(value)
        elif argument.action is ArgumentAction.STORE_TRUE:
            parts.append(argument.flags[0])
        else:
            parts.extend((argument.flags[0], value))
    for group in action.exclusive_groups:
        if not group.required:
            continue
        selected = next(
            (argument for argument in action.arguments if argument.name == group.arguments[0]),
            None,
        )
        if selected is None:
            continue
        value = _safe_example_value(selected)
        if selected.positional:
            parts.append(value)
        elif selected.action is ArgumentAction.STORE_TRUE:
            parts.append(selected.flags[0])
        else:
            parts.extend((selected.flags[0], value))
    return " ".join(parts)


def _render_defaults(arguments: Sequence[ArgumentSpec]) -> str | None:
    values = []
    for argument in arguments:
        if not _has_display_default(argument.default):
            continue
        default = argument.default
        rendered = ",".join(default) if isinstance(default, tuple) else str(default)
        name = argument.flags[0] if argument.flags else argument_metavar(argument)
        values.append(f"{name}={rendered}")
    return f"Defaults: {', '.join(values)}" if values else None


def _render_choices(arguments: Sequence[ArgumentSpec]) -> str | None:
    values = []
    for argument in arguments:
        if not argument.choices:
            continue
        name = argument.flags[0] if argument.flags else argument_metavar(argument)
        values.append(f"{name}: {', '.join(argument.choices)}")
    return f"Choices: {'; '.join(values)}" if values else None


def render_action_help(command: str, action: ActionSpec) -> str:
    """Render nested action help for interactive and future adapters."""

    required = _required_arguments(action)
    required_groups = _required_groups(action)
    usage_parts = [command, action.name, *(_display_name(item) for item in required)]
    usage_parts.extend(
        f"({' | '.join(_display_name(item) for item in group)})" for group in required_groups
    )
    usage = " ".join(usage_parts)
    lines = [f"Usage: {usage}", action.help]
    required_details = [_display_name(item) for item in required]
    required_details.extend(
        f"one of {' or '.join(_display_name(item) for item in group)}" for group in required_groups
    )
    if required_details:
        lines.append(f"Required: {', '.join(required_details)}")
    defaults = _render_defaults(action.arguments)
    if defaults:
        lines.append(defaults)
    choices = _render_choices(action.arguments)
    if choices:
        lines.append(choices)
    lines.append(f"Example: {render_action_example(command, action)}")
    return "\n".join(lines)


def render_command_help(specification: CommandSpec) -> str:
    """Render a command summary and its available nested-help route."""

    actions = ", ".join(action.name for action in specification.actions)
    return (
        f"{specification.name}: {specification.help}\n"
        f"Actions: {actions}\n"
        f"Run `help {specification.name} ACTION` for syntax, inputs, defaults, and an example."
    )
