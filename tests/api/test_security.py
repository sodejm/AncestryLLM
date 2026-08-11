"""Tests for control API authentication and security boundaries."""

from __future__ import annotations

import asyncio
import io
import json
import socket
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from ancestryllm.api import (
    API_BUILD_HEADER,
    API_CONTRACT,
    API_NAMESPACE,
    API_VERSION_HEADER,
    ApiSettings,
    ApiVersion,
    CapabilityManifest,
    create_uvicorn_config,
    error_response,
)
from ancestryllm.api.errors import error_envelope as api_error_envelope
from ancestryllm.api.openapi import contract_app
from ancestryllm.application.errors import domain_failure_from_exception, map_domain_failure
from ancestryllm.application.errors import error_envelope as application_error_envelope
from ancestryllm.core.errors import AncestryError
from ancestryllm.terminal.presentation import PresentationAdapter

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_authentication_happens_before_route_or_body_processing(api_client: TestClient) -> None:
    response = api_client.request(
        "POST",
        f"{API_NAMESPACE}/missing",
        content=b'{"unterminated":',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_authenticated_body_without_content_length_is_rejected(api_client: TestClient) -> None:
    messages: list[dict[str, Any]] = []
    request_messages = iter(
        (
            {"type": "http.request", "body": b"unexpected", "more_body": False},
            {"type": "http.disconnect"},
        )
    )

    async def receive() -> dict[str, Any]:
        return next(request_messages)

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    settings = api_client.app.user_middleware[0].kwargs["settings"]
    asyncio.run(
        api_client.app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "scheme": "http",
                "method": "GET",
                "path": f"{API_NAMESPACE}/health",
                "raw_path": f"{API_NAMESPACE}/health".encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"host", b"127.0.0.1:8421"),
                    (b"authorization", f"Bearer {settings.bearer_token}".encode()),
                    (API_VERSION_HEADER.casefold().encode(), API_CONTRACT.encode()),
                    (API_BUILD_HEADER.casefold().encode(), settings.app_build.encode()),
                ],
                "client": ("127.0.0.1", 49152),
                "server": ("127.0.0.1", 8421),
            },
            receive,
            send,
        )
    )

    response_start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    response_body = next(message for message in messages if message["type"] == "http.response.body")
    assert response_start["status"] == 400
    assert json.loads(response_body["body"])["code"] == "REQUEST_BODY_FORBIDDEN"


@pytest.mark.parametrize(
    ("method", "path_suffix", "header_update", "content", "expected_code"),
    (
        ("POST", "/health", {}, None, "METHOD_NOT_ALLOWED"),
        ("GET", "/health?unexpected=true", {}, None, "REQUEST_QUERY_FORBIDDEN"),
        (
            "GET",
            "/health",
            {"Content-Type": "application/json"},
            None,
            "REQUEST_CONTENT_TYPE_FORBIDDEN",
        ),
        ("GET", "/health", {}, b"x", "REQUEST_BODY_FORBIDDEN"),
        ("GET", "/health", {"Content-Length": str(1_048_577)}, None, "REQUEST_TOO_LARGE"),
    ),
)
def test_control_routes_reject_unexpected_request_state(
    api_client: TestClient,
    api_headers: dict[str, str],
    method: str,
    path_suffix: str,
    header_update: dict[str, str],
    content: bytes | None,
    expected_code: str,
) -> None:
    response = api_client.request(
        method,
        f"{API_NAMESPACE}{path_suffix}",
        headers=api_headers | header_update,
        content=content,
    )
    assert response.status_code >= 400
    assert response.json()["code"] == expected_code


@pytest.mark.parametrize(
    ("header_update", "expected_code"),
    (
        ({"Authorization": "Bearer wrong"}, "AUTHENTICATION_REQUIRED"),
        ({"Host": "localhost:8421"}, "REQUEST_HOST_INVALID"),
        ({"Origin": "http://127.0.0.1"}, "REQUEST_HEADER_FORBIDDEN"),
        ({"Cookie": "session=nope"}, "REQUEST_HEADER_FORBIDDEN"),
        ({"Forwarded": "for=127.0.0.1"}, "REQUEST_HEADER_FORBIDDEN"),
        ({"X-Forwarded-For": "127.0.0.1"}, "REQUEST_HEADER_FORBIDDEN"),
        ({API_VERSION_HEADER: "ancestryllm.internal-api/2"}, "API_VERSION_UNSUPPORTED"),
        ({API_BUILD_HEADER: "different-build"}, "APP_BUILD_MISMATCH"),
    ),
)
def test_handshake_and_browser_proxy_headers_fail_closed(
    api_client: TestClient,
    api_headers: dict[str, str],
    header_update: dict[str, str],
    expected_code: str,
) -> None:
    response = api_client.get(f"{API_NAMESPACE}/health", headers=api_headers | header_update)
    assert response.status_code >= 400
    assert response.json()["code"] == expected_code


def test_incompatible_app_and_sidecar_builds_fail_during_configuration() -> None:
    with pytest.raises(ValueError, match="matching app and sidecar builds"):
        ApiSettings(
            bearer_token="A" * 43,
            app_build="0.5.0-desktop",
            sidecar_build="0.5.0-sidecar",
            provider_id="none",
        )


def test_success_responses_never_enable_browser_state(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    response = api_client.get(f"{API_NAMESPACE}/health", headers=api_headers)
    assert response.headers["cache-control"] == "no-store"
    assert (
        response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "access-control-allow-origin" not in response.headers
    assert "set-cookie" not in response.headers


def test_server_configuration_is_ephemeral_loopback_with_graceful_lifespan() -> None:
    config = create_uvicorn_config(contract_app())
    assert config.host == "127.0.0.1"
    assert config.port == 0
    assert config.access_log is False
    assert config.proxy_headers is False
    assert config.forwarded_allow_ips == ""
    assert config.lifespan == "on"
    assert config.timeout_graceful_shutdown == 10


def test_provider_none_discovery_is_network_free_even_with_sdk_keys(
    api_client: TestClient, api_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(key, "unused-fictional-key")

    def network_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("provider none API discovery attempted network access")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    monkeypatch.setattr(socket.socket, "connect", network_forbidden)
    assert api_client.get(f"{API_NAMESPACE}/health", headers=api_headers).status_code == 200
    assert api_client.get(f"{API_NAMESPACE}/capabilities", headers=api_headers).status_code == 200


def test_models_are_strict_and_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ApiVersion.model_validate(
            {
                "namespace": API_NAMESPACE,
                "contract": API_CONTRACT,
                "application_contract": "ancestryllm.application/0.3",
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        CapabilityManifest.model_validate(
            {"api": {}, "modules": [], "request_policy": {}, "pagination": {}, "unexpected": True}
        )


def test_ancestry_errors_map_to_sanitized_stable_envelopes() -> None:
    response = error_response(
        AncestryError(
            "ARGUMENT_INVALID",
            "unsafe /Users/example/private/tree.ged",
            details={"path": "/Users/example/private/tree.ged"},
        ),
        correlation_ref="api_" + ("a" * 32),
    )
    body = response.body.decode()
    assert response.status_code == 400
    assert "REQUEST_INVALID" in body
    assert "The operation request is invalid." in body
    assert "/Users/example" not in body
    assert "tree.ged" not in body


def test_service_cli_json_and_api_error_semantics_do_not_drift() -> None:
    correlation_ref = "api_" + ("b" * 32)
    error = AncestryError(
        "ARGUMENT_INVALID",
        "unsafe /Users/example/private/tree.ged",
        details={"path": "/Users/example/private/tree.ged"},
    )
    sanitized = map_domain_failure(domain_failure_from_exception(error))
    application_envelope = application_error_envelope(sanitized, correlation_ref=correlation_ref)
    output = io.StringIO()
    PresentationAdapter.for_file(output).render(application_envelope, json_output=True)
    status_code, api_envelope = api_error_envelope(error, correlation_ref=correlation_ref)
    assert status_code == 400
    assert api_envelope.model_dump(mode="json") == json.loads(output.getvalue())
