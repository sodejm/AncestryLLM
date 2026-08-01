"""Focused loss-minimal GEDCOM command handler."""

from __future__ import annotations

from pathlib import Path

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.results import MarkdownResult
from ancestryllm.core.context import AppContext
from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.execution.common import (
    consent,
    integer,
    optional_integer,
    optional_path,
    optional_text,
    path,
    structured_result,
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
            merge_result = service.merge(
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
            return CommandOutcome(structured_result(merge_result))
        if action == "subtree":
            subtree_result = service.subtree(
                path(invocation, "input"),
                path(invocation, "output"),
                root_person=text(invocation, "root_person"),
                scope=text(invocation, "scope"),
                generations=optional_integer(invocation, "generations"),
                gedcom_version=text(invocation, "gedcom_version"),
            )
            return CommandOutcome(structured_result(subtree_result))
        if action == "quality":
            quality_result = service.quality(
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
            return CommandOutcome(structured_result(quality_result))
        sync_result = service.sync(
            [
                text(invocation, "sync_command"),
                *text_values(invocation, "sync_args"),
            ]
        )
        return CommandOutcome(
            MarkdownResult(
                sync_result.output,
                structured=structured_result(sync_result),
            ),
            exit_code=sync_result.exit_code,
        )


__all__ = ["GedcomExecutor"]
