"""Tests for the control API OpenAPI contract."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ancestryllm.api import API_NAMESPACE
from ancestryllm.api.openapi import OPENAPI_ARTIFACT, canonical_openapi, contract_app


def test_committed_openapi_artifact_matches_authoritative_models_exactly() -> None:
    committed = OPENAPI_ARTIFACT.read_text(encoding="utf-8")
    assert committed == canonical_openapi(contract_app())
    assert committed.endswith("\n")
    assert json.loads(committed)["paths"].keys() == {
        f"{API_NAMESPACE}/capabilities",
        f"{API_NAMESPACE}/health",
    }
    schemas = json.loads(committed)["components"]["schemas"]
    assert {"PaginationRequest", "PageMetadata"} <= schemas.keys()


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
