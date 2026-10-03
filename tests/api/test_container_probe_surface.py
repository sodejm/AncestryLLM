"""Contracts for the container-safe probe-only API surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from ancestryllm.api import API_NAMESPACE, ApiSettings, create_app, create_uvicorn_config
from ancestryllm.application.executor import CommandExecutor
from ancestryllm.application.secret_management import SecretManagementService
from ancestryllm.application.settings import SettingsService
from ancestryllm.core.config import AppConfig
from ancestryllm.core.secrets import MemorySecretStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ancestryllm.core.commands import ModuleDescriptor


class _EmptyRegistry:
    def descriptors(self) -> Sequence[ModuleDescriptor]:
        return ()


def _probe_client(tmp_path: pytest.TempPathFactory, settings: ApiSettings) -> TestClient:
    config_root = tmp_path.mktemp("container-probe")
    app = create_app(
        settings=settings,
        registry=_EmptyRegistry(),
        executor=CommandExecutor(()),
        settings_service=SettingsService(
            AppConfig(config_path=config_root / "config.toml", data_dir=config_root / "data")
        ),
        secret_service=SecretManagementService(MemorySecretStore({})),
        surface="probe",
    )
    return TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False)


def test_probe_surface_registers_only_health_and_capabilities(
    api_settings: ApiSettings,
    api_headers: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    with _probe_client(tmp_path_factory, api_settings) as client:
        assert client.get(f"{API_NAMESPACE}/health", headers=api_headers).status_code == 200
        capabilities = client.get(f"{API_NAMESPACE}/capabilities", headers=api_headers)
        assert capabilities.status_code == 200
        assert capabilities.json()["modules"] == []

        for path in (
            f"{API_NAMESPACE}/startup-diagnostics",
            f"{API_NAMESPACE}/settings",
            f"{API_NAMESPACE}/provider-configuration",
            f"{API_NAMESPACE}/secrets/fictional/status",
        ):
            response = client.get(path, headers=api_headers)
            assert response.status_code == 404
            assert response.json()["code"] == "ROUTE_UNAVAILABLE"

        assert set(client.app.openapi()["paths"]) == {
            f"{API_NAMESPACE}/health",
            f"{API_NAMESPACE}/capabilities",
        }


def test_unknown_api_surface_fails_closed(
    api_settings: ApiSettings, tmp_path_factory: pytest.TempPathFactory
) -> None:
    config_root = tmp_path_factory.mktemp("invalid-surface")
    with pytest.raises(ValueError, match="API_SURFACE_INVALID"):
        create_app(
            settings=api_settings,
            registry=_EmptyRegistry(),
            executor=CommandExecutor(()),
            settings_service=SettingsService(
                AppConfig(config_path=config_root / "config.toml", data_dir=config_root / "data")
            ),
            secret_service=SecretManagementService(MemorySecretStore({})),
            surface="unexpected",  # type: ignore[arg-type]
        )


def test_container_server_configuration_remains_loopback_and_uses_shutdown_budget() -> None:
    config = create_uvicorn_config(
        object(),  # type: ignore[arg-type]
        port=8000,
        graceful_shutdown_seconds=20,
    )
    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.timeout_graceful_shutdown == 20
    assert config.access_log is False
    assert config.proxy_headers is False
