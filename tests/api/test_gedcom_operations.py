"""Contracts for path-free GEDCOM operation submission and result retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ancestryllm.api import API_NAMESPACE
from ancestryllm.application.dto import ArtifactRef, ArtifactStatus
from ancestryllm.application.operations import (
    GedcomInspectRequest,
    GedcomInspectResult,
    GedcomSourceSummary,
    MergeRequest,
    QualityRequest,
    RootCandidate,
    SubtreeRequest,
    SyncRequest,
)

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


def test_remote_provider_requires_explicit_profile_model_and_consent(
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
