"""Focused research-person command handler."""

from __future__ import annotations

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.core.context import AppContext
from ancestryllm.domain.models import LivingStatus
from ancestryllm.execution.common import structured_result, text


class PeopleExecutor:
    def __init__(self, context: AppContext) -> None:
        self._context = context

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        value: object
        if invocation.key.action == "list":
            value = self._context.research.list_people(text(invocation, "workspace"))
        else:
            value = self._context.research.add_person(
                text(invocation, "display_name"),
                LivingStatus(text(invocation, "living_status")),
                text(invocation, "notes"),
                text(invocation, "workspace"),
            )
        return CommandOutcome(structured_result(value))


__all__ = ["PeopleExecutor"]
