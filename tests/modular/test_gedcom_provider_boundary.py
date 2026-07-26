from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import pytest

from ancestryllm.core.errors import ProviderError
from ancestryllm.gedcom import engine
from ancestryllm.gedcom.service import GedcomService
from ancestryllm.llm.contracts import DataClass, GenerationRequest, GenerationResult
from ancestryllm.llm.validation import validate_structured_output


class RecordingLLM:
    def __init__(self, parsed: dict[str, object] | None, *, remote: bool = False) -> None:
        self.parsed = parsed
        self.remote = remote
        self.requests: list[GenerationRequest] = []

    def generate(
        self, request: GenerationRequest, _consent: object | None = None
    ) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(
            provider_id=request.provider_id,
            model=f"resolved-{request.model}",
            text="provider payload must remain private",
            parsed=self.parsed,
            remote=self.remote,
        )


def _person(pointer: str, *, surname: str, birth: str, source: str) -> engine.IndividualRecord:
    return engine.IndividualRecord(
        pointer,
        given_name="John",
        surname=surname,
        birth_date=birth,
        birth_place="Boston, Massachusetts, USA",
        source_file=source,
    )


def _quality_report() -> engine.QualityReport:
    finding = engine.QualityFinding(
        finding_id="quality-1",
        code="DATE_GAP",
        severity="medium",
        category="general",
        title="Fictional date gap",
        description="A fictional event needs another source.",
        recommendation="Compare fictional records.",
        evidence=("Fictional evidence",),
    )
    return engine.QualityReport(
        root_pointer="@I1@",
        root_name="Fictional Person",
        input_files=("fictional.ged",),
        output_file="quality.md",
        findings=(finding,),
    )


def test_only_provider_adapters_import_network_clients() -> None:
    package = Path(__file__).resolve().parents[2] / "src" / "ancestryllm"
    forbidden = {
        "anthropic",
        "google.genai",
        "httpx",
        "ollama",
        "openai",
        "openrouter",
        "requests",
        "urllib.error",
        "urllib.request",
    }
    violations: list[str] = []
    for path in package.rglob("*.py"):
        relative = path.relative_to(package)
        if relative.parts[:2] == ("llm", "providers"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
                if node.module == "google":
                    modules += tuple(f"google.{alias.name}" for alias in node.names)
            for module in modules:
                if module in forbidden or any(module.startswith(f"{item}.") for item in forbidden):
                    violations.append(f"{relative}:{node.lineno}:{module}")
    assert violations == []


def test_identity_adjudication_uses_shared_generation_contract() -> None:
    llm = RecordingLLM(
        {
            "is_duplicate": True,
            "confidence": 0.91,
            "reasoning": "Fictional evidence agrees.",
            "preferred_values": {
                "given_name": "",
                "surname": "",
                "birth_date": "",
                "birth_place": "",
                "death_date": "",
                "death_place": "",
                "gender": "",
            },
        }
    )
    service = GedcomService(llm, provider_timeout_seconds=17.0)  # type: ignore[arg-type]
    resolver = service._identity_resolver("ollama", "fixture-model", None)

    verdict = resolver(
        _person("@I1@", surname="Smith", birth="1850", source="/a.ged"),
        _person("@I2@", surname="Smyth", birth="1851", source="/b.ged"),
    )

    request = llm.requests[0]
    assert request.module_id == "gedcom"
    assert request.purpose == "identity_adjudication"
    assert request.timeout_seconds == 17.0
    assert request.response_schema == engine._dedup_response_schema()
    assert verdict["_provider"] == "ollama"
    assert verdict["_model"] == "resolved-fixture-model"


def test_identity_adjudication_labels_relative_context_as_possibly_living() -> None:
    llm = RecordingLLM(
        {
            "is_duplicate": False,
            "confidence": 0.1,
            "reasoning": "Fictional evidence differs.",
            "preferred_values": {
                "given_name": "",
                "surname": "",
                "birth_date": "",
                "birth_place": "",
                "death_date": "",
                "death_place": "",
                "gender": "",
            },
        }
    )
    service = GedcomService(llm)  # type: ignore[arg-type]
    left = dataclasses.replace(
        _person("@I1@", surname="Smith", birth="1850", source="/a.ged"),
        death_date="1920",
        children=(engine.RelativeIdentity("@C1@", "Fictional Relative", "2000"),),
    )
    right = dataclasses.replace(
        _person("@I2@", surname="Smyth", birth="1851", source="/b.ged"),
        death_date="1921",
    )

    service._identity_resolver("openai", "fixture-model", None)(left, right)

    request = llm.requests[0]
    assert request.data_classes == frozenset({DataClass.POSSIBLY_LIVING_PERSON})
    assert "Fictional Relative" in request.messages[1].content


def test_quality_refinement_uses_shared_generation_contract() -> None:
    llm = RecordingLLM(
        {
            "annotations": [
                {
                    "finding_id": "quality-1",
                    "why_this_matters": "It may distinguish two fictional events.",
                    "research_suggestions": ["Compare the fictional register."],
                }
            ]
        }
    )
    service = GedcomService(llm, provider_timeout_seconds=19.0)  # type: ignore[arg-type]

    refined = engine.refine_quality_report_with_ai(
        _quality_report(),
        service._quality_resolver("ollama", "fixture-model", None),
    )

    request = llm.requests[0]
    assert request.module_id == "gedcom"
    assert request.purpose == "quality_refinement"
    assert request.timeout_seconds == 19.0
    assert request.response_schema == engine._quality_response_schema()
    assert refined.findings[0].ai_why.startswith("It may distinguish")
    assert refined.ai_backend == "ollama/resolved-fixture-model"
    assert refined.privacy_status.startswith("Local provider refinement")


def test_quality_refinement_uses_resolved_provider_locality() -> None:
    llm = RecordingLLM(
        {
            "annotations": [
                {
                    "finding_id": "quality-1",
                    "why_this_matters": "It may distinguish two fictional events.",
                    "research_suggestions": ["Compare the fictional register."],
                }
            ]
        },
        remote=True,
    )
    service = GedcomService(llm)  # type: ignore[arg-type]

    refined = engine.refine_quality_report_with_ai(
        _quality_report(),
        service._quality_resolver("ollama", "fixture-model", None),
    )

    assert refined.privacy_status.startswith("Bounded top-25 finding summaries sent")


@pytest.mark.parametrize(
    "payload",
    [
        {"annotations": []},
        {
            "annotations": [
                {
                    "finding_id": "unknown-finding",
                    "why_this_matters": "Must be ignored.",
                    "research_suggestions": ["Must be ignored."],
                }
            ]
        },
    ],
)
def test_remote_quality_refinement_discloses_provider_when_no_annotations_apply(
    payload: dict[str, object],
) -> None:
    llm = RecordingLLM(payload, remote=True)
    service = GedcomService(llm)  # type: ignore[arg-type]

    refined = engine.refine_quality_report_with_ai(
        _quality_report(),
        service._quality_resolver("openrouter", "fixture-model", None),
    )

    assert len(llm.requests) == 1
    assert refined.ai_refined is True
    assert refined.ai_backend == "openrouter/resolved-fixture-model"
    assert refined.privacy_status.startswith("Bounded top-25 finding summaries sent")
    assert refined.findings[0].ai_why == ""
    assert refined.findings[0].ai_research == ()


def test_provider_error_is_not_swallowed_or_copied_into_merge_output() -> None:
    sensitive_detail = "private upstream payload"

    def fail(_left: object, _right: object) -> dict[str, object]:
        raise ProviderError(
            "PROVIDER_TIMEOUT",
            "The ollama request timed out before output began.",
            details={"error_type": "TimeoutError"},
        )

    left = _person("@I1@", surname="Smith", birth="1850", source="/a.ged")
    right = _person("@I2@", surname="Smyth", birth="1851", source="/b.ged")
    with pytest.raises(ProviderError) as raised:
        engine.merge_records(
            [left, right],
            threshold=70,
            auto=True,
            identity_resolver=fail,
        )

    assert raised.value.code == "PROVIDER_TIMEOUT"
    assert sensitive_detail not in raised.value.render()


def test_malformed_modular_result_has_stable_sanitized_error() -> None:
    llm = RecordingLLM(None)
    service = GedcomService(llm)  # type: ignore[arg-type]
    resolver = service._identity_resolver("ollama", "fixture-model", None)

    with pytest.raises(ProviderError) as raised:
        resolver(
            _person("@I1@", surname="Smith", birth="1850", source="/a.ged"),
            _person("@I2@", surname="Smyth", birth="1851", source="/b.ged"),
        )

    assert raised.value.code == "PROVIDER_OUTPUT_INVALID"
    assert "provider payload must remain private" not in raised.value.render()
    assert "provider payload must remain private" not in repr(raised.value.details)


def test_non_standard_non_finite_json_confidence_is_rejected() -> None:
    payload = (
        '{"is_duplicate":true,"confidence":NaN,"reasoning":"invalid",'
        '"preferred_values":{"given_name":"","surname":"","birth_date":"",'
        '"birth_place":"","death_date":"","death_place":"","gender":""}}'
    )

    with pytest.raises(ProviderError) as raised:
        validate_structured_output(payload, engine._dedup_response_schema())

    assert raised.value.code == "PROVIDER_OUTPUT_INVALID"


def test_low_confidence_provider_identity_is_retained_when_auto() -> None:
    left = _person("@I1@", surname="Smith", birth="1850", source="/a.ged")
    right = _person("@I2@", surname="Smyth", birth="1851", source="/b.ged")

    merged = engine.merge_records(
        [left, right],
        threshold=70,
        auto=True,
        identity_resolver=lambda _left, _right: {
            "is_duplicate": True,
            "confidence": 0.1,
            "reasoning": "Insufficient fictional evidence.",
            "preferred_values": {},
        },
    )

    assert [person.pointer for person in merged] == ["@I1@", "@I2@"]


def test_gedcom_internals_do_not_discover_provider_selection_from_environment() -> None:
    package = Path(__file__).resolve().parents[2] / "src" / "ancestryllm" / "gedcom"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "GEDCOM_AI_BACKEND",
    ):
        assert name not in source


def test_provider_none_never_constructs_a_gedcom_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = GedcomService(RecordingLLM(None))  # type: ignore[arg-type]

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("provider=none constructed a resolver")

    monkeypatch.setattr(service, "_identity_resolver", forbidden)
    monkeypatch.setattr(service, "_quality_resolver", forbidden)
    input_path = tmp_path / "tree.ged"
    input_path.write_text(
        "0 HEAD\n"
        "1 GEDC\n"
        "2 VERS 5.5.5\n"
        "1 CHAR UTF-8\n"
        "0 @I1@ INDI\n"
        "1 NAME Fictional /Person/\n"
        "1 DEAT\n"
        "2 DATE 1900\n"
        "0 TRLR\n",
        encoding="utf-8",
    )

    output = tmp_path / "quality.md"
    service.quality(
        input_path,
        output,
        root_person="@I1@",
        provider_id="none",
        model="ignored-even-when-present",
    )

    assert output.is_file()
