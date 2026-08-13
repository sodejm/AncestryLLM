"""Authenticated HTTP contract for bounded transient chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application.chat import (
    ChatDataClass,
    ChatPurpose,
    ChatRunRequest,
    ChatSessionCreateRequest,
)
from ancestryllm.core.errors import ProviderError, SecurityPolicyError

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
        "streaming": False,
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
