"""Tests for the control API routes and shared metadata."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

from ancestryllm.api import API_CONTRACT, API_NAMESPACE, ApiSettings
from ancestryllm.application.dto import CONTRACT_VERSION
from ancestryllm.core.commands import BUILTIN_MODULES

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_health_requires_handshake_and_returns_token_derived_readiness(
    api_client: TestClient, api_headers: dict[str, str], api_settings: ApiSettings
) -> None:
    response = api_client.get(f"{API_NAMESPACE}/health", headers=api_headers)
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "api": {
            "namespace": API_NAMESPACE,
            "contract": API_CONTRACT,
            "application_contract": CONTRACT_VERSION,
        },
        "app_build": api_settings.app_build,
        "sidecar_build": api_settings.sidecar_build,
        "readiness_proof": hmac.new(
            api_settings.bearer_token.encode(),
            (f"{API_CONTRACT}\n{api_settings.app_build}\n{api_settings.sidecar_build}").encode(),
            hashlib.sha256,
        ).hexdigest(),
    }


def test_capabilities_are_shared_metadata_intersected_with_registered_handlers(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    response = api_client.get(f"{API_NAMESPACE}/capabilities", headers=api_headers)
    assert response.status_code == 200
    manifest = response.json()
    advertised = {
        module["module_id"]: tuple(action["dispatch_key"] for action in module["actions"])
        for module in manifest["modules"]
    }
    assert advertised == {
        "gedcom": (BUILTIN_MODULES["gedcom"].command.routes[0].key.value,),
        "providers": (BUILTIN_MODULES["providers"].command.routes[0].key.value,),
    }
    assert "ocr" not in advertised
    assert "secrets" not in advertised
    assert manifest["request_policy"]["max_body_bytes"] == 1_048_576
    assert manifest["pagination"]["maximum_limit"] == 100


def test_foundation_exposes_only_two_control_routes(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    for path in (
        f"{API_NAMESPACE}/commands",
        f"{API_NAMESPACE}/gedcom",
        f"{API_NAMESPACE}/providers",
        "/openapi.json",
        "/docs",
        "/redoc",
    ):
        response = api_client.get(path, headers=api_headers, follow_redirects=False)
        assert response.status_code == 404
        assert response.json()["code"] == "ROUTE_UNAVAILABLE"
