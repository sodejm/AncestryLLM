"""Application service for GEDCOM operations shared by every interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ancestryllm.core.errors import AncestryError
from ancestryllm.gedcom import engine
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
    def __init__(self, llm: LLMService | None = None) -> None:
        self.llm = llm

    @staticmethod
    def _people_and_sources(
        paths: list[Path],
    ) -> tuple[list[Any], list[engine.GedcomRecord], list[engine.IndividualRecord]]:
        sources = engine.load_sources(paths)
        source_records = [record for source in sources for record in source.records]
        people = [
            engine._individual_from_record(record)
            for record in source_records
            if record.tag == "INDI"
        ]
        return sources, source_records, engine.enrich_relationship_context(people, source_records)

    def _resolver(
        self,
        provider_id: str,
        model: str,
        consent: ConsentGrant | None,
    ) -> Callable[[Any, Any], dict[str, object]]:
        if self.llm is None:
            raise AncestryError("LLM_SERVICE_UNAVAILABLE", "No modular LLM service is configured.")
        llm = self.llm

        def resolve(left: Any, right: Any) -> dict[str, object]:
            schema: dict[str, object] = engine._dedup_response_schema()
            data_class = (
                DataClass.DECEASED_PERSON
                if getattr(left, "death_date", "") and getattr(right, "death_date", "")
                else DataClass.POSSIBLY_LIVING_PERSON
            )
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
                data_classes=frozenset({data_class}),
                max_output_tokens=1_000,
            )
            result = llm.generate(request, consent)
            verdict = result.parsed if isinstance(result.parsed, dict) else {}
            verdict["_provider"] = provider_id
            verdict["_model"] = model
            return verdict

        return resolve

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
        resolved_inputs = [path.expanduser().resolve() for path in input_files]
        resolved_output = output.expanduser().resolve()
        if resolved_output in resolved_inputs:
            raise AncestryError(
                "GEDCOM_OVERWRITE_INPUT", "Output must not overwrite an input GEDCOM."
            )
        sources, source_records, people = self._people_and_sources(resolved_inputs)
        pointer_map: dict[str, str] = {}
        decisions: list[engine.MergeDecision] = []
        backend = "none"
        ai_kwargs: dict[str, object] = {}
        if provider_id != "none":
            if not model:
                raise AncestryError(
                    "PROVIDER_MODEL_REQUIRED", "A model is required when AI is enabled."
                )
            backend = "modular"
            ai_kwargs["resolver"] = self._resolver(provider_id, model, consent)
        merged = engine.merge_records(
            people,
            threshold,
            backend,
            True,
            ai_kwargs,
            pointer_map,
            decisions,
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
        source_path = input_file.expanduser().resolve()
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

    def quality(self, input_file: Path, output: Path, *, root_person: str) -> Path:
        sources, source_records, people = self._people_and_sources(
            [input_file.expanduser().resolve()]
        )
        root_pointer = engine.resolve_root_person(root_person, people, [sources[0].pointer_map], {})
        report = engine.analyze_quality(
            people, source_records, sources, root_pointer, output_file=str(input_file)
        )
        output_path = output.expanduser().resolve()
        engine.write_quality_report(report, output_path)
        return output_path

    def sync(self, arguments: list[str]) -> int:
        return run_sync(arguments)
