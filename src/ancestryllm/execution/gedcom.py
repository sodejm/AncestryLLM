"""Focused loss-minimal GEDCOM command handler."""

from __future__ import annotations

from pathlib import Path

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.core.context import AppContext
from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.execution.common import (
    consent,
    integer,
    optional_integer,
    optional_path,
    optional_text,
    path,
    text,
    text_values,
)


class GedcomExecutor:
    def __init__(self, context: AppContext, ingress: FileIngressPolicy) -> None:
        self._context = context
        self._ingress = ingress

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        from ancestryllm.gedcom.service import GedcomService

        service = GedcomService(
            self._context.llm,
            ingress=self._ingress,
            consent_lookup=self._context.provider_profiles.consent_grant,
            provider_timeout_seconds=self._context.config.provider_timeout_seconds,
        )
        action = invocation.key.action
        if action == "merge":
            return CommandOutcome(
                service.merge(
                    [Path(item) for item in text_values(invocation, "inputs")],
                    path(invocation, "output"),
                    root_person=optional_text(invocation, "root_person"),
                    quality_path=optional_path(invocation, "quality_report"),
                    gedcom_version=text(invocation, "gedcom_version"),
                    provider_id=text(invocation, "provider"),
                    model=text(invocation, "model"),
                    consent=consent(
                        self._context,
                        optional_text(invocation, "consent"),
                    ),
                    threshold=integer(invocation, "similarity_threshold"),
                )
            )
        if action == "subtree":
            return CommandOutcome(
                service.subtree(
                    path(invocation, "input"),
                    path(invocation, "output"),
                    root_person=text(invocation, "root_person"),
                    scope=text(invocation, "scope"),
                    generations=optional_integer(invocation, "generations"),
                    gedcom_version=text(invocation, "gedcom_version"),
                )
            )
        if action == "quality":
            return CommandOutcome(
                service.quality(
                    path(invocation, "input"),
                    path(invocation, "output"),
                    root_person=text(invocation, "root_person"),
                    provider_id=text(invocation, "provider"),
                    model=text(invocation, "model"),
                    consent=consent(
                        self._context,
                        optional_text(invocation, "consent"),
                    ),
                )
            )
        result = service.sync(
            [
                text(invocation, "sync_command"),
                *text_values(invocation, "sync_args"),
            ]
        )
        return CommandOutcome(
            result if invocation.json_output else None,
            exit_code=result.exit_code,
            plain_text=None if invocation.json_output else result.output,
        )


__all__ = ["GedcomExecutor"]
