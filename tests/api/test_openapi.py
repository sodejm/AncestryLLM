"""Tests for the control API OpenAPI contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from ancestryllm.api import API_NAMESPACE
from ancestryllm.api.openapi import OPENAPI_ARTIFACT, canonical_openapi, contract_app

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from pytest import MonkeyPatch


def test_runtime_openapi_version_is_explicitly_pinned_to_3_1_0(
    monkeypatch: MonkeyPatch,
) -> None:
    original_init = FastAPI.__init__

    def initialize_with_future_default(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        if "openapi_version" not in kwargs:
            kwargs["openapi_version"] = "9.9.9"
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(FastAPI, "__init__", initialize_with_future_default)

    assert contract_app().openapi_version == "3.1.0"


def test_committed_openapi_artifact_matches_authoritative_models_exactly() -> None:
    committed = OPENAPI_ARTIFACT.read_text(encoding="utf-8")
    assert committed == canonical_openapi(contract_app())
    assert committed.endswith("\n")
    assert json.loads(committed)["paths"].keys() == {
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
        f"{API_NAMESPACE}/settings",
        f"{API_NAMESPACE}/secrets/{{reference}}/delete",
        f"{API_NAMESPACE}/secrets/{{reference}}/set",
        f"{API_NAMESPACE}/secrets/{{reference}}/status",
        f"{API_NAMESPACE}/startup-diagnostics",
    }
    schemas = json.loads(committed)["components"]["schemas"]
    assert {
        "ChatCapability",
        "ChatRunRequest",
        "ChatRunSummary",
        "ChatSession",
        "ChatSessionCreateRequest",
        "PageMetadata",
        "PaginationRequest",
    } <= schemas.keys()


def test_runtime_schema_and_docs_are_not_exposed(
    api_client: TestClient, api_headers: dict[str, str]
) -> None:
    for path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
        response = api_client.get(path, headers=api_headers, follow_redirects=False)
        assert response.status_code == 404
        assert response.headers.get("location") is None


def test_api_package_does_not_import_terminal_adapters() -> None:
    api_root = Path(__file__).parents[2] / "src" / "ancestryllm" / "api"
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(api_root.glob("*.py")))
    assert "ancestryllm.cli" not in source
    assert "ancestryllm.console" not in source
    assert "ancestryllm.terminal" not in source
