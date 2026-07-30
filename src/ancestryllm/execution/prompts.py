"""Focused prompt-library command handler."""

from __future__ import annotations

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.ingress import FileIngressPolicy, FileKind
from ancestryllm.execution.common import (
    key_values,
    optional_integer,
    optional_path,
    optional_text,
    text,
    text_values,
)


class PromptsExecutor:
    def __init__(self, context: AppContext, ingress: FileIngressPolicy) -> None:
        self._context = context
        self._ingress = ingress

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        action = invocation.key.action
        if action == "list":
            return CommandOutcome(self._context.prompts.list())
        if action == "save":
            body = optional_text(invocation, "body")
            if body is None:
                body_file = optional_path(invocation, "body_file")
                if body_file is None:
                    raise AncestryError(
                        "ARGUMENT_INVALID",
                        "Prompt save requires body text or a body file.",
                        exit_code=2,
                    )
                body = self._ingress.read_text(body_file, FileKind.PROMPT_BODY)
            schema_file = optional_path(invocation, "schema_file")
            schema = (
                self._ingress.read_json(
                    schema_file,
                    FileKind.JSON_SCHEMA,
                    require_object=True,
                )
                if schema_file is not None
                else None
            )
            if schema is not None:
                assert isinstance(schema, dict)
            return CommandOutcome(
                self._context.prompts.save(
                    text(invocation, "name"),
                    text(invocation, "purpose"),
                    body,
                    list(text_values(invocation, "variable")),
                    schema,
                    list(text_values(invocation, "tag")),
                )
            )
        if action == "show":
            return CommandOutcome(
                self._context.prompts.get(
                    text(invocation, "name"),
                    optional_integer(invocation, "version"),
                )
            )
        return CommandOutcome(
            self._context.prompts.render(
                text(invocation, "name"),
                key_values(text_values(invocation, "value")),
                optional_integer(invocation, "version"),
            )
        )


__all__ = ["PromptsExecutor"]
