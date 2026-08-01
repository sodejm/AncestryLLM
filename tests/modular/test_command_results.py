from __future__ import annotations

import json
from pathlib import Path

import pytest

from ancestryllm.application.dto import (
    ArtifactRef,
    ArtifactStatus,
    ErrorEnvelope,
)
from ancestryllm.application.events import ProgressEvent
from ancestryllm.application.executor import CommandOutcome
from ancestryllm.application.results import (
    ErrorResult,
    FileArtifactResult,
    MarkdownResult,
    StructuredResult,
    SuccessResult,
    TableResult,
    WarningResult,
)


def _artifact() -> ArtifactRef:
    return ArtifactRef(
        artifact_id=f"art_{'a' * 32}",
        media_type="text/plain",
        artifact_type="report",
        size_bytes=12,
        status=ArtifactStatus.READY,
        sha256="b" * 64,
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    (
        (SuccessResult("Saved."), "Saved."),
        (
            TableResult(
                columns=("name", "count"),
                rows=(("Ada", 2), ("Grace", 1)),
            ),
            [{"count": 2, "name": "Ada"}, {"count": 1, "name": "Grace"}],
        ),
        (MarkdownResult("# Report\n"), "# Report\n"),
        (
            FileArtifactResult(_artifact()),
            {
                "artifact_id": f"art_{'a' * 32}",
                "artifact_type": "report",
                "media_type": "text/plain",
                "sha256": "b" * 64,
                "size_bytes": 12,
                "status": "ready",
            },
        ),
        (
            WarningResult("RESULT_PARTIAL", "Some records were skipped."),
            {
                "code": "RESULT_PARTIAL",
                "message": "Some records were skipped.",
            },
        ),
        (
            ErrorResult(
                ErrorEnvelope(
                    code="RESULT_INVALID",
                    message="The result is invalid.",
                    remediation=None,
                    correlation_ref=None,
                )
            ),
            {
                "code": "RESULT_INVALID",
                "correlation_ref": None,
                "details": [],
                "message": "The result is invalid.",
                "remediation": None,
            },
        ),
        (
            StructuredResult({"count": 2, "names": ["Ada", "Grace"]}),
            {"count": 2, "names": ["Ada", "Grace"]},
        ),
    ),
)
def test_every_declared_command_result_has_a_strict_json_value(
    result: object,
    expected: object,
) -> None:
    serialized = result.to_serializable()  # type: ignore[attr-defined]

    assert serialized == expected
    assert json.loads(json.dumps(serialized, allow_nan=False)) == expected


def test_structured_results_reject_host_objects_at_the_application_boundary() -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        StructuredResult({"path": Path("private.ged")})  # type: ignore[dict-item]


def test_command_outcome_requires_one_declared_result_contract() -> None:
    outcome = CommandOutcome(SuccessResult("Done."), exit_code=0)

    assert isinstance(outcome.result, SuccessResult)
    with pytest.raises(TypeError, match="CommandResult"):
        CommandOutcome("implicit presentation text")  # type: ignore[arg-type]


def test_progress_event_is_transport_neutral_and_json_serializable() -> None:
    event = ProgressEvent(
        operation="gedcom.sync",
        stage="records",
        sequence=3,
        completed=25,
        total=100,
        artifact_id=f"art_{'c' * 32}",
    )

    serialized = event.to_serializable()

    assert serialized == {
        "artifact_id": f"art_{'c' * 32}",
        "completed": 25,
        "operation": "gedcom.sync",
        "sequence": 3,
        "stage": "records",
        "total": 100,
    }
    assert json.loads(json.dumps(serialized, allow_nan=False)) == serialized
