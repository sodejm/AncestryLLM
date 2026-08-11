"""Application-owned validation and publication for RootsMagic GEDCOM exports."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.errors import AncestryError, FileIngressError
from ancestryllm.core.ingress import FileKind
from ancestryllm.core.publication import (
    StagedFileToken,
    claim_staged_path,
    cleanup_staged_path,
    paths_alias,
    publish_staged_bundle,
    staging_path,
    write_staged_text,
)
from ancestryllm.gedcom.model import GedcomParseError
from ancestryllm.gedcom.serializer import serialize_gedcom_document
from ancestryllm.gedcom.validator import validate_gedcom_document
from ancestryllm.rootsmagic.mapping import ExportReport, RootsMagicMapper

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = ["RootsMagicExportResult", "RootsMagicExporter"]


@dataclass(frozen=True, slots=True)
class RootsMagicExportResult:
    """Filesystem publication result retained for compatibility callers."""

    output_path: Path
    report_path: Path
    report: ExportReport


def _paths_nested(left: Path, right: Path) -> bool:
    """Fail closed when either output pathname is an ancestor of the other."""

    try:
        left_parts = tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in left.resolve(strict=False).parts
        )
        right_parts = tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in right.resolve(strict=False).parts
        )
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return True
    return (
        len(left_parts) < len(right_parts) and right_parts[: len(left_parts)] == left_parts
    ) or (len(right_parts) < len(left_parts) and left_parts[: len(right_parts)] == right_parts)


class RootsMagicExporter(RootsMagicMapper):
    """Compatibility exporter that adds validation and atomic publication."""

    @staticmethod
    def _scope_people(
        root: str | None,
        scope: str,
        generations: int | None,
        families: list[dict[str, Any]],
        children: list[dict[str, Any]],
        *,
        _checkpoint: Callable[[], None] | None = None,
    ) -> set[str] | None:
        """Keep legacy cancellation monkeypatches observable by mapping traversal."""

        return RootsMagicMapper._scope_people(
            root,
            scope,
            generations,
            families,
            children,
            _checkpoint=_checkpoint or cancellation_checkpoint,
        )

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> StagedFileToken:
        return write_staged_text(path, payload)

    def export(
        self,
        tree: Path,
        output: Path,
        *,
        profile: str = "portable",
        gedcom_version: str = "5.5.5",
        destination: str = "generic",
        root_person_id: str | None = None,
        scope: str = "connected",
        generations: int | None = None,
        living: str = "exclude",
        report_path: Path | None = None,
    ) -> RootsMagicExportResult:
        self._validate_mapping_options(
            profile=profile,
            gedcom_version=gedcom_version,
            destination=destination,
            living=living,
        )
        source_tree = self.reader.ingress.normalize_path(
            tree,
            FileKind.ROOTSMAGIC,
            absolute=True,
        )
        resolved_output = self.reader.ingress.normalize_path(
            output,
            FileKind.ROOTSMAGIC,
            resolve=True,
        )
        resolved_report = self.reader.ingress.normalize_path(
            report_path or resolved_output.with_suffix(".export.md"),
            FileKind.ROOTSMAGIC,
            resolve=True,
        )
        if paths_alias(source_tree, resolved_output):
            raise AncestryError(
                "EXPORT_OVERWRITE_INPUT", "Output must not overwrite RootsMagic data."
            )
        if (
            paths_alias(resolved_report, source_tree)
            or paths_alias(resolved_report, resolved_output)
            or _paths_nested(resolved_report, resolved_output)
        ):
            raise AncestryError(
                "EXPORT_REPORT_ALIAS",
                "The export report must not alias the source database or primary output.",
            )
        if not resolved_output.parent.is_dir() or not resolved_report.parent.is_dir():
            raise AncestryError(
                "EXPORT_OUTPUT_DIRECTORY_INVALID",
                "Export output and report parent directories must already exist.",
                "Create both local parent directories, then retry the export.",
                exit_code=2,
            )
        mapped = self._map_snapshot(
            source_tree,
            profile=profile,
            gedcom_version=gedcom_version,
            destination=destination,
            root_person_id=root_person_id,
            scope=scope,
            generations=generations,
            living=living,
        )
        try:
            validate_gedcom_document(
                mapped.content.document,
                checkpoint=cancellation_checkpoint,
            )
            output_payload = serialize_gedcom_document(mapped.content.document)
        except GedcomParseError as exc:
            raise AncestryError(
                "GEDCOM_VALIDATION_FAILED",
                "Generated GEDCOM content failed validation.",
                "Review the mapping options and retry the export.",
                exit_code=2,
            ) from exc

        staged_output: Path | None = None
        staged_report: Path | None = None
        try:
            staged_output = staging_path(resolved_output)
            staged_report = staging_path(resolved_report)
            output_token = self._atomic_write(staged_output, output_payload)
            claim_staged_path(staged_output, output_token)
            report_token = self._atomic_write(
                staged_report,
                mapped.legacy_report.markdown(
                    source_tree,
                    resolved_output,
                    omitted_records=dict(mapped.omitted_records),
                    sqlite_snapshot=mapped.sqlite_snapshot,
                ),
            )
            claim_staged_path(staged_report, report_token)

            def validate_source() -> None:
                try:
                    self.reader.verify_source(
                        mapped.source_path,
                        mapped.source_fingerprint,
                    )
                except FileIngressError as exc:
                    raise self._source_changed(exc) from exc

            publish_staged_bundle(
                (
                    (staged_output, resolved_output),
                    (staged_report, resolved_report),
                ),
                replace=os.replace,
                validate_after=validate_source,
            )
        except BaseException:
            if staged_output is not None:
                cleanup_staged_path(staged_output)
            if staged_report is not None:
                cleanup_staged_path(staged_report)
            raise
        return RootsMagicExportResult(
            resolved_output,
            resolved_report,
            mapped.legacy_report,
        )
