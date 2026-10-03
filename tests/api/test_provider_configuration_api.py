"""Provider, endpoint, and consent contracts for the desktop settings UI."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ancestryllm.api import API_NAMESPACE

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from ancestryllm.core.secrets import MemorySecretStore


def _create_remote_profile(
    api_client: TestClient,
    api_headers: dict[str, str],
    *,
    revision: str,
) -> dict[str, object]:
    validation = api_client.post(
        f"{API_NAMESPACE}/provider-endpoints/validate",
        headers=api_headers,
        json={
            "schema_version": 1,
            "provider_id": "openai",
            "endpoint": "https://api.openai.com/v1",
        },
    )
    assert validation.status_code == 200, validation.text
    response = api_client.post(
        f"{API_NAMESPACE}/provider-profiles",
        headers=api_headers,
        json={
            "schema_version": 1,
            "expected_revision": revision,
            "name": "reviewed-cloud",
            "provider_id": "openai",
            "model": "gpt-4o-mini",
            "endpoint": "https://api.openai.com/v1",
            "endpoint_identity_sha256": validation.json()["destination_digest"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_provider_profiles_are_revision_checked_and_never_expose_secrets(
    api_client: TestClient,
    api_headers: dict[str, str],
    secret_store: MemorySecretStore,
) -> None:
    marker = "test-only-secret-that-must-not-cross-the-boundary"
    secret_response = api_client.post(
        f"{API_NAMESPACE}/secrets/openai.api_key/set",
        headers=api_headers,
        json={"value": marker},
    )
    assert secret_response.status_code == 200

    empty = api_client.get(f"{API_NAMESPACE}/provider-configuration", headers=api_headers).json()
    assert empty == {
        "schema_version": 1,
        "revision": empty["revision"],
        "profiles": [],
        "consents": [],
    }
    assert re.fullmatch(r"[0-9a-f]{64}", empty["revision"])

    created = _create_remote_profile(api_client, api_headers, revision=empty["revision"])
    assert created["profiles"] == [
        {
            "name": "reviewed-cloud",
            "provider_id": "openai",
            "model": "gpt-4o-mini",
            "endpoint": "https://api.openai.com/v1",
            "endpoint_kind": "remote",
            "secret_reference": "openai.api_key",
            "enabled": True,
        }
    ]
    assert marker not in str(created)
    assert secret_store.values["openai.api_key"] == marker

    stale = api_client.post(
        f"{API_NAMESPACE}/provider-profiles",
        headers=api_headers,
        json={
            "schema_version": 1,
            "expected_revision": empty["revision"],
            "name": "must-not-exist",
            "provider_id": "ollama",
            "model": "test-model",
            "endpoint": "http://127.0.0.1:11434",
            "endpoint_identity_sha256": "0" * 64,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "PROVIDER_CONFIGURATION_CONFLICT"
    assert [item["name"] for item in created["profiles"]] == ["reviewed-cloud"]


def test_endpoint_validation_is_explicit_pinned_and_sanitized(
    api_client: TestClient,
    api_headers: dict[str, str],
) -> None:
    response = api_client.post(
        f"{API_NAMESPACE}/provider-endpoints/validate",
        headers=api_headers,
        json={
            "schema_version": 1,
            "provider_id": "ollama",
            "endpoint": "http://127.0.0.1:11434",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "schema_version": 1,
        "status": "reachable",
        "endpoint_kind": "loopback",
        "http_status": 200,
        "destination_digest": response.json()["destination_digest"],
    }
    assert re.fullmatch(r"[0-9a-f]{64}", response.json()["destination_digest"])
    assert "127.0.0.1" not in response.json()["destination_digest"]

    rejected = api_client.post(
        f"{API_NAMESPACE}/provider-endpoints/validate",
        headers=api_headers,
        json={
            "schema_version": 1,
            "provider_id": "ollama",
            "endpoint": "http://192.0.2.10:11434",
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "ENDPOINT_REJECTED"
    assert "192.0.2.10" not in rejected.text


def test_consent_preview_must_be_reviewed_before_atomic_save_and_revoke(
    api_client: TestClient,
    api_headers: dict[str, str],
) -> None:
    empty = api_client.get(f"{API_NAMESPACE}/provider-configuration", headers=api_headers).json()
    configuration = _create_remote_profile(api_client, api_headers, revision=empty["revision"])
    preview_response = api_client.post(
        f"{API_NAMESPACE}/consents/preview",
        headers=api_headers,
        json={
            "schema_version": 1,
            "provider_profile_name": "reviewed-cloud",
            "modules": ["summary"],
            "purposes": ["genealogy-analysis"],
            "data_classes": ["living_person"],
            "models": ["gpt-4o-mini"],
            "max_cost_usd": 1.25,
            "retain_payloads": True,
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["provider_id"] == "openai"
    assert preview["warning_codes"] == [
        "LIVING_PERSON_DATA_INCLUDED",
        "REMOTE_PROVIDER_SELECTED",
        "REMOTE_RETENTION_ENABLED",
    ]

    create_response = api_client.post(
        f"{API_NAMESPACE}/consents",
        headers=api_headers,
        json={
            "schema_version": 1,
            "expected_revision": configuration["revision"],
            "name": "reviewed-consent",
            "preview": preview,
        },
    )
    assert create_response.status_code == 200, create_response.text
    created = create_response.json()
    assert created["consents"] == [
        {
            "name": "reviewed-consent",
            "provider_profile_name": "reviewed-cloud",
            "provider_id": "openai",
            "modules": ["summary"],
            "purposes": ["genealogy-analysis"],
            "data_classes": ["living_person"],
            "models": ["gpt-4o-mini"],
            "max_cost_usd": 1.25,
            "retain_payloads": True,
            "active": True,
        }
    ]

    stale = api_client.post(
        f"{API_NAMESPACE}/consents/reviewed-consent/revoke",
        headers=api_headers,
        json={
            "schema_version": 1,
            "expected_revision": configuration["revision"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "PROVIDER_CONFIGURATION_CONFLICT"

    revoked = api_client.post(
        f"{API_NAMESPACE}/consents/reviewed-consent/revoke",
        headers=api_headers,
        json={"schema_version": 1, "expected_revision": created["revision"]},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["consents"][0]["active"] is False


def test_provider_requests_reject_unknown_fields_without_echoing_them(
    api_client: TestClient,
    api_headers: dict[str, str],
) -> None:
    marker = "must-not-be-echoed"
    response = api_client.post(
        f"{API_NAMESPACE}/provider-endpoints/validate",
        headers=api_headers,
        json={
            "schema_version": 1,
            "provider_id": "ollama",
            "endpoint": "http://127.0.0.1:11434",
            "credential": marker,
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "REQUEST_INVALID"
    assert marker not in response.text
