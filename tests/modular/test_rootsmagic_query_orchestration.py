"""Tests for RootsMagic query orchestration and provider-independent failures."""

from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from ancestryllm.application.dto import ProgressUpdate, ProviderSelection
from ancestryllm.application.operations import (
    QueryExecutionRecord,
    QueryRow,
    RootsMagicQueryRequest,
    RootsMagicQueryResult,
)
from ancestryllm.core.cancellation import CancellationToken, bind_cancellation_token
from ancestryllm.core.config import AppConfig
from ancestryllm.core.errors import AncestryError, ProviderError
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode
from ancestryllm.llm.contracts import DataClass, GenerationRequest, GenerationResult
from ancestryllm.llm.policy import ConsentGrant
from ancestryllm.rootsmagic.core import RootsMagicReader
from ancestryllm.rootsmagic.query import RootsMagicQueryService


def _create_tree(path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE Evidence(Label TEXT, Payload BLOB);
        INSERT INTO Evidence VALUES ('first', X'00FF');
        INSERT INTO Evidence VALUES ('second', NULL);
        INSERT INTO Evidence VALUES ('third', NULL);
        """
    )
    connection.commit()
    connection.close()
    return path


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        config_path=tmp_path / "config.toml",
        data_dir=tmp_path / "data",
        family_tree_dirs=[tmp_path],
        max_query_rows=2,
        query_timeout_seconds=0.1,
        provider_timeout_seconds=3.0,
    )


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class _Progress:
    def __init__(self) -> None:
        self.updates: list[ProgressUpdate] = []

    def emit(self, update: ProgressUpdate) -> None:
        self.updates.append(update)


class _ExplodingLlm:
    def generate(
        self, request: GenerationRequest, consent: object | None = None
    ) -> GenerationResult:
        del request, consent
        raise AssertionError("deterministic SQL must not invoke a provider")


class _CapturingLlm:
    def __init__(self, parsed: object) -> None:
        self.parsed = parsed
        self.requests: list[GenerationRequest] = []
        self.consents: list[object | None] = []

    def generate(
        self, request: GenerationRequest, consent: object | None = None
    ) -> GenerationResult:
        self.requests.append(request)
        self.consents.append(consent)
        return GenerationResult(
            provider_id=request.provider_id,
            model=request.model,
            text="fixture response that must not enter progress or errors",
            parsed=self.parsed,
        )


class _FailingLlm:
    def __init__(self, failure: AncestryError) -> None:
        self.failure = failure

    def generate(
        self, request: GenerationRequest, consent: object | None = None
    ) -> GenerationResult:
        del request, consent
        raise self.failure


class _Cancelled:
    def check_cancelled(self) -> None:
        raise DomainFailure(DomainFailureCode.CANCELLED)


def test_direct_sql_is_provider_independent_and_returns_a_canonical_boundary_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _create_tree(tmp_path / "fictional.rmtree")
    before = _digest(tree)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-enable-network")

    def forbid_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("provider-none direct SQL must remain network-free")

    monkeypatch.setattr("socket.create_connection", forbid_network)
    progress = _Progress()
    service = RootsMagicQueryService(
        _config(tmp_path),
        RootsMagicReader([tmp_path], max_rows=2, timeout_seconds=0.1),
        _ExplodingLlm(),  # type: ignore[arg-type]
        progress=progress,
    )

    result = service.execute(
        RootsMagicQueryRequest(
            tree_ref=tree.name,
            sql="SELECT Label, Payload FROM Evidence ORDER BY Label",
            question=None,
            provider=ProviderSelection(provider_id="openai", model_id="ignored-model"),
        )
    )

    assert result == RootsMagicQueryResult(
        columns=("Label", "Payload"),
        rows=(
            QueryRow(("first", '{"data":"AP8=","encoding":"base64"}')),
            QueryRow(("second", None)),
        ),
        truncated=True,
        execution=QueryExecutionRecord(
            mode_code="direct_sql",
            provider_id="none",
            row_limit=2,
            returned_rows=2,
        ),
    )
    assert RootsMagicQueryResult.from_json(result.to_json()) == result
    assert "SELECT" not in result.to_json()
    assert str(tree) not in result.to_json()
    assert _digest(tree) == before
    assert [(event.stage, event.sequence) for event in progress.updates] == [
        ("start", 0),
        ("complete", 1),
    ]
    serialized_events = "".join(event.to_json() for event in progress.updates)
    assert str(tree) not in serialized_events
    assert "SELECT" not in serialized_events
    assert "first" not in serialized_events


def test_provider_sql_uses_the_same_reader_hardening_and_safe_result_conversion(
    tmp_path: Path,
) -> None:
    tree = _create_tree(tmp_path / "fictional.rmtree")
    before = _digest(tree)
    llm = _CapturingLlm({"sql": "SELECT Label FROM Evidence ORDER BY Label"})
    progress = _Progress()
    service = RootsMagicQueryService(
        _config(tmp_path),
        RootsMagicReader([tmp_path], max_rows=2, timeout_seconds=0.1),
        llm,  # type: ignore[arg-type]
        progress=progress,
    )

    result = service.execute(
        RootsMagicQueryRequest(
            tree_ref=tree.name,
            sql=None,
            question="Which fictional labels exist?",
            provider=ProviderSelection(provider_id="fixture", model_id="fixture-model"),
        )
    )

    assert result.rows == (QueryRow(("first",)), QueryRow(("second",)))
    assert result.execution.mode_code == "provider_sql"
    assert result.execution.provider_id == "fixture"
    assert result.execution.row_limit == 2
    assert result.execution.returned_rows == 2
    assert result.truncated is True
    assert _digest(tree) == before
    assert len(llm.requests) == 1
    assert llm.requests[0].timeout_seconds == 3.0
    assert [(event.stage, event.sequence) for event in progress.updates] == [
        ("start", 0),
        ("provider_complete", 1),
        ("complete", 2),
    ]
    serialized_events = "".join(event.to_json() for event in progress.updates)
    assert "Which fictional" not in serialized_events
    assert "fixture response" not in serialized_events
    assert str(tree) not in serialized_events


@pytest.mark.parametrize("provider_assisted", [False, True], ids=["direct", "provider"])
@pytest.mark.parametrize(
    ("sql", "expected_code"),
    [
        ("DELETE FROM Evidence", "SQL_REJECTED"),
        ("SELECT * FROM MissingTable", "SQL_TABLE_DENIED"),
        ("SELECT load_extension('fictional') FROM Evidence", "SQL_OPERATION_DENIED"),
        ("SELECT no_such_function() FROM Evidence", "ROOTSMAGIC_QUERY_FAILED"),
    ],
)
def test_direct_and_generated_sql_share_validation_authorization_and_execution_errors(
    tmp_path: Path,
    provider_assisted: bool,
    sql: str,
    expected_code: str,
) -> None:
    tree = _create_tree(tmp_path / "fictional.rmtree")
    before = _digest(tree)
    progress = _Progress()
    llm = _CapturingLlm({"sql": sql})
    service = RootsMagicQueryService(
        _config(tmp_path),
        RootsMagicReader([tmp_path], max_rows=2, timeout_seconds=0.1),
        llm,  # type: ignore[arg-type]
        progress=progress,
    )
    request = RootsMagicQueryRequest(
        tree_ref=tree.name,
        sql=None if provider_assisted else sql,
        question="Return fictional evidence." if provider_assisted else None,
        provider=(
            ProviderSelection(provider_id="fixture", model_id="fixture-model")
            if provider_assisted
            else ProviderSelection()
        ),
    )

    with pytest.raises(AncestryError) as raised:
        service.execute(request)

    assert raised.value.code == expected_code
    assert _digest(tree) == before
    serialized_events = "".join(event.to_json() for event in progress.updates)
    assert sql not in serialized_events
    assert str(tree) not in serialized_events
    assert "Return fictional evidence" not in serialized_events
    assert len(llm.requests) == int(provider_assisted)


@pytest.mark.parametrize("provider_assisted", [False, True], ids=["direct", "provider"])
def test_direct_and_generated_sql_share_timeout_and_source_immutability(
    tmp_path: Path,
    provider_assisted: bool,
) -> None:
    tree = _create_tree(tmp_path / "fictional.rmtree")
    before = _digest(tree)
    sql = (
        "SELECT COUNT(*) FROM Evidence a, Evidence b, Evidence c, Evidence d, "
        "Evidence e, Evidence f, Evidence g, Evidence h"
    )
    llm = _CapturingLlm({"sql": sql})
    service = RootsMagicQueryService(
        _config(tmp_path),
        RootsMagicReader([tmp_path], max_rows=2, timeout_seconds=-1.0),
        llm,  # type: ignore[arg-type]
    )

    with pytest.raises(AncestryError) as raised:
        service.execute(
            RootsMagicQueryRequest(
                tree_ref=tree.name,
                sql=None if provider_assisted else sql,
                question="Count fictional rows." if provider_assisted else None,
                provider=(
                    ProviderSelection(provider_id="fixture", model_id="fixture-model")
                    if provider_assisted
                    else ProviderSelection()
                ),
            )
        )

    assert raised.value.code == "ROOTSMAGIC_QUERY_TIMEOUT"
    assert _digest(tree) == before


@pytest.mark.parametrize("provider_assisted", [False, True], ids=["direct", "provider"])
def test_ambient_cancellation_maps_to_stable_boundary_failure_and_preserves_source(
    tmp_path: Path,
    provider_assisted: bool,
) -> None:
    tree = _create_tree(tmp_path / "fictional.rmtree")
    before = _digest(tree)
    sql = "SELECT Label FROM Evidence"
    llm = _CapturingLlm({"sql": sql})
    service = RootsMagicQueryService(
        _config(tmp_path),
        RootsMagicReader([tmp_path], max_rows=2, timeout_seconds=0.1),
        llm,  # type: ignore[arg-type]
    )
    token = CancellationToken()
    token.request()

    with bind_cancellation_token(token), pytest.raises(DomainFailure) as raised:
        service.execute(
            RootsMagicQueryRequest(
                tree_ref=tree.name,
                sql=None if provider_assisted else sql,
                question="List fictional evidence." if provider_assisted else None,
                provider=(
                    ProviderSelection(provider_id="fixture", model_id="fixture-model")
                    if provider_assisted
                    else ProviderSelection()
                ),
            )
        )

    assert raised.value.code is DomainFailureCode.CANCELLED
    assert _digest(tree) == before


def test_provider_failure_and_consent_selection_have_stable_sanitized_codes(
    tmp_path: Path,
) -> None:
    tree = _create_tree(tmp_path / "fictional.rmtree")
    before = _digest(tree)
    request = RootsMagicQueryRequest(
        tree.name,
        None,
        "List fictional evidence.",
        ProviderSelection(
            provider_id="fixture",
            model_id="fixture-model",
            consent_id="fixture-consent",
        ),
    )
    service = RootsMagicQueryService(
        _config(tmp_path),
        RootsMagicReader([tmp_path], max_rows=2, timeout_seconds=0.1),
        _CapturingLlm({"sql": "SELECT Label FROM Evidence"}),  # type: ignore[arg-type]
    )

    with pytest.raises(AncestryError) as mismatch:
        service.execute(request)
    assert mismatch.value.code == "CONSENT_SELECTION_MISMATCH"
    assert str(tree) not in mismatch.value.render()

    consent = ConsentGrant(
        consent_id="fixture-consent",
        provider_id="fixture",
        allowed_modules=frozenset({"rootsmagic"}),
        allowed_purposes=frozenset({"sql_generation"}),
        allowed_data_classes=frozenset({DataClass.POSSIBLY_LIVING_PERSON}),
        model_allowlist=("fixture-model",),
    )
    failure = ProviderError("PROVIDER_TIMEOUT", "The selected provider request timed out.")
    service = RootsMagicQueryService(
        _config(tmp_path),
        RootsMagicReader([tmp_path], max_rows=2, timeout_seconds=0.1),
        _FailingLlm(failure),  # type: ignore[arg-type]
    )
    with pytest.raises(ProviderError) as provider_failure:
        service.execute(request, consent=consent)
    assert provider_failure.value.code == "PROVIDER_TIMEOUT"
    assert str(tree) not in provider_failure.value.render()
    assert "List fictional evidence" not in provider_failure.value.render()
    assert _digest(tree) == before


def test_request_selection_parsing_and_cancellation_have_stable_failures(
    tmp_path: Path,
) -> None:
    tree = _create_tree(tmp_path / "fictional.rmtree")
    before = _digest(tree)
    reader = RootsMagicReader([tmp_path], max_rows=2, timeout_seconds=0.1)

    service = RootsMagicQueryService(_config(tmp_path), reader)
    with pytest.raises(AncestryError) as missing_query:
        service.execute(RootsMagicQueryRequest(tree.name, None, None, ProviderSelection()))
    assert missing_query.value.code == "ARGUMENT_INVALID"

    with pytest.raises(AncestryError) as offline_question:
        service.execute(
            RootsMagicQueryRequest(
                tree.name,
                None,
                "Who is present?",
                ProviderSelection(),
            )
        )
    assert offline_question.value.code == "PROVIDER_REQUIRED"

    invalid_llm = _CapturingLlm({"not_sql": "SELECT 1"})
    service = RootsMagicQueryService(
        _config(tmp_path),
        reader,
        invalid_llm,  # type: ignore[arg-type]
    )
    with pytest.raises(AncestryError) as invalid_response:
        service.execute(
            RootsMagicQueryRequest(
                tree.name,
                None,
                "Who is present?",
                ProviderSelection(provider_id="fixture", model_id="fixture-model"),
            )
        )
    assert invalid_response.value.code == "SQL_GENERATION_INVALID"

    cancelled_llm = _CapturingLlm({"sql": "SELECT Label FROM Evidence"})
    service = RootsMagicQueryService(
        _config(tmp_path),
        reader,
        cancelled_llm,  # type: ignore[arg-type]
        cancellation=_Cancelled(),
    )
    with pytest.raises(DomainFailure) as cancelled:
        service.execute(
            RootsMagicQueryRequest(
                tree.name,
                None,
                "Who is present?",
                ProviderSelection(provider_id="fixture", model_id="fixture-model"),
            )
        )
    assert cancelled.value.code is DomainFailureCode.CANCELLED
    assert cancelled_llm.requests == []
    assert _digest(tree) == before


def test_query_compatibility_facade_does_not_own_runtime_or_provider_policy() -> None:
    source = (Path(__file__).parents[2] / "src/ancestryllm/rootsmagic/query.py").read_text(
        encoding="utf-8"
    )

    assert "class RootsMagicQueryService" not in source
    assert "ancestryllm.application._rootsmagic" in source
    assert "ancestryllm.llm" not in source
    assert "ancestryllm.core.config" not in source
    assert (
        json.loads(
            RootsMagicQueryResult(
                ("column",),
                (QueryRow(("value",)),),
                False,
                QueryExecutionRecord("direct_sql", "none", 1, 1),
            ).to_json()
        )["type"]
        == "RootsMagicQueryResult"
    )
