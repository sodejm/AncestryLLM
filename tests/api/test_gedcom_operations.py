"""Contracts for path-free GEDCOM operation submission and result retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ancestryllm.api import API_NAMESPACE
from ancestryllm.api.contracts import GedcomResultResponse
from ancestryllm.application.dto import ArtifactRef, ArtifactStatus
from ancestryllm.application.operations import (
    GedcomInspectRequest,
    GedcomInspectResult,
    GedcomSourceSummary,
    MergeRequest,
    QualityRequest,
    RootCandidate,
    RootCandidatePage,
    SubtreeRequest,
    SyncRequest,
)
from ancestryllm.core.errors import AncestryError

if TYPE_CHECKING:
    from unittest.mock import Mock

    from fastapi.testclient import TestClient


def _grant(operation: str, access: str, *, marker: str = "a") -> dict[str, str]:
    return {
        "grant_id": "grt_" + (marker * 64),
        "operation": operation,
        "access": access,
    }


def test_inspect_submits_only_an_operation_scoped_read_grant(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
) -> None:
    response = api_client.post(
        f"{API_NAMESPACE}/gedcom/inspect",
        headers=api_headers,
        json={"schema_version": 1, "source": _grant("gedcom.inspect", "read")},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "gedcom.inspect"
    assert "/" not in response.text
    request = cast("GedcomInspectRequest", gedcom_job_facade.submit_inspect.call_args.args[0])
    assert request.source.grant_id == "grt_" + ("a" * 64)
    assert request.source.operation == "gedcom.inspect"
    assert request.source.access.value == "read"


def test_inspect_rejects_raw_paths_and_mismatched_grants(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
) -> None:
    raw_path = api_client.post(
        f"{API_NAMESPACE}/gedcom/inspect",
        headers=api_headers,
        json={
            "schema_version": 1,
            "source": {
                **_grant("gedcom.inspect", "read"),
                "path": "/private/fictional.ged",
            },
        },
    )
    mismatched = api_client.post(
        f"{API_NAMESPACE}/gedcom/inspect",
        headers=api_headers,
        json={"schema_version": 1, "source": _grant("gedcom.merge", "read")},
    )

    assert raw_path.status_code == 400
    assert mismatched.status_code == 400
    gedcom_job_facade.submit_inspect.assert_not_called()


def test_merge_submits_distinct_read_and_write_grants(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
) -> None:
    response = api_client.post(
        f"{API_NAMESPACE}/gedcom/merge",
        headers=api_headers,
        json={
            "schema_version": 1,
            "inputs": [
                _grant("gedcom.merge", "read", marker="a"),
                _grant("gedcom.merge", "read", marker="b"),
            ],
            "output": _grant("gedcom.merge", "write", marker="c"),
            "quality_report": _grant("gedcom.merge", "write", marker="d"),
            "root_person_ref": "person:fictional-root",
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "gedcom.merge"
    request = cast("MergeRequest", gedcom_job_facade.submit_merge.call_args.args[0])
    assert request.gedcom_version == "5.5.5"
    assert request.provider.provider_id == "none"
    assert tuple(grant.access.value for grant in request.inputs) == ("read", "read")
    assert request.output.access.value == "write"
    assert request.quality_report.access.value == "write"


def test_subtree_and_quality_map_path_free_contracts(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
) -> None:
    subtree = api_client.post(
        f"{API_NAMESPACE}/gedcom/subtree",
        headers=api_headers,
        json={
            "schema_version": 1,
            "source": _grant("gedcom.subtree", "read", marker="a"),
            "output": _grant("gedcom.subtree", "write", marker="b"),
            "root_person_ref": "person:fictional-root",
            "scope": "ancestors",
            "generations": 4,
            "gedcom_version": "5.5.1",
        },
    )
    quality = api_client.post(
        f"{API_NAMESPACE}/gedcom/quality",
        headers=api_headers,
        json={
            "schema_version": 1,
            "source": _grant("gedcom.quality", "read", marker="c"),
            "output": _grant("gedcom.quality", "write", marker="d"),
            "root_person_ref": "person:fictional-root",
        },
    )

    assert subtree.status_code == 200
    assert quality.status_code == 200
    subtree_request = cast("SubtreeRequest", gedcom_job_facade.submit_subtree.call_args.args[0])
    quality_request = cast("QualityRequest", gedcom_job_facade.submit_quality.call_args.args[0])
    assert (subtree_request.scope, subtree_request.generations) == ("ancestors", 4)
    assert subtree_request.gedcom_version == "5.5.1"
    assert quality_request.provider.provider_id == "none"


def test_sync_update_requires_and_maps_scoped_snapshots(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
) -> None:
    response = api_client.post(
        f"{API_NAMESPACE}/gedcom/sync",
        headers=api_headers,
        json={
            "schema_version": 1,
            "sync_command": "update",
            "master": _grant("gedcom.sync", "read", marker="a"),
            "release_root": _grant("gedcom.sync", "write", marker="b"),
            "snapshots": [
                {
                    "source_id": "fictional-source",
                    "vendor": "other",
                    "artifact": _grant("gedcom.sync", "read", marker="c"),
                    "exported_at": "2026-08-27T12:00:00+00:00",
                }
            ],
            "initialize_manifest": True,
            "quality_root_person_ref": "person:fictional-root",
        },
    )

    assert response.status_code == 200
    request = cast("SyncRequest", gedcom_job_facade.submit_sync.call_args.args[0])
    assert request.sync_command == "update"
    assert request.initialize_manifest is True
    assert request.snapshots[0].source_id == "fictional-source"
    assert request.provider.provider_id == "none"


def test_non_offline_provider_requires_explicit_profile_and_model(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
) -> None:
    response = api_client.post(
        f"{API_NAMESPACE}/gedcom/quality",
        headers=api_headers,
        json={
            "schema_version": 1,
            "source": _grant("gedcom.quality", "read", marker="a"),
            "output": _grant("gedcom.quality", "write", marker="b"),
            "root_person_ref": "person:fictional-root",
            "provider": {"provider_id": "openai", "profile_id": "fictional"},
        },
    )

    assert response.status_code == 400
    gedcom_job_facade.submit_quality.assert_not_called()


def test_local_provider_selection_can_omit_consent(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
) -> None:
    response = api_client.post(
        f"{API_NAMESPACE}/gedcom/quality",
        headers=api_headers,
        json={
            "schema_version": 1,
            "source": _grant("gedcom.quality", "read", marker="a"),
            "output": _grant("gedcom.quality", "write", marker="b"),
            "root_person_ref": "person:fictional-root",
            "provider": {
                "provider_id": "ollama",
                "profile_id": "fictional-local",
                "model_id": "fixture",
            },
        },
    )

    assert response.status_code == 200
    request = cast("QualityRequest", gedcom_job_facade.submit_quality.call_args.args[0])
    assert request.provider.provider_id == "ollama"
    assert request.provider.profile_id == "fictional-local"
    assert request.provider.model_id == "fixture"
    assert request.provider.consent_id is None


def test_completed_inspect_result_stays_structured_and_path_free(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
) -> None:
    artifact = ArtifactRef(
        artifact_id="art_" + ("a" * 64),
        media_type="application/x-gedcom",
        artifact_type="gedcom",
        size_bytes=128,
        status=ArtifactStatus.READY,
        sha256="b" * 64,
    )
    gedcom_job_facade.result.return_value = GedcomInspectResult(
        summary=GedcomSourceSummary(
            source=artifact,
            gedcom_version="5.5.5",
            individual_count=2,
            family_count=1,
            other_record_count=0,
        ),
        findings=(),
        root_candidates=(
            RootCandidate(person_ref="person:fictional-root", reason_code="single-root"),
        ),
    )

    response = api_client.get(
        f"{API_NAMESPACE}/gedcom/jobs/j000001/result",
        headers=api_headers,
    )

    assert response.status_code == 200
    assert response.json()["operation"] == "gedcom.inspect"
    assert response.json()["value"]["summary"]["individual_count"] == 2
    assert '"path"' not in response.text
    assert "/private/" not in response.text
    gedcom_job_facade.result.assert_called_once_with("j000001")


def test_completed_inspection_returns_a_bounded_summary_for_a_large_source() -> None:
    artifact = ArtifactRef(
        artifact_id="art_" + ("a" * 64),
        media_type="application/x-gedcom",
        artifact_type="gedcom",
        size_bytes=128,
        status=ArtifactStatus.READY,
        sha256="b" * 64,
    )
    result = GedcomInspectResult(
        summary=GedcomSourceSummary(
            source=artifact,
            gedcom_version="5.5.5",
            individual_count=12_000,
            family_count=0,
            other_record_count=0,
        ),
        findings=(),
        root_candidates=tuple(
            RootCandidate(
                person_ref=f"person:{index:032x}",
                reason_code="individual-record",
            )
            for index in range(12_000)
        ),
    )

    response = GedcomResultResponse.from_application(result)

    assert response.value["root_candidate_count"] == 12_000
    assert "root_candidates" not in response.value
    assert len(response.model_dump_json().encode()) < 16_384


@pytest.mark.parametrize(
    "payload",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"query": "x" * 129},
        {"cursor": "x" * 257},
        {"source_path": "/private/fictional.ged"},
    ],
)
def test_root_candidate_query_rejects_unbounded_or_unknown_inputs(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
    payload: dict[str, object],
) -> None:
    response = api_client.post(
        f"{API_NAMESPACE}/gedcom/jobs/j000001/root-candidates",
        headers=api_headers,
        json=payload,
    )

    assert response.status_code == 400
    gedcom_job_facade.root_candidates.assert_not_called()


def test_root_candidate_query_returns_a_bounded_private_page(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
) -> None:
    gedcom_job_facade.root_candidates.return_value = RootCandidatePage(
        candidates=(
            RootCandidate(
                person_ref="person:fictional-root",
                reason_code="individual-record",
                display_name="Ada Example",
                source_identifier="@I1@",
                birth_date="1900",
                relationship_summary="0 parents, 1 partners, 2 children",
            ),
        ),
        total_count=2,
        next_cursor="c1_00000001_" + "a" * 64,
    )
    response = api_client.post(
        f"{API_NAMESPACE}/gedcom/jobs/j000001/root-candidates",
        headers=api_headers,
        json={"query": "Ada", "limit": 1},
    )
    assert response.status_code == 200
    assert response.json()["candidates"][0]["source_identifier"] == "@I1@"
    assert response.json()["total_count"] == 2
    assert response.json()["next_cursor"] is not None
    gedcom_job_facade.root_candidates.assert_called_once_with(
        "j000001", query="Ada", limit=1, cursor=None
    )


@pytest.mark.parametrize(
    ("code", "status"),
    [("GEDCOM_ROOT_CURSOR_INVALID", 400), ("GEDCOM_JOB_RESULT_UNAVAILABLE", 409)],
)
def test_root_candidate_query_maps_coded_failures(
    api_client: TestClient,
    api_headers: dict[str, str],
    gedcom_job_facade: Mock,
    code: str,
    status: int,
) -> None:
    gedcom_job_facade.root_candidates.side_effect = AncestryError(code, "Unavailable.")
    response = api_client.post(
        f"{API_NAMESPACE}/gedcom/jobs/j000001/root-candidates",
        headers=api_headers,
        json={},
    )
    assert response.status_code == status
    assert response.json()["code"] == code


def test_root_candidate_query_authenticates_before_processing(
    api_client: TestClient, gedcom_job_facade: Mock
) -> None:
    response = api_client.post(
        f"{API_NAMESPACE}/gedcom/jobs/j000001/root-candidates",
        content=b'{"query":"private search',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert "private search" not in response.text
    gedcom_job_facade.root_candidates.assert_not_called()
