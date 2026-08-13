"""Authenticated HTTP contract for bounded transient chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application.chat import (
    ChatDataClass,
    ChatPurpose,
    ChatRunRequest,
    ChatSessionCreateRequest,
)
from ancestryllm.core.errors import AncestryError, ProviderError, SecurityPolicyError

if TYPE_CHECKING:
    from unittest.mock import Mock

    from fastapi.testclient import TestClient


def _session_request() -> dict[str, object]:
    return {
        "schema_version": 1,
        "provider_profile_name": "fictional-local",
        "model": "fictional-model",
        "purpose": "genealogy_analysis",
        "data_classes": ["deceased_person"],
        "consent_name": None,
    }


def test_chat_capability_and_session_lifecycle_delegate_to_one_service_boundary(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_service: Mock,
) -> None:
    capability = api_client.get("/api/v1/chat/capability", headers=api_headers)
    started = api_client.post(
        "/api/v1/chat/sessions",
        headers=api_headers,
        json=_session_request(),
    )
    session_id = "chat_" + ("a" * 32)
    fetched = api_client.get(f"/api/v1/chat/sessions/{session_id}", headers=api_headers)
    completed = api_client.post(
        f"/api/v1/chat/sessions/{session_id}/runs",
        headers=api_headers,
        json={
            "schema_version": 1,
            "message": "Analyze this fictional deceased-person record.",
            "max_output_tokens": 4096,
            "temperature": 1.0,
            "timeout_seconds": 120,
            "max_safe_retries": 1,
        },
    )
    deleted = api_client.delete(f"/api/v1/chat/sessions/{session_id}", headers=api_headers)

    assert capability.status_code == 200
    assert capability.json() == {
        "schema_version": 1,
        "max_active_sessions": 32,
        "max_messages": 32,
        "max_message_characters": 16384,
        "max_context_characters": 65536,
        "max_output_tokens": 4096,
        "max_temperature": 1.0,
        "max_timeout_seconds": 120.0,
        "max_safe_retries": 1,
        "transient": True,
        "tools_enabled": False,
        "payload_retention": False,
        "output_is_evidence": False,
        "streaming": True,
        "stream_replay_max_bytes": 262144,
    }
    assert started.status_code == 200
    assert started.json()["session_id"] == session_id
    assert started.json()["transient"] is True
    assert started.json()["payload_retention"] is False
    assert fetched.status_code == 200
    assert completed.status_code == 200
    assert completed.json()["assistant_message"] == {
        "role": "assistant",
        "content": "Fictional response with no evidentiary status.",
    }
    assert completed.json()["output_is_evidence"] is False
    assert completed.json()["retained_payload"] is False
    assert deleted.status_code == 204
    assert deleted.content == b""

    chat_service.capability.assert_called_once_with()
    chat_service.start.assert_called_once_with(
        ChatSessionCreateRequest(
            provider_profile_name="fictional-local",
            model="fictional-model",
            purpose=ChatPurpose.GENEALOGY_ANALYSIS,
            data_classes=(ChatDataClass.DECEASED_PERSON,),
        )
    )
    chat_service.get.assert_called_once_with(session_id)
    chat_service.run.assert_called_once_with(
        session_id,
        ChatRunRequest(
            message="Analyze this fictional deceased-person record.",
            max_output_tokens=4096,
            temperature=1.0,
            timeout_seconds=120,
            max_safe_retries=1,
        ),
    )
    chat_service.teardown.assert_called_once_with(session_id)


def test_chat_run_rejects_out_of_contract_bounds_before_service_execution(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_service: Mock,
) -> None:
    response = api_client.post(
        f"/api/v1/chat/sessions/chat_{'b' * 32}/runs",
        headers=api_headers,
        json={"schema_version": 1, "message": "Fictional request.", "max_output_tokens": 4097},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "REQUEST_INVALID"
    chat_service.run.assert_not_called()


def test_chat_session_rejects_duplicate_data_classes_before_service_execution(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_service: Mock,
) -> None:
    request = _session_request()
    request["data_classes"] = ["deceased_person", "deceased_person"]

    response = api_client.post(
        "/api/v1/chat/sessions",
        headers=api_headers,
        json=request,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "REQUEST_INVALID"
    chat_service.start.assert_not_called()


def test_chat_provider_failure_is_sanitized_at_the_http_boundary(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_service: Mock,
) -> None:
    private_marker = "PRIVATE-PROVIDER-RESPONSE-BODY"
    chat_service.run.side_effect = ProviderError(
        "PROVIDER_TRANSIENT",
        private_marker,
        details={"response_body": private_marker},
    )

    response = api_client.post(
        f"/api/v1/chat/sessions/chat_{'c' * 32}/runs",
        headers=api_headers,
        json={"schema_version": 1, "message": "Fictional request."},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"
    assert private_marker not in response.text


def test_chat_consent_failure_is_sanitized_at_the_http_boundary(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_service: Mock,
) -> None:
    private_marker = "PRIVATE-CONSENT-DETAIL"
    chat_service.run.side_effect = SecurityPolicyError(
        "CONSENT_INACTIVE",
        private_marker,
        details={"consent": private_marker},
    )

    response = api_client.post(
        f"/api/v1/chat/sessions/chat_{'d' * 32}/runs",
        headers=api_headers,
        json={"schema_version": 1, "message": "Fictional request."},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "PROVIDER_CONSENT_REQUIRED"
    assert private_marker not in response.text


def test_chat_stream_start_delegates_to_the_ordered_stream_service(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_streaming_service: Mock,
) -> None:
    session_id = "chat_" + ("a" * 32)

    response = api_client.post(
        f"/api/v1/chat/sessions/{session_id}/streams",
        headers=api_headers,
        json={
            "schema_version": 1,
            "message": "Stream this fictional record analysis.",
            "max_output_tokens": 512,
            "temperature": 0.0,
            "timeout_seconds": 30,
            "max_safe_retries": 0,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "session_id": session_id,
        "run_id": "run_" + ("b" * 32),
        "state": "active",
        "latest_sequence": 1,
        "terminal": False,
    }
    chat_streaming_service.start.assert_awaited_once_with(
        session_id,
        ChatRunRequest(
            message="Stream this fictional record analysis.",
            max_output_tokens=512,
            temperature=0.0,
            timeout_seconds=30,
            max_safe_retries=0,
        ),
    )


def test_chat_stream_events_emit_strict_ordered_sse_and_honor_replay_cursor(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_streaming_service: Mock,
) -> None:
    session_id = "chat_" + ("a" * 32)
    run_id = "run_" + ("b" * 32)

    response = api_client.get(
        f"/api/v1/chat/sessions/{session_id}/streams/{run_id}/events",
        headers={**api_headers, "Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text == (
        'id: 1\nevent: active\ndata: {"payload":{"code":null,"message_count":null,'
        '"model":"fictional-model","provider_id":"ollama","remote":false,"text":null},'
        '"run_id":"run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","schema_version":1,'
        '"sequence":1,"timestamp":"2026-08-13T12:00:00+00:00","type":"active"}\n\n'
        'id: 2\nevent: first-token\ndata: {"payload":{"code":null,"message_count":null,'
        '"model":null,"provider_id":null,"remote":null,"text":"Fictional "},'
        '"run_id":"run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","schema_version":1,'
        '"sequence":2,"timestamp":"2026-08-13T12:00:00+00:00","type":"first-token"}\n\n'
        'id: 3\nevent: delta\ndata: {"payload":{"code":null,"message_count":null,'
        '"model":null,"provider_id":null,"remote":null,"text":" "},'
        '"run_id":"run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","schema_version":1,'
        '"sequence":3,"timestamp":"2026-08-13T12:00:00+00:00","type":"delta"}\n\n'
        'id: 4\nevent: completed\ndata: {"payload":{"code":null,"message_count":2,'
        '"model":null,"provider_id":null,"remote":null,"text":null},'
        '"run_id":"run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","schema_version":1,'
        '"sequence":4,"timestamp":"2026-08-13T12:00:00+00:00","type":"completed"}\n\n'
    )
    chat_streaming_service.subscribe.assert_awaited_once_with(
        session_id,
        run_id,
        after_sequence=1,
    )


def test_chat_stream_cancel_is_owner_scoped_and_returns_terminal_state(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_streaming_service: Mock,
) -> None:
    session_id = "chat_" + ("a" * 32)
    run_id = "run_" + ("b" * 32)

    response = api_client.post(
        f"/api/v1/chat/sessions/{session_id}/streams/{run_id}/cancel",
        headers=api_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "session_id": session_id,
        "run_id": run_id,
        "state": "interrupted",
        "latest_sequence": 4,
        "terminal": True,
    }
    chat_streaming_service.cancel.assert_awaited_once_with(session_id, run_id)


def test_chat_stream_rejects_invalid_cursor_before_service_execution(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_streaming_service: Mock,
) -> None:
    response = api_client.get(
        f"/api/v1/chat/sessions/chat_{'a' * 32}/streams/run_{'b' * 32}/events",
        headers={**api_headers, "Last-Event-ID": "-1"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "CHAT_STREAM_CURSOR_INVALID"
    chat_streaming_service.subscribe.assert_not_awaited()


def test_chat_stream_replay_and_owner_failures_are_stable_http_errors(
    api_client: TestClient,
    api_headers: dict[str, str],
    chat_streaming_service: Mock,
) -> None:
    url = f"/api/v1/chat/sessions/chat_{'a' * 32}/streams/run_{'b' * 32}/events"
    chat_streaming_service.subscribe.side_effect = AncestryError(
        "CHAT_STREAM_REPLAY_EXPIRED",
        "The requested chat stream events are no longer buffered.",
    )

    expired = api_client.get(url, headers=api_headers)

    assert expired.status_code == 410
    assert expired.json()["code"] == "CHAT_STREAM_REPLAY_EXPIRED"

    chat_streaming_service.subscribe.reset_mock()
    chat_streaming_service.subscribe.side_effect = AncestryError(
        "CHAT_STREAM_NOT_FOUND",
        "The chat stream does not exist.",
    )
    missing = api_client.get(url, headers=api_headers)

    assert missing.status_code == 404
    assert missing.json()["code"] == "CHAT_STREAM_NOT_FOUND"
