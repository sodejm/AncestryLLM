"""Tests for the API adapter lifecycle hooks."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ancestryllm.api import ApiLifecycle, ApiSettings, create_app
from ancestryllm.application.executor import CommandExecutor

from .conftest import FixtureRegistry


class RecordingLifecycle(ApiLifecycle):
    def __init__(self) -> None:
        self.events: list[str] = []

    async def startup(self) -> None:
        self.events.append("startup")

    async def shutdown(self) -> None:
        self.events.append("shutdown")


def test_adapter_exposes_supervisor_lifecycle_hooks() -> None:
    lifecycle = RecordingLifecycle()
    app = create_app(
        settings=ApiSettings(
            bearer_token="A" * 43,
            app_build="0.5.0-test",
            sidecar_build="0.5.0-test",
            provider_id="none",
        ),
        registry=FixtureRegistry(()),
        executor=CommandExecutor(()),
        lifecycle=lifecycle,
    )

    with TestClient(app, base_url="http://127.0.0.1:8421"):
        assert lifecycle.events == ["startup"]
    assert lifecycle.events == ["startup", "shutdown"]
