"""Focused loss-minimal GEDCOM command handler."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.results import FileArtifactResult, MarkdownResult
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

if TYPE_CHECKING:
    from ancestryllm.core.context import AppContext
    from ancestryllm.core.ingress import FileIngressPolicy

_GEDCOM_MEDIA_TYPE = "text/vnd.familysearch.gedcom"
_REPORT_MEDIA_TYPE = "text/markdown"


class GedcomExecutor:
    """Dispatch GEDCOM commands through the application service boundary."""

    def __init__(self, context: AppContext, ingress: FileIngressPolicy) -> None:
        self._context = context
        self._ingress = ingress

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        """Dispatch loss-minimal GEDCOM operations and return serializable artifacts."""
        from ancestryllm.gedcom.service import GedcomService

        service = GedcomService(
            self._context.llm,
            ingress=self._ingress,
            consent_lookup=self._context.provider_profiles.consent_grant,
            provider_timeout_seconds=self._context.config.provider_timeout_seconds,
        )
        action = invocation.key.action
        if action == "merge":
            output_path = path(invocation, "output")
            quality_path = optional_path(invocation, "quality_report")
            service.merge(
                [Path(item) for item in text_values(invocation, "inputs")],
                output_path,
                root_person=optional_text(invocation, "root_person"),
                quality_path=quality_path,
                gedcom_version=text(invocation, "gedcom_version"),
                provider_id=text(invocation, "provider"),
                model=text(invocation, "model"),
                consent=consent(
                    self._context,
                    optional_text(invocation, "consent"),
                ),
                threshold=integer(invocation, "similarity_threshold"),
            )
            related = (
                ((quality_path, "quality_report", _REPORT_MEDIA_TYPE),)
                if quality_path is not None
                else ()
            )
            return CommandOutcome(
                _file_artifact_result(
                    output_path,
                    operation="gedcom.merge",
                    artifact_type="gedcom_merge",
                    media_type=_GEDCOM_MEDIA_TYPE,
                    related=related,
                )
            )
        if action == "subtree":
            output_path = path(invocation, "output")
            service.subtree(
                path(invocation, "input"),
                output_path,
                root_person=text(invocation, "root_person"),
                scope=text(invocation, "scope"),
                generations=optional_integer(invocation, "generations"),
                gedcom_version=text(invocation, "gedcom_version"),
            )
            return CommandOutcome(
                _file_artifact_result(
                    output_path,
                    operation="gedcom.subtree",
                    artifact_type="gedcom_subtree",
                    media_type=_GEDCOM_MEDIA_TYPE,
                )
            )
        if action == "quality":
            output_path = path(invocation, "output")
            service.quality(
                path(invocation, "input"),
                output_path,
                root_person=text(invocation, "root_person"),
                provider_id=text(invocation, "provider"),
                model=text(invocation, "model"),
                consent=consent(
                    self._context,
                    optional_text(invocation, "consent"),
                ),
            )
            return CommandOutcome(
                _file_artifact_result(
                    output_path,
                    operation="gedcom.quality",
                    artifact_type="quality_report",
                    media_type=_REPORT_MEDIA_TYPE,
                )
            )
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


def _file_artifact_result(
    output: Path,
    *,
    operation: str,
    artifact_type: str,
    media_type: str,
    related: tuple[tuple[Path, str, str], ...] = (),
) -> FileArtifactResult:
    registry = _ArtifactRegistry()
    output_grant = registry.grant_output(
        output,
        operation=operation,
        artifact_type=artifact_type,
        media_type=media_type,
    )
    related_refs = []
    for path_value, related_type, related_media_type in related:
        grant = registry.grant_output(
            path_value,
            operation=operation,
            artifact_type=related_type,
            media_type=related_media_type,
        )
        related_refs.append(registry.describe_output(grant, operation=operation))
    return FileArtifactResult(
        registry.describe_output(output_grant, operation=operation),
        related_artifacts=tuple(related_refs),
    )


__all__ = ["GedcomExecutor"]
