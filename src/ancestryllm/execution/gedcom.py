"""Focused loss-minimal GEDCOM command handler."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.dto import ProviderSelection
from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.operations import (
    GedcomSyncSnapshot,
    MergeRequest,
    QualityRequest,
    SubtreeRequest,
    SyncRequest,
)
from ancestryllm.application.results import FileArtifactResult, MarkdownResult
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.ingress import FileIngressPolicy, FileKind
from ancestryllm.domain.errors import DomainFailure
from ancestryllm.execution.common import (
    integer,
    optional_integer,
    optional_text,
    path,
    structured_result,
    text,
    text_values,
)
from ancestryllm.gedcom.sync_cli import ParsedSyncInvocation, parse_arguments
from ancestryllm.gedcom.sync_contracts import (
    SyncError,
    SyncRebaseCommand,
    SyncUpdateCommand,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ancestryllm.core.context import AppContext

_GEDCOM_MEDIA_TYPE = "text/vnd.familysearch.gedcom"
_REPORT_MEDIA_TYPE = "text/markdown"
_JSON_MEDIA_TYPE = "application/json"
_DIRECTORY_MEDIA_TYPE = "application/x-directory"


def _execute_with_terminal_error_compatibility[ResultT](
    operation: Callable[[], ResultT],
) -> ResultT:
    """Preserve the shipped CLI's sanitized codes behind the typed boundary."""

    try:
        return operation()
    except DomainFailure as failure:
        cause = failure.__cause__
        if isinstance(cause, AncestryError):
            raise cause from failure
        raise


class GedcomExecutor:
    """Dispatch GEDCOM commands through the application service boundary."""

    def __init__(self, context: AppContext, ingress: FileIngressPolicy) -> None:
        self._context = context
        self._ingress = ingress

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        """Dispatch loss-minimal GEDCOM operations and return serializable artifacts."""
        from ancestryllm.gedcom.service import GedcomService

        registry = _ArtifactRegistry()
        service = GedcomService(
            self._context.llm,
            ingress=self._ingress,
            consent_lookup=self._context.provider_profiles.consent_grant,
            provider_timeout_seconds=self._context.config.provider_timeout_seconds,
            artifacts=registry,
        )
        action = invocation.key.action
        if action == "merge":
            merge_result = _execute_with_terminal_error_compatibility(
                lambda: service.execute_merge(_merge_request(invocation, registry, self._ingress))
            )
            related = (
                (merge_result.quality_report,) if merge_result.quality_report is not None else ()
            )
            return CommandOutcome(
                FileArtifactResult(merge_result.gedcom, related_artifacts=related)
            )
        if action == "subtree":
            subtree_result = _execute_with_terminal_error_compatibility(
                lambda: service.execute_subtree(
                    _subtree_request(invocation, registry, self._ingress)
                )
            )
            return CommandOutcome(FileArtifactResult(subtree_result.gedcom))
        if action == "quality":
            quality_result = _execute_with_terminal_error_compatibility(
                lambda: service.execute_quality(
                    _quality_request(invocation, registry, self._ingress)
                )
            )
            return CommandOutcome(FileArtifactResult(quality_result.report))

        try:
            parsed = parse_arguments(
                [
                    text(invocation, "sync_command"),
                    *text_values(invocation, "sync_args"),
                ]
            )
        except SyncError as exc:
            raise exc.as_ancestry_error() from exc
        sync_result = _execute_with_terminal_error_compatibility(
            lambda: service.execute_sync(_sync_request(parsed, registry, self._ingress))
        )
        return CommandOutcome(
            MarkdownResult(
                "The GEDCOM synchronization completed.",
                structured=structured_result(sync_result),
            )
        )


def _provider(invocation: CommandInvocation) -> ProviderSelection:
    """Translate explicit provider options without resolving credentials."""

    return ProviderSelection(
        provider_id=text(invocation, "provider"),
        model_id=optional_text(invocation, "model") or None,
        consent_id=optional_text(invocation, "consent") or None,
    )


def _merge_request(
    invocation: CommandInvocation,
    registry: _ArtifactRegistry,
    ingress: FileIngressPolicy,
) -> MergeRequest:
    operation = "gedcom.merge"
    quality_path = optional_text(invocation, "quality_report")
    return MergeRequest(
        inputs=tuple(
            registry.grant_input(
                ingress.normalize_path(Path(item), FileKind.GEDCOM, absolute=True),
                operation=operation,
                artifact_type="gedcom",
                media_type=_GEDCOM_MEDIA_TYPE,
            )
            for item in text_values(invocation, "inputs")
        ),
        output=registry.grant_output(
            path(invocation, "output"),
            operation=operation,
            artifact_type="gedcom_merge",
            media_type=_GEDCOM_MEDIA_TYPE,
        ),
        quality_report=(
            registry.grant_output(
                Path(quality_path),
                operation=operation,
                artifact_type="quality_report",
                media_type=_REPORT_MEDIA_TYPE,
            )
            if quality_path is not None
            else None
        ),
        root_person_ref=optional_text(invocation, "root_person"),
        provider=_provider(invocation),
        similarity_threshold=integer(invocation, "similarity_threshold"),
        gedcom_version=text(invocation, "gedcom_version"),
    )


def _subtree_request(
    invocation: CommandInvocation,
    registry: _ArtifactRegistry,
    ingress: FileIngressPolicy,
) -> SubtreeRequest:
    operation = "gedcom.subtree"
    return SubtreeRequest(
        source=registry.grant_input(
            ingress.normalize_path(
                path(invocation, "input"),
                FileKind.GEDCOM,
                absolute=True,
            ),
            operation=operation,
            artifact_type="gedcom",
            media_type=_GEDCOM_MEDIA_TYPE,
        ),
        output=registry.grant_output(
            path(invocation, "output"),
            operation=operation,
            artifact_type="gedcom_subtree",
            media_type=_GEDCOM_MEDIA_TYPE,
        ),
        root_person_ref=text(invocation, "root_person"),
        scope=text(invocation, "scope"),
        generations=optional_integer(invocation, "generations"),
        gedcom_version=text(invocation, "gedcom_version"),
    )


def _quality_request(
    invocation: CommandInvocation,
    registry: _ArtifactRegistry,
    ingress: FileIngressPolicy,
) -> QualityRequest:
    operation = "gedcom.quality"
    return QualityRequest(
        source=registry.grant_input(
            ingress.normalize_path(
                path(invocation, "input"),
                FileKind.GEDCOM,
                absolute=True,
            ),
            operation=operation,
            artifact_type="gedcom",
            media_type=_GEDCOM_MEDIA_TYPE,
        ),
        output=registry.grant_output(
            path(invocation, "output"),
            operation=operation,
            artifact_type="quality_report",
            media_type=_REPORT_MEDIA_TYPE,
        ),
        root_person_ref=text(invocation, "root_person"),
        provider=_provider(invocation),
    )


def _sync_request(
    parsed: ParsedSyncInvocation,
    registry: _ArtifactRegistry,
    ingress: FileIngressPolicy,
) -> SyncRequest:
    operation = "gedcom.sync"
    command = parsed.command
    master = registry.grant_input(
        ingress.normalize_path(command.master, FileKind.GEDCOM, absolute=True),
        operation=operation,
        artifact_type="gedcom",
        media_type=_GEDCOM_MEDIA_TYPE,
    )
    release_root = registry.grant_output(
        ingress.normalize_path(command.release_root, FileKind.MANIFEST, absolute=True),
        operation=operation,
        artifact_type="sync-release-root",
        media_type=_DIRECTORY_MEDIA_TYPE,
    )
    if isinstance(command, SyncUpdateCommand):
        manifest = (
            registry.grant_input(
                ingress.normalize_path(command.manifest, FileKind.MANIFEST, absolute=True),
                operation=operation,
                artifact_type="sync-manifest",
                media_type=_JSON_MEDIA_TYPE,
            )
            if command.manifest is not None
            else None
        )
        return SyncRequest(
            sync_command="update",
            master=master,
            release_root=release_root,
            provider=ProviderSelection(
                provider_id=command.provider,
                model_id=parsed.model,
                consent_id=parsed.consent_id,
            ),
            manifest=manifest,
            snapshots=tuple(
                GedcomSyncSnapshot(
                    source_id=snapshot.source_id,
                    vendor=snapshot.vendor,
                    artifact=registry.grant_input(
                        ingress.normalize_path(
                            snapshot.path,
                            FileKind.GEDCOM,
                            absolute=True,
                        ),
                        operation=operation,
                        artifact_type="gedcom-snapshot",
                        media_type=_GEDCOM_MEDIA_TYPE,
                    ),
                    exported_at=snapshot.exported_at,
                )
                for snapshot in command.snapshots
            ),
            initialize_manifest=command.initialize_manifest,
            quality_root_person_ref=command.quality_root_person,
            quality_report_enabled=not command.no_quality_report,
            dry_run=command.dry_run,
            gedcom_version=command.gedcom_version,
            automatic_identity_resolution=command.auto,
        )

    if not isinstance(command, SyncRebaseCommand):
        raise AssertionError("unsupported typed GEDCOM sync command")
    return SyncRequest(
        sync_command="rebase",
        master=master,
        release_root=release_root,
        provider=ProviderSelection(),
        manifest=registry.grant_input(
            ingress.normalize_path(command.manifest, FileKind.MANIFEST, absolute=True),
            operation=operation,
            artifact_type="sync-manifest",
            media_type=_JSON_MEDIA_TYPE,
        ),
        quality_report_enabled=False,
        dry_run=command.dry_run,
        accept_manual_deletions=command.accept_manual_deletions,
        reason=command.reason,
    )


__all__ = ["GedcomExecutor"]
