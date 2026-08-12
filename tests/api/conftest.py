"""Fictional fixtures for the internal control-plane API."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from ancestryllm.api import (
    API_BUILD_HEADER,
    API_CONTRACT,
    API_VERSION_HEADER,
    ApiSettings,
    create_app,
)
from ancestryllm.application.executor import CommandExecutor, CommandInvocation, CommandOutcome
from ancestryllm.application.results import StructuredResult
from ancestryllm.application.secret_management import SecretManagementService
from ancestryllm.application.settings import SettingsService
from ancestryllm.core.commands import BUILTIN_MODULES, DispatchKey, ModuleDescriptor
from ancestryllm.core.config import AppConfig
from ancestryllm.core.secrets import MemorySecretStore
from ancestryllm.llm.endpoint_validation import (
    EndpointProbeRequest,
    EndpointProbeResponse,
    EndpointValidationService,
)
from ancestryllm.llm.profiles import ProviderProfileService
from ancestryllm.llm.provider_configuration import ProviderConfigurationService
from ancestryllm.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


class FixtureRegistry:
    def __init__(self, descriptors: Sequence[ModuleDescriptor]) -> None:
        self._descriptors = tuple(descriptors)

    def descriptors(self) -> list[ModuleDescriptor]:
        return list(self._descriptors)


def _complete(invocation: CommandInvocation) -> CommandOutcome:
    return CommandOutcome(StructuredResult({"dispatch_key": invocation.key.value}))


@pytest.fixture
def api_settings() -> ApiSettings:
    return ApiSettings(
        bearer_token="A" * 43,
        app_build="0.5.0-test",
        sidecar_build="0.5.0-test",
        provider_id="none",
    )


@pytest.fixture
def registered_keys() -> tuple[DispatchKey, ...]:
    return tuple(
        BUILTIN_MODULES[module_id].command.routes[0].key
        for module_id in ("gedcom", "providers", "ocr")
    )


@pytest.fixture
def secret_store() -> MemorySecretStore:
    return MemorySecretStore({})


@pytest.fixture
def api_client(
    api_settings: ApiSettings,
    registered_keys: tuple[DispatchKey, ...],
    secret_store: MemorySecretStore,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[TestClient]:
    registry = FixtureRegistry(
        tuple(BUILTIN_MODULES[module_id] for module_id in ("gedcom", "providers", "secrets"))
    )
    executor = CommandExecutor((key, _complete) for key in registered_keys)
    config_root = tmp_path_factory.mktemp("api-config")
    database = Database(config_root / "workspace.db", secret_store)
    endpoint_validator = EndpointValidationService(
        resolver=lambda hostname, _port: (
            ("127.0.0.1",) if hostname in {"127.0.0.1", "localhost"} else ("8.8.8.8",)
        ),
        probe=lambda request: _successful_probe(request),
    )
    provider_profiles = ProviderProfileService(
        database,
        endpoint_validator=endpoint_validator,
    )
    app = create_app(
        settings=api_settings,
        registry=registry,
        executor=executor,
        settings_service=SettingsService(
            AppConfig(
                config_path=config_root / "config.toml",
                data_dir=config_root / "data",
            )
        ),
        secret_service=SecretManagementService(secret_store),
        provider_configuration_service=ProviderConfigurationService(
            provider_profiles,
            endpoint_validator,
        ),
        endpoint_validation_service=endpoint_validator,
    )
    with TestClient(app, base_url="http://127.0.0.1:8421", raise_server_exceptions=False) as client:
        yield client
    database.close()


def _successful_probe(request: EndpointProbeRequest) -> EndpointProbeResponse:
    return EndpointProbeResponse(status_code=200, peer_address=request.pinned_address)


@pytest.fixture
def api_headers(api_settings: ApiSettings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_settings.bearer_token}",
        API_VERSION_HEADER: API_CONTRACT,
        API_BUILD_HEADER: api_settings.app_build,
    }
