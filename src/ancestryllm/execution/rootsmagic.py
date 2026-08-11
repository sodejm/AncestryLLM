"""Focused immutable RootsMagic command handler."""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING, Protocol

from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.operations import TreeRecord
from ancestryllm.application.results import FileArtifactResult
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

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.application.ports import ProgressPort
    from ancestryllm.core.context import AppContext

_EXPORT_OPERATION = "rootsmagic.export"
_GEDCOM_MEDIA_TYPE = "text/vnd.familysearch.gedcom"
_REPORT_MEDIA_TYPE = "text/markdown"
_TREE_COLUMNS = ("tree_ref", "label", "immutable")
_TREE_REF_PREFIX = "tree_"


class _TreeLister(Protocol):
    def list_trees(self) -> list[Path]: ...


def _tree_ref(tree: Path) -> str:
    canonical = tree.resolve(strict=True)
    digest = hashlib.sha256(os.fsencode(str(canonical))).hexdigest()
    return f"{_TREE_REF_PREFIX}{digest}"


def _tree_entries(trees: list[Path]) -> list[tuple[Path, TreeRecord]]:
    entries: list[tuple[Path, TreeRecord]] = []
    paths_by_ref: dict[str, Path] = {}
    for tree in trees:
        canonical = tree.resolve(strict=True)
        reference = _tree_ref(canonical)
        previous = paths_by_ref.get(reference)
        if previous is not None and previous != canonical:
            raise AncestryError(
                "ROOTSMAGIC_TREE_REF_COLLISION",
                "Configured RootsMagic databases could not be assigned unique references.",
                "Remove the conflicting configuration entry and try again.",
            )
        paths_by_ref[reference] = canonical
        entries.append(
            (
                canonical,
                TreeRecord(
                    tree_ref=reference,
                    label=canonical.stem,
                    immutable=True,
                ),
            )
        )
    return entries


def _is_opaque_tree_ref(value: str) -> bool:
    digest = value.removeprefix(_TREE_REF_PREFIX)
    return (
        value.startswith(_TREE_REF_PREFIX)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def _resolve_tree_ref(service: _TreeLister, selection: str) -> str | Path:
    if not _is_opaque_tree_ref(selection):
        return selection
    for tree, record in _tree_entries(service.list_trees()):
        if record.tree_ref == selection:
            return tree
    raise AncestryError(
        "ROOTSMAGIC_TREE_NOT_FOUND",
        "No configured RootsMagic database matches the requested reference.",
        "List configured RootsMagic databases and select a current tree reference.",
    )


class RootsMagicExecutor:
    def __init__(
        self,
        context: AppContext,
        *,
        progress: ProgressPort | None = None,
    ) -> None:
        self._context = context
        self._progress = progress

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        from ancestryllm.rootsmagic.service import RootsMagicService

        service = RootsMagicService(
            self._context.config,
            self._context.llm,
            progress=self._progress,
        )
        if invocation.key.action == "list":
            records: list[dict[str, object]] = []
            for _tree, record in _tree_entries(service.list_trees()):
                records.append(
                    {
                        "tree_ref": record.tree_ref,
                        "label": record.label,
                        "immutable": record.immutable,
                    }
                )
            return CommandOutcome(table_result(_TREE_COLUMNS, records))
        if invocation.key.action == "query":
            selected_tree = _resolve_tree_ref(service, text(invocation, "tree"))
            sql = optional_text(invocation, "sql")
            if sql is not None:
                value = service.query_sql(selected_tree, sql)
            else:
                question = optional_text(invocation, "question")
                if question is None:
                    raise AncestryError(
                        "ARGUMENT_INVALID",
                        "RootsMagic query requires SQL or a natural-language question.",
                        exit_code=2,
                    )
                value = service.query_question(
                    selected_tree,
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
            _resolve_tree_ref(service, text(invocation, "tree")),
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


__all__ = ["RootsMagicExecutor"]
