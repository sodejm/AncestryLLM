"""Versioned settings and write-only secret API contracts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ancestryllm.api import API_NAMESPACE
from ancestryllm.api.openapi import canonical_openapi, contract_app
from ancestryllm.core.errors import StorageError

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

    from ancestryllm.core.secrets import MemorySecretStore


def test_settings_get_exposes_only_reviewed_non_secret_schema(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    response = api_client.get(f"{API_NAMESPACE}/settings", headers=api_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["revision"] == 0
    assert {field["key"] for field in payload["fields"]} == {
        "providers.default",
        "limits.max_query_rows",
        "limits.max_output_chars",
        "limits.query_timeout_seconds",
        "limits.provider_timeout_seconds",
    }
    assert all(field["label"] and field["help"] for field in payload["fields"])
    assert all(field["sensitive"] is False for field in payload["fields"])
    assert "api_key" not in response.text
    assert "config_path" not in response.text


def test_startup_diagnostics_are_versioned_typed_and_path_free(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    response = api_client.get(f"{API_NAMESPACE}/startup-diagnostics", headers=api_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["status"] == "ready"
    assert set(payload["platform"]) == {"operating_system", "architecture"}
    assert payload["components"]
    assert all(
        set(item)
        == {
            "component",
            "status",
            "code",
            "message",
            "remediation",
            "restart_required",
            "blocks_mutations",
        }
        for item in payload["components"]
    )
    assert "/" not in json.dumps(payload)
    assert "\\" not in json.dumps(payload)


def test_settings_patch_is_allowlisted_revision_checked_and_secret_free(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    response = api_client.patch(
        f"{API_NAMESPACE}/settings",
        headers=api_headers,
        json={
            "schema_version": 1,
            "expected_revision": 0,
            "changes": {"providers.default": "ollama", "limits.max_query_rows": 250},
        },
    )
    assert response.status_code == 200
    assert response.json()["revision"] == 1

    stale = api_client.patch(
        f"{API_NAMESPACE}/settings",
        headers=api_headers,
        json={
            "schema_version": 1,
            "expected_revision": 0,
            "changes": {"limits.max_query_rows": 251},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "SETTINGS_REVISION_CONFLICT"

    marker = "test-only-credential-material"
    rejected = api_client.patch(
        f"{API_NAMESPACE}/settings",
        headers=api_headers,
        json={
            "schema_version": 1,
            "expected_revision": 1,
            "changes": {"openai.api_key": marker},
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "SETTINGS_FIELD_UNKNOWN"
    assert marker not in rejected.text


def test_settings_patch_rejects_unknown_schema_with_a_stable_redacted_error(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    marker = "test-only-credential-material"
    response = api_client.patch(
        f"{API_NAMESPACE}/settings",
        headers=api_headers,
        json={
            "schema_version": 2,
            "expected_revision": 0,
            "changes": {"openai.api_key": marker},
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "SETTINGS_SCHEMA_UNSUPPORTED"
    assert marker not in response.text


def test_secret_routes_expose_only_status_and_never_echo_values(
    api_client: TestClient,
    api_headers: dict[str, str],
    secret_store: MemorySecretStore,
) -> None:
    reference = "openai.api_key"
    base = f"{API_NAMESPACE}/secrets/{reference}"
    marker = "test-only-credential-material"

    missing = api_client.get(f"{base}/status", headers=api_headers)
    assert missing.status_code == 200
    assert missing.json() == {"reference": reference, "status": "missing"}

    saved = api_client.post(f"{base}/set", headers=api_headers, json={"value": marker})
    assert saved.status_code == 200
    assert saved.json() == {"reference": reference, "status": "present"}
    assert marker not in saved.text
    assert secret_store.values == {reference: marker}

    status = api_client.get(f"{base}/status", headers=api_headers)
    assert status.json() == {"reference": reference, "status": "present"}
    assert marker not in status.text

    deleted = api_client.post(f"{base}/delete", headers=api_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"reference": reference, "status": "missing"}
    assert secret_store.values == {}


def test_secret_routes_reject_unknown_references_without_echoing_input(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    marker = "test-only-credential-material"
    response = api_client.post(
        f"{API_NAMESPACE}/secrets/unreviewed.credential/set",
        headers=api_headers,
        json={"value": marker},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "SECRET_REFERENCE_UNKNOWN"
    assert marker not in response.text
    assert "unreviewed.credential" not in response.text


def test_secret_request_validation_never_echoes_rejected_input(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    marker = "test-only-credential-material"
    response = api_client.post(
        f"{API_NAMESPACE}/secrets/openai.api_key/set",
        headers=api_headers,
        json={"value": marker, "unexpected": marker},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "REQUEST_INVALID"
    assert marker not in response.text
    assert "unexpected" not in response.text


def test_keyring_failure_is_stable_and_never_exposes_backend_or_secret_details(
    api_client: TestClient,
    api_headers: dict[str, str],
    secret_store: MemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_marker = "test-only-credential-material"
    backend_marker = "test-only-keyring-backend-detail"

    def fail_set(_self: object, _name: str, _value: str) -> None:
        raise StorageError(
            "KEYRING_WRITE_UNVERIFIED",
            "The OS keyring could not verify the credential write.",
            details={"backend": backend_marker},
        ) from RuntimeError(backend_marker)

    monkeypatch.setattr(type(secret_store), "set", fail_set)
    response = api_client.post(
        f"{API_NAMESPACE}/secrets/openai.api_key/set",
        headers=api_headers,
        json={"value": secret_marker},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "KEYRING_WRITE_UNVERIFIED"
    assert response.json()["details"] == []
    assert secret_marker not in response.text
    assert backend_marker not in response.text


def test_openapi_marks_secret_input_write_only_and_has_no_readback_contract() -> None:
    rendered = canonical_openapi(contract_app())
    schema = json.loads(rendered)

    assert schema["paths"].keys() == {
        f"{API_NAMESPACE}/capabilities",
        f"{API_NAMESPACE}/chat/capability",
        f"{API_NAMESPACE}/chat/sessions",
        f"{API_NAMESPACE}/chat/sessions/{{session_id}}",
        f"{API_NAMESPACE}/chat/sessions/{{session_id}}/runs",
        f"{API_NAMESPACE}/consents",
        f"{API_NAMESPACE}/consents/preview",
        f"{API_NAMESPACE}/consents/{{name}}/revoke",
        f"{API_NAMESPACE}/health",
        f"{API_NAMESPACE}/jobs",
        f"{API_NAMESPACE}/jobs/shutdown",
        f"{API_NAMESPACE}/jobs/{{job_id}}",
        f"{API_NAMESPACE}/jobs/{{job_id}}/cancel",
        f"{API_NAMESPACE}/jobs/{{job_id}}/events",
        f"{API_NAMESPACE}/provider-configuration",
        f"{API_NAMESPACE}/provider-endpoints/validate",
        f"{API_NAMESPACE}/provider-profiles",
        f"{API_NAMESPACE}/startup-diagnostics",
        f"{API_NAMESPACE}/settings",
        f"{API_NAMESPACE}/secrets/{{reference}}/status",
        f"{API_NAMESPACE}/secrets/{{reference}}/set",
        f"{API_NAMESPACE}/secrets/{{reference}}/delete",
    }
    secret_set = schema["components"]["schemas"]["SecretSetRequest"]
    assert (
        schema["components"]["schemas"]["SettingsResponse"]["properties"]["schema_version"]["const"]
        == 1
    )
    assert (
        schema["components"]["schemas"]["SettingsPatchRequest"]["properties"]["schema_version"][
            "const"
        ]
        == 1
    )
    assert secret_set["properties"]["value"]["writeOnly"] is True
    assert "value" not in schema["components"]["schemas"]["SecretStatusResponse"]["properties"]
    assert "example" not in rendered.casefold()
    assert "credential-material" not in rendered
