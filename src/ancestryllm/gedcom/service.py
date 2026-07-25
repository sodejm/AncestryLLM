"""Application service for GEDCOM operations shared by every interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ancestryllm.core.errors import AncestryError, ProviderError
from ancestryllm.core.ingress import FileIngressPolicy
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
    ) -> tuple[list[Any], list[engine.GedcomRecord], list[engine.IndividualRecord]]:
        sources = engine.load_sources(paths, self.ingress)
        source_records = [record for source in sources for record in source.records]
        people = [
            engine._individual_from_record(record)
            for record in source_records
            if record.tag == "INDI"
        ]
        return sources, source_records, engine.enrich_relationship_context(people, source_records)

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
    ) -> IdentityResolver:
        llm = self._require_provider(provider_id)

        def resolve(left: Any, right: Any) -> dict[str, object]:
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
    ) -> QualityResolver:
        llm = self._require_provider(provider_id)

        def resolve(report: engine.QualityReport) -> QualityResolution:
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
        resolved_inputs = [path.expanduser().absolute() for path in input_files]
        resolved_output = output.expanduser().resolve()
        if resolved_output in resolved_inputs:
            raise AncestryError(
                "GEDCOM_OVERWRITE_INPUT", "Output must not overwrite an input GEDCOM."
            )
        sources, source_records, people = self._people_and_sources(resolved_inputs)
        pointer_map: dict[str, str] = {}
        decisions: list[engine.MergeDecision] = []
        resolver: IdentityResolver | None = None
        if provider_id != "none":
            resolver = self._identity_resolver(provider_id, model, consent)
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
            root_pointer = engine.resolve_root_person(
                root_person, merged, [source.pointer_map for source in sources], pointer_map
            )
            include_people, include_families = engine.connected_tree_pointers(
                root_pointer, merged, source_records, pointer_map
            )
        engine.write_gedcom(
            merged,
            resolved_output,
            source_documents=sources,
            pointer_map=pointer_map,
            include_individuals=include_people,
            include_families=include_families,
            gedcom_version=gedcom_version,
        )
        report_path = None
        if quality_path is not None:
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
            report_path = quality_path.expanduser().resolve()
            engine.write_quality_report(report, report_path)
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
        source_path = input_file.expanduser().absolute()
        output_path = output.expanduser().resolve()
        if source_path == output_path:
            raise AncestryError(
                "GEDCOM_OVERWRITE_INPUT", "Output must not overwrite the input GEDCOM."
            )
        sources, source_records, people = self._people_and_sources([source_path])
        root_pointer = engine.resolve_root_person(root_person, people, [sources[0].pointer_map], {})
        keep_people, keep_families = scoped_tree_pointers(
            root_pointer, people, source_records, scope, generations
        )
        engine.write_gedcom(
            people,
            output_path,
            source_documents=sources,
            include_individuals=keep_people,
            include_families=keep_families,
            gedcom_version=gedcom_version,
        )
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
        sources, source_records, people = self._people_and_sources(
            [input_file.expanduser().absolute()]
        )
        root_pointer = engine.resolve_root_person(root_person, people, [sources[0].pointer_map], {})
        report = engine.analyze_quality(
            people, source_records, sources, root_pointer, output_file=str(input_file)
        )
        if provider_id != "none":
            report = engine.refine_quality_report_with_ai(
                report,
                self._quality_resolver(provider_id, model, consent),
            )
        output_path = output.expanduser().resolve()
        engine.write_quality_report(report, output_path)
        return output_path

    def sync(self, arguments: list[str]) -> int:
        return run_sync(
            arguments,
            self.ingress,
            resolver_factory=self._sync_identity_resolver,
        )
