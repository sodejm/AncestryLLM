"""Contract tests for authenticated background-job control and event replay."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ancestryllm.api import API_NAMESPACE
from ancestryllm.application.dto import ArtifactRef, ArtifactStatus

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from ancestryllm.application.jobs import JobLifecycleService
    from ancestryllm.core.jobs import JobReporter


def _wait(job_service: JobLifecycleService, job_id: str) -> None:
    job_service.manager.wait(job_id, timeout=2.0)


def test_jobs_list_is_bounded_and_rejects_query_variants(
    api_client: TestClient,
    api_headers: dict[str, str],
) -> None:
    response = api_client.get(f"{API_NAMESPACE}/jobs", headers=api_headers)
    rejected = api_client.get(f"{API_NAMESPACE}/jobs?limit=1", headers=api_headers)

    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "jobs": []}
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "REQUEST_QUERY_FORBIDDEN"


def test_completed_job_exposes_only_opaque_resources_and_artifact_metadata(
    api_client: TestClient,
    api_headers: dict[str, str],
    job_service: JobLifecycleService,
) -> None:
    private_marker = "PRIVATE-JOB-RESOURCE"
    artifact = ArtifactRef(
        artifact_id="art_" + ("a" * 32),
        media_type="application/json",
        artifact_type="gedcom_export",
        size_bytes=37,
        status=ArtifactStatus.READY,
        sha256="b" * 64,
    )
    submitted = job_service.manager.submit(
        "Fictional export",
        lambda: artifact,
        resource_keys=(f"/private/{private_marker}/tree.ged",),
    )
    _wait(job_service, submitted.job_id)

    response = api_client.get(
        f"{API_NAMESPACE}/jobs/{submitted.job_id}",
        headers=api_headers,
    )
    listing = api_client.get(f"{API_NAMESPACE}/jobs", headers=api_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "completed"
    assert payload["resource_refs"][0].startswith("resource_")
    assert len(payload["resource_refs"][0]) == len("resource_") + 64
    assert payload["artifact"] == {
        "artifact_id": artifact.artifact_id,
        "media_type": artifact.media_type,
        "artifact_type": artifact.artifact_type,
        "size_bytes": artifact.size_bytes,
        "status": "ready",
        "sha256": artifact.sha256,
    }
    assert listing.json()["jobs"][0]["job_id"] == submitted.job_id
    assert private_marker not in response.text
    assert "/private/" not in response.text
    assert private_marker not in listing.text


def test_uncaught_job_failure_is_sanitized_at_the_api_boundary(
    api_client: TestClient,
    api_headers: dict[str, str],
    job_service: JobLifecycleService,
) -> None:
    private_marker = "PRIVATE-JOB-FAILURE"

    def fail() -> None:
        raise RuntimeError(f"failed while reading /private/{private_marker}/tree.ged")

    submitted = job_service.manager.submit("Fictional failure", fail)
    _wait(job_service, submitted.job_id)

    response = api_client.get(
        f"{API_NAMESPACE}/jobs/{submitted.job_id}",
        headers=api_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "failed"
    assert payload["error_code"] == "JOB_FAILED"
    assert payload["error_message"] == "The background job failed."
    assert private_marker not in response.text
    assert "/private/" not in response.text


def test_terminal_job_stream_replays_sse_and_accepts_latest_cursor(
    api_client: TestClient,
    api_headers: dict[str, str],
    job_service: JobLifecycleService,
) -> None:
    submitted = job_service.manager.submit("Fictional completion", lambda: None)
    _wait(job_service, submitted.job_id)
    snapshot = job_service.get(submitted.job_id)

    replay = api_client.get(
        f"{API_NAMESPACE}/jobs/{submitted.job_id}/events",
        headers=api_headers,
    )
    acknowledged = api_client.get(
        f"{API_NAMESPACE}/jobs/{submitted.job_id}/events",
        headers=api_headers | {"Last-Event-ID": str(snapshot.sequence)},
    )

    assert replay.status_code == 200
    assert replay.headers["content-type"].startswith("text/event-stream")
    assert "event: terminal\n" in replay.text
    assert f'"job_id":"{submitted.job_id}"' in replay.text
    assert acknowledged.status_code == 200
    assert acknowledged.text == ""


def test_job_stream_rejects_invalid_or_expired_cursors(
    api_client: TestClient,
    api_headers: dict[str, str],
    job_service: JobLifecycleService,
) -> None:
    submitted = job_service.manager.submit("Fictional completion", lambda: None)
    _wait(job_service, submitted.job_id)

    malformed = api_client.get(
        f"{API_NAMESPACE}/jobs/{submitted.job_id}/events",
        headers=api_headers | {"Last-Event-ID": "not-a-number"},
    )
    future = api_client.get(
        f"{API_NAMESPACE}/jobs/{submitted.job_id}/events",
        headers=api_headers | {"Last-Event-ID": "9999999999"},
    )

    assert malformed.status_code == 400
    assert malformed.json()["code"] == "JOB_EVENT_CURSOR_INVALID"
    assert future.status_code == 400
    assert future.json()["code"] == "JOB_EVENT_CURSOR_INVALID"


def test_job_identifier_and_route_shapes_fail_closed(
    api_client: TestClient,
    api_headers: dict[str, str],
) -> None:
    malformed = api_client.get(f"{API_NAMESPACE}/jobs/not-a-job", headers=api_headers)
    extra = api_client.get(f"{API_NAMESPACE}/jobs/j000001/extra", headers=api_headers)
    missing = api_client.get(f"{API_NAMESPACE}/jobs/j000001", headers=api_headers)

    assert malformed.status_code == 400
    assert malformed.json()["code"] == "JOB_ID_INVALID"
    assert extra.status_code == 404
    assert extra.json()["code"] == "ROUTE_UNAVAILABLE"
    assert missing.status_code == 404
    assert missing.json()["code"] == "JOB_NOT_FOUND"


def test_cancel_route_cooperatively_reaches_one_cancelled_terminal_state(
    api_client: TestClient,
    api_headers: dict[str, str],
    job_service: JobLifecycleService,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def work(reporter: JobReporter) -> None:
        entered.set()
        while not release.wait(0.005):
            reporter.check_cancelled()
        reporter.check_cancelled()

    submitted = job_service.manager.submit_with_progress("Fictional cancellation", work)
    assert entered.wait(timeout=1.0)
    try:
        response = api_client.post(
            f"{API_NAMESPACE}/jobs/{submitted.job_id}/cancel",
            headers=api_headers,
        )
    finally:
        release.set()
    _wait(job_service, submitted.job_id)
    terminal = api_client.get(
        f"{API_NAMESPACE}/jobs/{submitted.job_id}",
        headers=api_headers,
    )

    assert response.status_code == 200
    assert response.json()["state"] in {"cancelling", "cancelled"}
    assert terminal.status_code == 200
    assert terminal.json()["state"] == "cancelled"
    assert terminal.json()["error_code"] == "JOB_CANCELLED"
    events = job_service.repository.replay(submitted.job_id, after=0).events
    assert sum(event.kind.value == "terminal" for event in events) == 1


def test_shutdown_handshake_succeeds_only_after_jobs_reach_a_safe_boundary(
    api_client: TestClient,
    api_headers: dict[str, str],
    job_service: JobLifecycleService,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def protected_work(reporter: JobReporter) -> None:
        with reporter.non_interruptible("atomic_commit"):
            entered.set()
            assert release.wait(timeout=2.0)
        reporter.check_cancelled()

    submitted = job_service.manager.submit_with_progress(
        "Fictional protected operation",
        protected_work,
    )
    assert entered.wait(timeout=1.0)
    try:
        vetoed = api_client.post(
            f"{API_NAMESPACE}/jobs/shutdown",
            headers=api_headers,
            json={"schema_version": 1, "action": "cancel", "timeout_seconds": 0},
        )
    finally:
        release.set()
    _wait(job_service, submitted.job_id)
    accepted = api_client.post(
        f"{API_NAMESPACE}/jobs/shutdown",
        headers=api_headers,
        json={"schema_version": 1, "action": "wait", "timeout_seconds": 0},
    )

    assert vetoed.status_code == 409
    assert vetoed.json()["code"] == "JOB_SHUTDOWN_TIMEOUT"
    assert accepted.status_code == 200
    assert accepted.json() == {
        "schema_version": 1,
        "safe_to_quit": True,
        "active_jobs": [],
    }
