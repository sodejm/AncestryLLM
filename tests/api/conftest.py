"""Fictional fixtures for the internal control-plane API."""

from __future__ import annotations

from collections.abc import Sequence

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
from ancestryllm.core.commands import BUILTIN_MODULES, DispatchKey, ModuleDescriptor


class FixtureRegistry:
    def __init__(self, descriptors: Sequence[ModuleDescriptor]) -> None:
        self._descriptors = tuple(descriptors)

    def descriptors(self) -> list[ModuleDescriptor]:
        return list(self._descriptors)


def _complete(invocation: CommandInvocation) -> CommandOutcome:
    return CommandOutcome(value={"dispatch_key": invocation.key.value})


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
def api_client(api_settings: ApiSettings, registered_keys: tuple[DispatchKey, ...]) -> TestClient:
    registry = FixtureRegistry(
        tuple(BUILTIN_MODULES[module_id] for module_id in ("gedcom", "providers", "secrets"))
    )
    executor = CommandExecutor((key, _complete) for key in registered_keys)
    app = create_app(settings=api_settings, registry=registry, executor=executor)
    return TestClient(app, base_url="http://127.0.0.1:8421", raise_server_exceptions=False)


@pytest.fixture
def api_headers(api_settings: ApiSettings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_settings.bearer_token}",
        API_VERSION_HEADER: API_CONTRACT,
        API_BUILD_HEADER: api_settings.app_build,
    }
