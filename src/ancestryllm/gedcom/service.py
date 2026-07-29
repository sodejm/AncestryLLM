"""Application service for GEDCOM operations shared by every interface."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from ancestryllm.core.errors import AncestryError, ProviderError
from ancestryllm.core.ingress import FileFingerprint, FileIngressPolicy, FileKind
from ancestryllm.core.publication import (
    claim_staged_path,
    cleanup_staged_path,
    paths_alias,
    publish_staged_bundle,
    staging_path,
)
from ancestryllm.gedcom import engine
from ancestryllm.gedcom.contracts import IdentityResolver, QualityResolution, QualityResolver
from ancestryllm.gedcom.graph import scoped_tree_pointers
from ancestryllm.gedcom.sync import run_sync
from ancestryllm.llm.contracts import DataClass, GenerationRequest, Message
from ancestryllm.llm.policy import ConsentGrant
from ancestryllm.llm.service import LLMService


@dataclass(frozen=True, slots=True)
class GedcomOperationResult:
    output_path: Path
    people_read: int
    people_written: int
    quality_path: Path | None = None


@dataclass(frozen=True, slots=True)
class GedcomSyncResult:
    """Serializable result for an incremental GEDCOM synchronization."""

    exit_code: int
    output: str


class GedcomService:
    def __init__(
        self,
        llm: LLMService | None = None,
        ingress: FileIngressPolicy | None = None,
        *,
        consent_lookup: Callable[[str], ConsentGrant] | None = None,
        provider_timeout_seconds: float = 60.0,
    ) -> None:
        self.llm = llm
        self.ingress = ingress or FileIngressPolicy()
        self.consent_lookup = consent_lookup
        self.provider_timeout_seconds = provider_timeout_seconds

    def _people_and_sources(
        self,
        paths: list[Path],
    ) -> tuple[
        list[Any],
        list[engine.GedcomRecord],
        list[engine.IndividualRecord],
        dict[Path, FileFingerprint],
    ]:
        fingerprints = {path: self.ingress.fingerprint(path, FileKind.GEDCOM) for path in paths}
        try:
            sources = engine.load_sources(
                paths,
                self.ingress,
                {path: fingerprint.snapshot for path, fingerprint in fingerprints.items()},
                validate_structure=True,
            )
        except engine.GedcomParseError as exc:
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
            engine._individual_from_record(record)
            for record in source_records
            if record.tag == "INDI"
        ]
        return (
            sources,
            source_records,
            engine.enrich_relationship_context(people, source_records),
            fingerprints,
        )

    def _verify_sources(self, fingerprints: dict[Path, FileFingerprint]) -> None:
        for path, fingerprint in fingerprints.items():
            self.ingress.verify(path, FileKind.GEDCOM, fingerprint)

    @staticmethod
    def _resolve_root_person(
        requested: str,
        records: list[engine.IndividualRecord],
        source_pointer_maps: list[dict[str, str]],
        merged_pointer_map: dict[str, str],
    ) -> str:
        """Resolve a root without exposing the requested genealogy value."""

        try:
            resolved = engine.resolve_root_person(
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
            schema: dict[str, object] = engine._dedup_response_schema()
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
                            + engine._build_dedup_prompt(left, right)
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

        def resolve(report: engine.QualityReport) -> QualityResolution:
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
                            + engine._build_quality_prompt(report)
                            + "\n</untrusted_genealogy_findings>"
                        ),
                    ),
                ),
                response_schema=engine._quality_response_schema(),
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
            allowed = {finding.finding_id for finding in report.findings[: engine.QUALITY_AI_LIMIT]}
            try:
                annotations = engine._quality_annotations_from_payload(result.parsed, allowed)
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
        threshold: int = engine.DEFAULT_SIMILARITY_THRESHOLD,
    ) -> GedcomOperationResult:
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
        decisions: list[engine.MergeDecision] = []
        resolver: IdentityResolver | None = None
        if provider_id != "none":
            resolver = self._identity_resolver(
                provider_id,
                model,
                consent,
                verify_inputs,
            )
        merged = engine.merge_records(
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
            include_people, include_families = engine.connected_tree_pointers(
                root_pointer, merged, source_records, pointer_map
            )
        staged_output: Path | None = None
        staged_report: Path | None = None
        try:
            staged_output = staging_path(resolved_output)
            output_token = engine.write_gedcom(
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
                report = engine.analyze_quality(
                    merged,
                    source_records,
                    sources,
                    root_pointer,
                    pointer_map=pointer_map,
                    merge_decisions=decisions,
                    output_file=str(resolved_output),
                )
                staged_report = staging_path(report_path)
                report_token = engine.write_quality_report(report, staged_report)
                claim_staged_path(staged_report, report_token)
                artifacts.append((staged_report, report_path))
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
        return GedcomOperationResult(
            resolved_output,
            len(people),
            len(include_people) if include_people is not None else len(merged),
            report_path,
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
            output_token = engine.write_gedcom(
                people,
                staged_output,
                source_documents=sources,
                include_individuals=keep_people,
                include_families=keep_families,
                gedcom_version=gedcom_version,
            )
            claim_staged_path(staged_output, output_token)
            publish_staged_bundle(
                ((staged_output, output_path),),
                replace=os.replace,
                validate_after=lambda: self._verify_sources(fingerprints),
            )
        except BaseException:
            cleanup_staged_path(staged_output)
            raise
        return GedcomOperationResult(output_path, len(people), len(keep_people))

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
        report = engine.analyze_quality(
            people, source_records, sources, root_pointer, output_file=str(source_path)
        )
        if provider_id != "none":
            report = engine.refine_quality_report_with_ai(
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
            output_token = engine.write_quality_report(report, staged_output)
            claim_staged_path(staged_output, output_token)
            publish_staged_bundle(
                ((staged_output, output_path),),
                replace=os.replace,
                validate_after=verify_inputs,
            )
        except BaseException:
            cleanup_staged_path(staged_output)
            raise
        return output_path

    def sync(self, arguments: list[str]) -> GedcomSyncResult:
        """Run incremental sync and retain its transcript for each presentation surface."""

        output = StringIO()
        with redirect_stdout(output):
            exit_code = run_sync(
                arguments,
                self.ingress,
                resolver_factory=self._sync_identity_resolver,
                raise_errors=True,
            )
        return GedcomSyncResult(exit_code=exit_code, output=output.getvalue())
