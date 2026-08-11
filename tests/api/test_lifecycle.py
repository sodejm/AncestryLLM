"""Tests for the API adapter lifecycle hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from fastapi.testclient import TestClient

from ancestryllm.api import ApiLifecycle, ApiSettings, create_app
from ancestryllm.application.executor import CommandExecutor
from ancestryllm.application.secret_management import SecretManagementService
from ancestryllm.application.settings import SettingsService
from ancestryllm.core.config import AppConfig
from ancestryllm.core.secrets import MemorySecretStore

from .conftest import FixtureRegistry


class RecordingLifecycle(ApiLifecycle):
    def __init__(self) -> None:
        self.events: list[str] = []

    async def startup(self) -> None:
        self.events.append("startup")

    async def shutdown(self) -> None:
        self.events.append("shutdown")


def test_adapter_exposes_supervisor_lifecycle_hooks(tmp_path: Path) -> None:
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
        settings_service=SettingsService(
            AppConfig(config_path=tmp_path / "config.toml", data_dir=tmp_path / "data")
        ),
        secret_service=SecretManagementService(MemorySecretStore({})),
        lifecycle=lifecycle,
    )

    with TestClient(app, base_url="http://127.0.0.1:8421"):
        assert lifecycle.events == ["startup"]
    assert lifecycle.events == ["startup", "shutdown"]
