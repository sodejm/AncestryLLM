"""Application service for GEDCOM operations shared by every interface."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ancestryllm.application._artifacts import _ArtifactRegistry
    from ancestryllm.llm.policy import ConsentGrant
    from ancestryllm.llm.service import LLMService

from ancestryllm.application.dto import ArtifactAccess, ProviderSelection
from ancestryllm.application.errors import domain_failure_from_exception
from ancestryllm.application.genealogy import GenealogyAggregate
from ancestryllm.application.operations import (
    ChangeSummary,
    GedcomMergeRequest,
    GedcomMergeResult,
    GedcomQualityRequest,
    GedcomQualityResult,
    GedcomSubtreeRequest,
    GedcomSubtreeResult,
    GedcomSyncRequest,
    ProvenanceRecord,
    QualitySummary,
)
from ancestryllm.application.operations import (
    GedcomSyncResult as GedcomSyncServiceResult,
)
from ancestryllm.application.ports import CancellationPort, NeverCancelled
from ancestryllm.core.cancellation import CancellationError
from ancestryllm.core.errors import AncestryError, ProviderError
from ancestryllm.core.ingress import FileFingerprint, FileIngressPolicy, FileKind
from ancestryllm.core.publication import (
    claim_staged_path,
    cleanup_staged_path,
    paths_alias,
    publish_staged_bundle,
    staging_path,
)
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode
from ancestryllm.domain.genealogy import (
    ChangeKind,
    GenealogyChange,
    GenealogyIdentity,
    GenealogyProvenance,
    GenealogyQualityFinding,
    QualityKind,
)
from ancestryllm.gedcom.contracts import IdentityResolver, QualityResolution, QualityResolver
from ancestryllm.gedcom.graph import (
    connected_tree_pointers,
    resolve_root_person,
    scoped_tree_pointers,
)
from ancestryllm.gedcom.identity import (
    DEFAULT_SIMILARITY_THRESHOLD,
    IndividualRecord,
    MergeDecision,
    build_dedup_prompt,
    dedup_response_schema,
    enrich_relationship_context,
    individual_from_record,
    merge_records,
)
from ancestryllm.gedcom.parser import GedcomParseError, GedcomRecord, load_sources
from ancestryllm.gedcom.quality import (
    QUALITY_AI_LIMIT,
    QualityReport,
    analyze_quality,
    build_quality_prompt,
    quality_annotations_from_payload,
    quality_response_schema,
    refine_quality_report_with_ai,
)
from ancestryllm.gedcom.serialization import (
    SUPPORTED_GEDCOM_VERSIONS,
    write_gedcom,
    write_quality_report,
)
from ancestryllm.gedcom.sync import (
    SOURCE_ID_RE,
    SUPPORTED_VENDORS,
    SyncCommand,
    SyncRebaseCommand,
    SyncSnapshotInput,
    SyncUpdateCommand,
    execute_sync_command,
)
from ancestryllm.gedcom.sync import (
    execute_sync as execute_sync_arguments,
)
from ancestryllm.llm.contracts import DataClass, GenerationRequest, Message

__all__ = [
    "GedcomOperationResult",
    "GedcomService",
    "GedcomSyncResult",
]


@dataclass(frozen=True, slots=True)
class GedcomOperationResult:
    """Compatibility result retained for the shipped terminal adapters."""

    output_path: Path
    people_read: int
    people_written: int
    quality_path: Path | None = None


@dataclass(frozen=True, slots=True)
class _GedcomGenealogyExecution:
    """Private path-bearing execution result used by compatibility facades."""

    operation: GedcomOperationResult
    root_person_ref: str | None
    changes: ChangeSummary
    quality: QualitySummary
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True, slots=True)
class _GedcomQualityExecution:
    """Private path-bearing quality result used by compatibility facades."""

    output_path: Path
    quality: QualitySummary


@dataclass(frozen=True, slots=True)
class GedcomSyncResult:
    """Serializable result for an incremental GEDCOM synchronization."""

    exit_code: int
    output: str
    error: str = ""
    committed: bool = False


def _opaque_ref(namespace: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"{namespace}:{digest}"


def _at_contract_boundary[ResultT](operation: Callable[[], ResultT]) -> ResultT:
    """Translate current implementation failures into the stable domain contract."""

    try:
        return operation()
    except CancellationError as exc:
        raise DomainFailure(DomainFailureCode.CANCELLED) from exc
    except DomainFailure:
        raise
    except Exception as exc:
        raise domain_failure_from_exception(exc) from exc


def _stable_code(value: str, fallback: str) -> str:
    normalized = "".join(
        character.casefold() if character.isalnum() else "-" for character in value
    ).strip("-")
    return (normalized or fallback)[:96]


def _quality_values(
    report: QualityReport | None,
) -> tuple[GenealogyQualityFinding, ...]:
    if report is None:
        return ()
    severity_kinds = {
        "critical": QualityKind.ERROR,
        "high": QualityKind.ERROR,
        "medium": QualityKind.WARNING,
        "low": QualityKind.INFORMATION,
    }
    return tuple(
        GenealogyQualityFinding(
            subject_ref=_opaque_ref("quality", finding.finding_id),
            kind=severity_kinds.get(finding.severity.casefold(), QualityKind.INFORMATION),
            rule_code=_stable_code(finding.code, "quality-finding"),
        )
        for finding in report.findings
    )


def _merge_aggregate(
    people: list[IndividualRecord],
    pointer_map: dict[str, str],
    decisions: list[MergeDecision],
    report: QualityReport | None,
    include_people: set[str] | None,
) -> GenealogyAggregate:
    grouped_sources: dict[str, list[str]] = {}
    for person in people:
        canonical_pointer = pointer_map.get(person.pointer, person.pointer)
        if include_people is not None and canonical_pointer not in include_people:
            continue
        grouped_sources.setdefault(canonical_pointer, []).append(person.pointer)

    identities: list[GenealogyIdentity] = []
    changes: list[GenealogyChange] = []
    provenance: list[GenealogyProvenance] = []
    for canonical_pointer, source_pointers in sorted(grouped_sources.items()):
        result_ref = _opaque_ref("person", canonical_pointer)
        source_refs = tuple(
            _opaque_ref("source", pointer) for pointer in sorted(set(source_pointers))
        )
        identities.append(GenealogyIdentity(result_ref, source_refs))
        changes.append(
            GenealogyChange(
                result_ref,
                ChangeKind.UPDATED if len(source_refs) > 1 else ChangeKind.UNCHANGED,
            )
        )
        provenance.append(
            GenealogyProvenance(
                result_ref,
                source_refs,
                "identity-merged" if len(source_refs) > 1 else "identity-preserved",
            )
        )

    for decision in decisions:
        if decision.disposition == "merged":
            continue
        left_pointer = pointer_map.get(decision.left_pointer, decision.left_pointer)
        right_pointer = pointer_map.get(decision.right_pointer, decision.right_pointer)
        if include_people is not None and not {
            left_pointer,
            right_pointer,
        }.issubset(include_people):
            continue
        signature = "|".join(
            (
                *sorted((decision.left_pointer, decision.right_pointer)),
                decision.disposition,
                *sorted(decision.conflicts),
            )
        )
        warning_codes = tuple(
            _stable_code(conflict, "identity-conflict") for conflict in decision.conflicts
        ) or (_stable_code(decision.disposition, "identity-retained"),)
        changes.append(
            GenealogyChange(
                _opaque_ref("conflict", signature),
                ChangeKind.CONFLICT,
                warning_codes,
            )
        )

    return GenealogyAggregate(
        identities=tuple(identities),
        changes=tuple(changes),
        quality_findings=_quality_values(report),
        provenance=tuple(provenance),
    )


def _subtree_aggregate(
    people: list[IndividualRecord],
    keep_people: set[str],
) -> GenealogyAggregate:
    identities: list[GenealogyIdentity] = []
    changes: list[GenealogyChange] = []
    provenance: list[GenealogyProvenance] = []
    for pointer in sorted(person.pointer for person in people if person.pointer in keep_people):
        result_ref = _opaque_ref("person", pointer)
        source_ref = _opaque_ref("source", pointer)
        identities.append(GenealogyIdentity(result_ref, (source_ref,)))
        changes.append(GenealogyChange(result_ref, ChangeKind.UNCHANGED))
        provenance.append(GenealogyProvenance(result_ref, (source_ref,), "subtree-selected"))
    return GenealogyAggregate(
        identities=tuple(identities),
        changes=tuple(changes),
        provenance=tuple(provenance),
    )


class GedcomService:
    def __init__(
        self,
        llm: LLMService | None = None,
        ingress: FileIngressPolicy | None = None,
        *,
        consent_lookup: Callable[[str], ConsentGrant] | None = None,
        provider_timeout_seconds: float = 60.0,
        artifacts: _ArtifactRegistry | None = None,
    ) -> None:
        self.llm = llm
        self.ingress = ingress or FileIngressPolicy()
        self.consent_lookup = consent_lookup
        self.provider_timeout_seconds = provider_timeout_seconds
        self._artifacts = artifacts

    def _require_artifacts(self) -> _ArtifactRegistry:
        if self._artifacts is None:
            raise DomainFailure(DomainFailureCode.INTERNAL)
        return self._artifacts

    def _consent_for(self, selection: ProviderSelection) -> ConsentGrant | None:
        if not selection.network_allowed:
            return None
        if selection.consent_id is None or self.consent_lookup is None:
            raise DomainFailure(DomainFailureCode.PROVIDER_CONSENT_REQUIRED)
        return self.consent_lookup(selection.consent_id)

    def _people_and_sources(
        self,
        paths: list[Path],
    ) -> tuple[
        list[Any],
        list[GedcomRecord],
        list[IndividualRecord],
        dict[Path, FileFingerprint],
    ]:
        fingerprints = {path: self.ingress.fingerprint(path, FileKind.GEDCOM) for path in paths}
        try:
            sources = load_sources(
                paths,
                self.ingress,
                {path: fingerprint.snapshot for path, fingerprint in fingerprints.items()},
                validate_structure=True,
            )
        except GedcomParseError as exc:
            raise AncestryError(
                "GEDCOM_PARSE_INVALID",
                "A GEDCOM input contains invalid syntax.",
                "Correct the malformed GEDCOM structure and try again.",
                exit_code=2,
                details={"error_type": type(exc).__name__},
            ) from exc
        self._verify_sources(fingerprints)
        source_records = [record for source in sources for record in source.records]
        people = [
            individual_from_record(record) for record in source_records if record.tag == "INDI"
        ]
        return (
            sources,
            source_records,
            enrich_relationship_context(people, source_records),
            fingerprints,
        )

    def _verify_sources(self, fingerprints: dict[Path, FileFingerprint]) -> None:
        for path, fingerprint in fingerprints.items():
            self.ingress.verify(path, FileKind.GEDCOM, fingerprint)

    @staticmethod
    def _resolve_root_person(
        requested: str,
        records: list[IndividualRecord],
        source_pointer_maps: list[dict[str, str]],
        merged_pointer_map: dict[str, str],
    ) -> str:
        """Resolve a root without exposing the requested genealogy value."""

        if requested.startswith("person:"):
            matches = {
                record.pointer
                for record in records
                if _opaque_ref("person", record.pointer) == requested
            }
            resolved = next(iter(matches)) if len(matches) == 1 else None
        else:
            try:
                resolved = resolve_root_person(
                    requested,
                    records,
                    source_pointer_maps,
                    merged_pointer_map,
                )
            except ValueError:
                resolved = None
        if resolved is None:
            raise AncestryError(
                "GEDCOM_ROOT_PERSON_UNRESOLVED",
                "The requested GEDCOM root person was not found or is not unique.",
                "Use an existing unique GEDCOM pointer or exact unique full name.",
                exit_code=2,
            )
        return resolved

    def _require_provider(self, provider_id: str) -> LLMService:
        if provider_id == "none":
            raise AncestryError(
                "PROVIDER_REQUIRED",
                "This operation requires an explicitly selected provider.",
            )
        if self.llm is None:
            raise AncestryError("LLM_SERVICE_UNAVAILABLE", "No modular LLM service is configured.")
        return self.llm

    def _identity_resolver(
        self,
        provider_id: str,
        model: str,
        consent: ConsentGrant | None,
        verify_inputs: Callable[[], None] | None = None,
    ) -> IdentityResolver:
        llm = self._require_provider(provider_id)

        def resolve(left: Any, right: Any) -> dict[str, object]:
            if verify_inputs is not None:
                verify_inputs()
            schema: dict[str, object] = dedup_response_schema()
            request = GenerationRequest(
                provider_id=provider_id,
                model=model,
                module_id="gedcom",
                purpose="identity_adjudication",
                messages=(
                    Message(
                        role="system",
                        content="Adjudicate identity only. Never delete genealogy evidence.",
                    ),
                    Message(
                        role="user",
                        content=(
                            "<untrusted_genealogy_data>\n"
                            + build_dedup_prompt(left, right)
                            + "\n</untrusted_genealogy_data>"
                        ),
                    ),
                ),
                response_schema=schema,
                data_classes=frozenset({DataClass.POSSIBLY_LIVING_PERSON}),
                max_output_tokens=1_000,
                timeout_seconds=self.provider_timeout_seconds,
            )
            result = llm.generate(request, consent)
            if verify_inputs is not None:
                verify_inputs()
            if not isinstance(result.parsed, dict):
                raise ProviderError(
                    "PROVIDER_OUTPUT_INVALID",
                    "The model returned an invalid identity decision.",
                    "Retry with a model that supports the required structured output.",
                    details={"result_type": type(result.parsed).__name__},
                )
            verdict = dict(result.parsed)
            verdict["_provider"] = result.provider_id
            verdict["_model"] = result.model
            return verdict

        return resolve

    def _quality_resolver(
        self,
        provider_id: str,
        model: str,
        consent: ConsentGrant | None,
        verify_inputs: Callable[[], None] | None = None,
    ) -> QualityResolver:
        llm = self._require_provider(provider_id)

        def resolve(report: QualityReport) -> QualityResolution:
            if verify_inputs is not None:
                verify_inputs()
            request = GenerationRequest(
                provider_id=provider_id,
                model=model,
                module_id="gedcom",
                purpose="quality_refinement",
                messages=(
                    Message(
                        role="system",
                        content=(
                            "Explain deterministic quality findings only. "
                            "Never alter genealogy evidence or assert new facts."
                        ),
                    ),
                    Message(
                        role="user",
                        content=(
                            "<untrusted_genealogy_findings>\n"
                            + build_quality_prompt(report)
                            + "\n</untrusted_genealogy_findings>"
                        ),
                    ),
                ),
                response_schema=quality_response_schema(),
                data_classes=frozenset({DataClass.POSSIBLY_LIVING_PERSON}),
                max_output_tokens=2_000,
                timeout_seconds=self.provider_timeout_seconds,
            )
            result = llm.generate(request, consent)
            if verify_inputs is not None:
                verify_inputs()
            if not isinstance(result.parsed, dict):
                raise ProviderError(
                    "PROVIDER_OUTPUT_INVALID",
                    "The model returned invalid quality annotations.",
                    "Retry with a model that supports the required structured output.",
                    details={"result_type": type(result.parsed).__name__},
                )
            allowed = {finding.finding_id for finding in report.findings[:QUALITY_AI_LIMIT]}
            try:
                annotations = quality_annotations_from_payload(result.parsed, allowed)
            except (TypeError, ValueError) as exc:
                raise ProviderError(
                    "PROVIDER_OUTPUT_INVALID",
                    "The model returned invalid quality annotations.",
                    "Retry with a model that supports the required structured output.",
                    details={"error_type": type(exc).__name__},
                ) from exc
            return QualityResolution(
                annotations=annotations,
                provider_id=result.provider_id,
                model=result.model,
                remote=result.remote,
            )

        return resolve

    def _sync_identity_resolver(
        self,
        provider_id: str,
        model: str,
        consent_name: str | None,
    ) -> IdentityResolver:
        consent: ConsentGrant | None = None
        if consent_name:
            if self.consent_lookup is None:
                raise AncestryError(
                    "CONSENT_SERVICE_UNAVAILABLE",
                    "The configured consent profile cannot be resolved.",
                )
            consent = self.consent_lookup(consent_name)
        return self._identity_resolver(provider_id, model, consent)

    def _merge(
        self,
        input_files: list[Path],
        output: Path,
        *,
        root_person: str | None = None,
        quality_path: Path | None = None,
        gedcom_version: str = "5.5.5",
        provider_id: str = "none",
        model: str = "",
        consent: ConsentGrant | None = None,
        threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
        cancellation: CancellationPort | None = None,
    ) -> _GedcomGenealogyExecution:
        cancellation_port = cancellation or NeverCancelled()
        cancellation_port.check_cancelled()
        if len(input_files) < 2:
            raise AncestryError(
                "GEDCOM_INPUT_REQUIRED", "Merge requires at least two GEDCOM files."
            )
        resolved_inputs = [
            self.ingress.normalize_path(path, FileKind.GEDCOM, absolute=True)
            for path in input_files
        ]
        resolved_output = self.ingress.normalize_path(
            output,
            FileKind.GEDCOM,
            resolve=True,
        )
        if any(paths_alias(resolved_output, item) for item in resolved_inputs):
            raise AncestryError(
                "GEDCOM_OVERWRITE_INPUT",
                "Output must not overwrite an input GEDCOM.",
                exit_code=2,
            )
        report_path = (
            self.ingress.normalize_path(
                quality_path,
                FileKind.GEDCOM,
                resolve=True,
            )
            if quality_path is not None
            else None
        )
        if report_path is not None and (
            paths_alias(report_path, resolved_output)
            or any(paths_alias(report_path, item) for item in resolved_inputs)
        ):
            raise AncestryError(
                "GEDCOM_REPORT_ALIAS",
                "The quality report must not alias an input GEDCOM or the primary output.",
                exit_code=2,
            )
        sources, source_records, people, fingerprints = self._people_and_sources(resolved_inputs)

        def verify_inputs() -> None:
            self._verify_sources(fingerprints)

        pointer_map: dict[str, str] = {}
        decisions: list[MergeDecision] = []
        resolver: IdentityResolver | None = None
        if provider_id != "none":
            resolver = self._identity_resolver(
                provider_id,
                model,
                consent,
                verify_inputs,
            )
        merged = merge_records(
            people,
            threshold=threshold,
            auto=True,
            identity_resolver=resolver,
            pointer_map=pointer_map,
            decisions=decisions,
        )
        include_people: set[str] | None = None
        include_families: set[str] | None = None
        root_pointer: str | None = None
        if root_person:
            root_pointer = self._resolve_root_person(
                root_person, merged, [source.pointer_map for source in sources], pointer_map
            )
            include_people, include_families = connected_tree_pointers(
                root_pointer, merged, source_records, pointer_map
            )
        staged_output: Path | None = None
        staged_report: Path | None = None
        report: QualityReport | None = None
        try:
            staged_output = staging_path(resolved_output)
            output_token = write_gedcom(
                merged,
                staged_output,
                source_documents=sources,
                pointer_map=pointer_map,
                include_individuals=include_people,
                include_families=include_families,
                gedcom_version=gedcom_version,
            )
            claim_staged_path(staged_output, output_token)
            artifacts = [(staged_output, resolved_output)]
            if report_path is not None:
                if root_pointer is None:
                    raise AncestryError(
                        "QUALITY_ROOT_REQUIRED", "Quality reporting requires a root person."
                    )
                report = analyze_quality(
                    merged,
                    source_records,
                    sources,
                    root_pointer,
                    pointer_map=pointer_map,
                    merge_decisions=decisions,
                    output_file=str(resolved_output),
                )
                staged_report = staging_path(report_path)
                report_token = write_quality_report(report, staged_report)
                claim_staged_path(staged_report, report_token)
                artifacts.append((staged_report, report_path))
            cancellation_port.check_cancelled()
            publish_staged_bundle(
                artifacts,
                replace=os.replace,
                validate_after=verify_inputs,
            )
        except BaseException:
            if staged_output is not None:
                cleanup_staged_path(staged_output)
            if staged_report is not None:
                cleanup_staged_path(staged_report)
            raise
        operation = GedcomOperationResult(
            resolved_output,
            len(people),
            len(include_people) if include_people is not None else len(merged),
            report_path,
        )
        aggregate = _merge_aggregate(
            people,
            pointer_map,
            decisions,
            report,
            include_people,
        )
        return _GedcomGenealogyExecution(
            operation=operation,
            root_person_ref=root_pointer,
            changes=aggregate.change_summary(),
            quality=aggregate.quality_summary(),
            provenance=aggregate.provenance_records(),
        )

    def merge(
        self,
        input_files: list[Path],
        output: Path,
        *,
        root_person: str | None = None,
        quality_path: Path | None = None,
        gedcom_version: str = "5.5.5",
        provider_id: str = "none",
        model: str = "",
        consent: ConsentGrant | None = None,
        threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> GedcomOperationResult:
        """Return the shipped compatibility result for terminal adapters."""

        return self._merge(
            input_files,
            output,
            root_person=root_person,
            quality_path=quality_path,
            gedcom_version=gedcom_version,
            provider_id=provider_id,
            model=model,
            consent=consent,
            threshold=threshold,
        ).operation

    def _subtree(
        self,
        input_file: Path,
        output: Path,
        *,
        root_person: str,
        scope: str = "connected",
        generations: int | None = None,
        gedcom_version: str = "5.5.5",
        cancellation: CancellationPort | None = None,
    ) -> _GedcomGenealogyExecution:
        cancellation_port = cancellation or NeverCancelled()
        cancellation_port.check_cancelled()
        source_path = self.ingress.normalize_path(
            input_file,
            FileKind.GEDCOM,
            absolute=True,
        )
        output_path = self.ingress.normalize_path(
            output,
            FileKind.GEDCOM,
            resolve=True,
        )
        if paths_alias(source_path, output_path):
            raise AncestryError(
                "GEDCOM_OVERWRITE_INPUT",
                "Output must not overwrite the input GEDCOM.",
                exit_code=2,
            )
        sources, source_records, people, fingerprints = self._people_and_sources([source_path])
        root_pointer = self._resolve_root_person(
            root_person,
            people,
            [sources[0].pointer_map],
            {},
        )
        keep_people, keep_families = scoped_tree_pointers(
            root_pointer, people, source_records, scope, generations
        )
        staged_output = staging_path(output_path)
        try:
            output_token = write_gedcom(
                people,
                staged_output,
                source_documents=sources,
                include_individuals=keep_people,
                include_families=keep_families,
                gedcom_version=gedcom_version,
            )
            claim_staged_path(staged_output, output_token)
            cancellation_port.check_cancelled()
            publish_staged_bundle(
                ((staged_output, output_path),),
                replace=os.replace,
                validate_after=lambda: self._verify_sources(fingerprints),
            )
        except BaseException:
            cleanup_staged_path(staged_output)
            raise
        operation = GedcomOperationResult(output_path, len(people), len(keep_people))
        aggregate = _subtree_aggregate(people, keep_people)
        return _GedcomGenealogyExecution(
            operation=operation,
            root_person_ref=root_pointer,
            changes=aggregate.change_summary(),
            quality=aggregate.quality_summary(),
            provenance=aggregate.provenance_records(),
        )

    def subtree(
        self,
        input_file: Path,
        output: Path,
        *,
        root_person: str,
        scope: str = "connected",
        generations: int | None = None,
        gedcom_version: str = "5.5.5",
    ) -> GedcomOperationResult:
        """Return the shipped compatibility result for terminal adapters."""

        return self._subtree(
            input_file,
            output,
            root_person=root_person,
            scope=scope,
            generations=generations,
            gedcom_version=gedcom_version,
        ).operation

    def _quality(
        self,
        input_file: Path,
        output: Path,
        *,
        root_person: str,
        provider_id: str = "none",
        model: str = "",
        consent: ConsentGrant | None = None,
        cancellation: CancellationPort | None = None,
    ) -> _GedcomQualityExecution:
        cancellation_port = cancellation or NeverCancelled()
        cancellation_port.check_cancelled()
        source_path = self.ingress.normalize_path(
            input_file,
            FileKind.GEDCOM,
            absolute=True,
        )
        output_path = self.ingress.normalize_path(
            output,
            FileKind.GEDCOM,
            resolve=True,
        )
        if paths_alias(source_path, output_path):
            raise AncestryError(
                "GEDCOM_REPORT_ALIAS",
                "The quality report must not alias the immutable input GEDCOM.",
                exit_code=2,
            )
        sources, source_records, people, fingerprints = self._people_and_sources([source_path])

        def verify_inputs() -> None:
            self._verify_sources(fingerprints)

        root_pointer = self._resolve_root_person(
            root_person,
            people,
            [sources[0].pointer_map],
            {},
        )
        report = analyze_quality(
            people, source_records, sources, root_pointer, output_file=str(source_path)
        )
        if provider_id != "none":
            report = refine_quality_report_with_ai(
                report,
                self._quality_resolver(
                    provider_id,
                    model,
                    consent,
                    verify_inputs,
                ),
            )
        staged_output = staging_path(output_path)
        try:
            output_token = write_quality_report(report, staged_output)
            claim_staged_path(staged_output, output_token)
            cancellation_port.check_cancelled()
            publish_staged_bundle(
                ((staged_output, output_path),),
                replace=os.replace,
                validate_after=verify_inputs,
            )
        except BaseException:
            cleanup_staged_path(staged_output)
            raise
        aggregate = GenealogyAggregate(quality_findings=_quality_values(report))
        return _GedcomQualityExecution(output_path, aggregate.quality_summary())

    def quality(
        self,
        input_file: Path,
        output: Path,
        *,
        root_person: str,
        provider_id: str = "none",
        model: str = "",
        consent: ConsentGrant | None = None,
    ) -> Path:
        """Return the shipped compatibility path for terminal adapters."""

        return self._quality(
            input_file,
            output,
            root_person=root_person,
            provider_id=provider_id,
            model=model,
            consent=consent,
        ).output_path

    def execute_merge(
        self,
        request: GedcomMergeRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> GedcomMergeResult:
        """Execute a merge using only transport-neutral capabilities and DTOs."""

        return _at_contract_boundary(
            lambda: self._execute_merge(request, cancellation=cancellation)
        )

    def _execute_merge(
        self,
        request: GedcomMergeRequest,
        *,
        cancellation: CancellationPort | None,
    ) -> GedcomMergeResult:
        operation = "gedcom.merge"
        if (
            len(request.inputs) < 2
            or request.root_person_ref is None
            or not request.root_person_ref.strip()
            or request.gedcom_version not in SUPPORTED_GEDCOM_VERSIONS
            or not 0 <= request.similarity_threshold <= 100
        ):
            raise DomainFailure(DomainFailureCode.INVALID_REQUEST)
        registry = self._require_artifacts()
        cancellation_port = cancellation or NeverCancelled()
        cancellation_port.check_cancelled()
        inputs = [
            registry.resolve(
                grant,
                operation=operation,
                access=ArtifactAccess.READ,
            )
            for grant in request.inputs
        ]
        output = registry.resolve(
            request.output,
            operation=operation,
            access=ArtifactAccess.WRITE,
        )
        quality_report = registry.resolve(
            request.quality_report,
            operation=operation,
            access=ArtifactAccess.WRITE,
        )
        execution = self._merge(
            inputs,
            output,
            root_person=request.root_person_ref,
            quality_path=quality_report,
            gedcom_version=request.gedcom_version,
            provider_id=request.provider.provider_id,
            model=request.provider.model_id or "",
            consent=self._consent_for(request.provider),
            threshold=request.similarity_threshold,
            cancellation=cancellation_port,
        )
        if execution.root_person_ref is None:
            raise DomainFailure(DomainFailureCode.INTERNAL)
        return GedcomMergeResult(
            gedcom=registry.describe_output(request.output, operation=operation),
            quality_report=registry.describe_output(
                request.quality_report,
                operation=operation,
            ),
            root_person_ref=_opaque_ref("person", execution.root_person_ref),
            changes=execution.changes,
            quality=execution.quality,
            provenance=execution.provenance,
        )

    def execute_subtree(
        self,
        request: GedcomSubtreeRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> GedcomSubtreeResult:
        """Execute a rooted subtree export without exposing host paths."""

        return _at_contract_boundary(
            lambda: self._execute_subtree(request, cancellation=cancellation)
        )

    def _execute_subtree(
        self,
        request: GedcomSubtreeRequest,
        *,
        cancellation: CancellationPort | None,
    ) -> GedcomSubtreeResult:
        operation = "gedcom.subtree"
        if (
            not request.root_person_ref.strip()
            or request.scope not in {"connected", "ancestors", "descendants"}
            or (request.generations is not None and request.generations < 0)
            or request.gedcom_version not in SUPPORTED_GEDCOM_VERSIONS
        ):
            raise DomainFailure(DomainFailureCode.INVALID_REQUEST)
        registry = self._require_artifacts()
        cancellation_port = cancellation or NeverCancelled()
        cancellation_port.check_cancelled()
        source = registry.resolve(
            request.source,
            operation=operation,
            access=ArtifactAccess.READ,
        )
        output = registry.resolve(
            request.output,
            operation=operation,
            access=ArtifactAccess.WRITE,
        )
        execution = self._subtree(
            source,
            output,
            root_person=request.root_person_ref,
            scope=request.scope,
            generations=request.generations,
            gedcom_version=request.gedcom_version,
            cancellation=cancellation_port,
        )
        if execution.root_person_ref is None:
            raise DomainFailure(DomainFailureCode.INTERNAL)
        return GedcomSubtreeResult(
            gedcom=registry.describe_output(request.output, operation=operation),
            root_person_ref=_opaque_ref("person", execution.root_person_ref),
            changes=execution.changes,
            provenance=execution.provenance,
        )

    def execute_quality(
        self,
        request: GedcomQualityRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> GedcomQualityResult:
        """Execute rooted quality analysis without exposing host paths."""

        return _at_contract_boundary(
            lambda: self._execute_quality(request, cancellation=cancellation)
        )

    def _execute_quality(
        self,
        request: GedcomQualityRequest,
        *,
        cancellation: CancellationPort | None,
    ) -> GedcomQualityResult:
        operation = "gedcom.quality"
        if request.root_person_ref is None or not request.root_person_ref.strip():
            raise DomainFailure(DomainFailureCode.INVALID_REQUEST)
        registry = self._require_artifacts()
        cancellation_port = cancellation or NeverCancelled()
        cancellation_port.check_cancelled()
        source = registry.resolve(
            request.source,
            operation=operation,
            access=ArtifactAccess.READ,
        )
        output = registry.resolve(
            request.output,
            operation=operation,
            access=ArtifactAccess.WRITE,
        )
        execution = self._quality(
            source,
            output,
            root_person=request.root_person_ref,
            provider_id=request.provider.provider_id,
            model=request.provider.model_id or "",
            consent=self._consent_for(request.provider),
            cancellation=cancellation_port,
        )
        return GedcomQualityResult(
            report=registry.describe_output(request.output, operation=operation),
            quality=execution.quality,
        )

    def execute_sync(
        self,
        request: GedcomSyncRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> GedcomSyncServiceResult:
        """Execute typed update or rebase sync without exposing host paths."""

        return _at_contract_boundary(lambda: self._execute_sync(request, cancellation=cancellation))

    def _execute_sync(
        self,
        request: GedcomSyncRequest,
        *,
        cancellation: CancellationPort | None,
    ) -> GedcomSyncServiceResult:
        operation = "gedcom.sync"
        command_name = request.sync_command
        if (
            command_name not in {"update", "rebase"}
            or request.gedcom_version not in SUPPORTED_GEDCOM_VERSIONS
        ):
            raise DomainFailure(DomainFailureCode.INVALID_REQUEST)

        source_ids = [snapshot.source_id for snapshot in request.snapshots]
        exported_at_valid = True
        for snapshot in request.snapshots:
            if snapshot.exported_at is None:
                continue
            try:
                dt.datetime.fromisoformat(snapshot.exported_at)
            except ValueError:
                exported_at_valid = False

        if command_name == "update":
            invalid_update = (
                not request.snapshots
                or (request.manifest is None) is not request.initialize_manifest
                or (
                    request.quality_report_enabled
                    and (
                        request.quality_root_person_ref is None
                        or not request.quality_root_person_ref.strip()
                    )
                )
                or request.accept_manual_deletions
                or request.reason is not None
                or len(source_ids) != len(set(source_ids))
                or any(
                    SOURCE_ID_RE.fullmatch(snapshot.source_id) is None
                    or snapshot.vendor not in SUPPORTED_VENDORS
                    for snapshot in request.snapshots
                )
                or not exported_at_valid
            )
            if invalid_update:
                raise DomainFailure(DomainFailureCode.INVALID_REQUEST)
        elif (
            request.manifest is None
            or request.snapshots
            or request.initialize_manifest
            or request.reason is None
            or not request.reason.strip()
            or request.provider.network_allowed
        ):
            raise DomainFailure(DomainFailureCode.INVALID_REQUEST)

        registry = self._require_artifacts()
        cancellation_port = cancellation or NeverCancelled()
        cancellation_port.check_cancelled()
        master = registry.resolve(
            request.master,
            operation=operation,
            access=ArtifactAccess.READ,
        )
        release_root = registry.resolve(
            request.release_root,
            operation=operation,
            access=ArtifactAccess.WRITE,
        )
        manifest = (
            registry.resolve(
                request.manifest,
                operation=operation,
                access=ArtifactAccess.READ,
            )
            if request.manifest is not None
            else None
        )
        snapshot_paths = tuple(
            registry.resolve(
                snapshot.artifact,
                operation=operation,
                access=ArtifactAccess.READ,
            )
            for snapshot in request.snapshots
        )
        input_descriptions = [
            registry.describe_input(request.master, operation=operation),
            *(
                [registry.describe_input(request.manifest, operation=operation)]
                if request.manifest is not None
                else []
            ),
            *(
                registry.describe_input(snapshot.artifact, operation=operation)
                for snapshot in request.snapshots
            ),
        ]
        source_refs = tuple(
            sorted(
                {
                    _opaque_ref(
                        "artifact-source",
                        description.sha256 or description.artifact_id,
                    )
                    for description in input_descriptions
                }
            )
        )

        def cancellation_check() -> None:
            try:
                cancellation_port.check_cancelled()
            except DomainFailure as exc:
                if exc.code is DomainFailureCode.CANCELLED:
                    raise CancellationError("sync cancelled") from exc
                raise

        identity_resolver: IdentityResolver | None = None
        if (
            command_name == "update"
            and request.provider.network_allowed
            and request.automatic_identity_resolution
        ):
            identity_resolver = self._identity_resolver(
                request.provider.provider_id,
                request.provider.model_id or "",
                self._consent_for(request.provider),
            )

        if command_name == "update":
            sync_command: SyncCommand = SyncUpdateCommand(
                master=master,
                release_root=release_root,
                provider=request.provider.provider_id,
                snapshots=tuple(
                    SyncSnapshotInput(
                        snapshot.source_id,
                        snapshot.vendor,
                        snapshot_path,
                        snapshot.exported_at,
                    )
                    for snapshot, snapshot_path in zip(
                        request.snapshots,
                        snapshot_paths,
                        strict=True,
                    )
                ),
                manifest=manifest,
                initialize_manifest=request.initialize_manifest,
                quality_root_person=request.quality_root_person_ref,
                no_quality_report=not request.quality_report_enabled,
                dry_run=request.dry_run,
                gedcom_version=request.gedcom_version,
                auto=request.automatic_identity_resolution,
            )
        else:
            assert manifest is not None
            assert request.reason is not None
            sync_command = SyncRebaseCommand(
                master=master,
                manifest=manifest,
                release_root=release_root,
                reason=request.reason.strip(),
                accept_manual_deletions=request.accept_manual_deletions,
                dry_run=request.dry_run,
            )

        execution = execute_sync_command(
            sync_command,
            self.ingress,
            identity_resolver=identity_resolver,
            cancellation_check=cancellation_check,
            raise_errors=True,
        )
        if execution.exit_code != 0:
            raise DomainFailure(DomainFailureCode.INTERNAL)

        artifact_contracts = {
            "master.ged": ("text/vnd.gedcom", "gedcom"),
            "manifest.json": ("application/json", "sync-manifest"),
            "update.md": ("text/markdown", "update-report"),
            "quality.md": ("text/markdown", "quality-report"),
            "rollback.json": ("application/json", "rollback-manifest"),
        }
        artifacts = tuple(
            registry.describe_generated_output(
                request.release_root,
                artifact,
                operation=operation,
                media_type=artifact_contracts[artifact.name][0],
                artifact_type=artifact_contracts[artifact.name][1],
            )
            for artifact in execution.artifacts
        )
        accounting = execution.accounting
        provenance = tuple(
            ProvenanceRecord(
                result_ref=_opaque_ref(
                    "artifact-result",
                    artifact.sha256 or artifact.artifact_id,
                ),
                source_refs=source_refs,
                rule_code=("sync-published" if command_name == "update" else "rebase-published"),
            )
            for artifact in artifacts
        )
        return GedcomSyncServiceResult(
            committed=execution.committed,
            artifacts=artifacts,
            changes=ChangeSummary(
                created=accounting.created,
                updated=accounting.updated,
                unchanged=accounting.unchanged,
                conflicts=accounting.conflicts,
                warnings=accounting.warnings,
            ),
            quality=QualitySummary(
                information=accounting.information,
                warnings=accounting.quality_warnings,
                errors=accounting.errors,
                resolved=accounting.resolved,
            ),
            provenance=provenance,
        )

    def sync(self, arguments: list[str]) -> GedcomSyncResult:
        """Run incremental sync without capturing or writing terminal streams."""

        result = execute_sync_arguments(
            arguments,
            self.ingress,
            resolver_factory=self._sync_identity_resolver,
            raise_errors=True,
        )
        return GedcomSyncResult(
            exit_code=result.exit_code,
            output=result.output,
            error=result.error,
            committed=result.committed,
        )
