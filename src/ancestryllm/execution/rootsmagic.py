"""Focused immutable RootsMagic command handler."""

from __future__ import annotations

from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.dto import ArtifactRef
from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.results import FileArtifactResult
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.execution.common import (
    consent,
    optional_integer,
    optional_path,
    optional_text,
    path,
    structured_result,
    table_result,
    text,
)

_LIST_OPERATION = "rootsmagic.list"
_EXPORT_OPERATION = "rootsmagic.export"
_ROOTSMAGIC_MEDIA_TYPE = "application/vnd.sqlite3"
_GEDCOM_MEDIA_TYPE = "text/vnd.familysearch.gedcom"
_REPORT_MEDIA_TYPE = "text/markdown"
_ARTIFACT_COLUMNS = (
    "artifact_id",
    "media_type",
    "artifact_type",
    "size_bytes",
    "status",
    "sha256",
)


class RootsMagicExecutor:
    def __init__(self, context: AppContext) -> None:
        self._context = context

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        from ancestryllm.rootsmagic.service import RootsMagicService

        service = RootsMagicService(self._context.config, self._context.llm)
        if invocation.key.action == "list":
            registry = _ArtifactRegistry()
            artifacts = []
            for tree in service.list_trees():
                grant = registry.grant_input(
                    tree,
                    operation=_LIST_OPERATION,
                    media_type=_ROOTSMAGIC_MEDIA_TYPE,
                    artifact_type="rootsmagic_tree",
                )
                artifacts.append(
                    _artifact_record(registry.describe_input(grant, operation=_LIST_OPERATION))
                )
            return CommandOutcome(table_result(_ARTIFACT_COLUMNS, artifacts))
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
        output_path = path(invocation, "output")
        report_path = optional_path(invocation, "report")
        export_result = service.export(
            text(invocation, "tree"),
            output_path,
            profile=text(invocation, "profile"),
            gedcom_version=text(invocation, "gedcom_version"),
            destination=text(invocation, "destination"),
            root_person_id=optional_text(invocation, "root_person_id"),
            scope=text(invocation, "scope"),
            generations=optional_integer(invocation, "generations"),
            living=text(invocation, "living"),
            report_path=report_path,
        )
        registry = _ArtifactRegistry()
        output_grant = registry.grant_output(
            export_result.output_path,
            operation=_EXPORT_OPERATION,
            media_type=_GEDCOM_MEDIA_TYPE,
            artifact_type="gedcom_export",
        )
        report_grant = registry.grant_output(
            export_result.report_path,
            operation=_EXPORT_OPERATION,
            media_type=_REPORT_MEDIA_TYPE,
            artifact_type="export_report",
        )
        return CommandOutcome(
            FileArtifactResult(
                registry.describe_output(output_grant, operation=_EXPORT_OPERATION),
                related_artifacts=(
                    registry.describe_output(report_grant, operation=_EXPORT_OPERATION),
                ),
            )
        )


def _artifact_record(artifact: ArtifactRef) -> dict[str, object]:
    return {
        "artifact_id": artifact.artifact_id,
        "media_type": artifact.media_type,
        "artifact_type": artifact.artifact_type,
        "size_bytes": artifact.size_bytes,
        "status": artifact.status,
        "sha256": artifact.sha256,
    }


__all__ = ["RootsMagicExecutor"]
