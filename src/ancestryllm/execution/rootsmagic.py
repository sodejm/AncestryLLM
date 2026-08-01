"""Focused immutable RootsMagic command handler."""

from __future__ import annotations

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.execution.common import (
    consent,
    optional_integer,
    optional_path,
    optional_text,
    path,
    structured_result,
    text,
)


class RootsMagicExecutor:
    def __init__(self, context: AppContext) -> None:
        self._context = context

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        from ancestryllm.rootsmagic.service import RootsMagicService

        service = RootsMagicService(self._context.config, self._context.llm)
        if invocation.key.action == "list":
            return CommandOutcome(structured_result(service.list_trees()))
        if invocation.key.action == "query":
            sql = optional_text(invocation, "sql")
            if sql is not None:
                value = service.query_sql(text(invocation, "tree"), sql)
            else:
                question = optional_text(invocation, "question")
                if question is None:
                    raise AncestryError(
                        "ARGUMENT_INVALID",
                        "RootsMagic query requires SQL or a natural-language question.",
                        exit_code=2,
                    )
                value = service.query_question(
                    text(invocation, "tree"),
                    question,
                    provider_id=text(invocation, "provider"),
                    model=text(invocation, "model"),
                    consent=consent(
                        self._context,
                        optional_text(invocation, "consent"),
                    ),
                )
            return CommandOutcome(structured_result(value))
        return CommandOutcome(
            structured_result(
                service.export(
                    text(invocation, "tree"),
                    path(invocation, "output"),
                    profile=text(invocation, "profile"),
                    gedcom_version=text(invocation, "gedcom_version"),
                    destination=text(invocation, "destination"),
                    root_person_id=optional_text(invocation, "root_person_id"),
                    scope=text(invocation, "scope"),
                    generations=optional_integer(invocation, "generations"),
                    living=text(invocation, "living"),
                    report_path=optional_path(invocation, "report"),
                )
            )
        )


__all__ = ["RootsMagicExecutor"]
