"""Probe-only container entrypoint reserved for the later authenticated gateway."""

from __future__ import annotations

import secrets
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from uvicorn import Server

from ancestryllm import __version__
from ancestryllm.api import ApiSettings, create_app, create_uvicorn_config
from ancestryllm.application.executor import CommandExecutor
from ancestryllm.application.secret_management import SecretManagementService
from ancestryllm.application.settings import SettingsService
from ancestryllm.container_runtime import (
    PROBE_PORT,
    PROBE_TOKEN_PATH,
    ContainerRuntimeError,
    publish_private_runtime_file,
    remove_private_runtime_file,
)
from ancestryllm.core.config import AppConfig
from ancestryllm.core.secrets import MemorySecretStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI

    from ancestryllm.core.commands import ModuleDescriptor

SHUTDOWN_BUDGET_SECONDS = 20


class _EmptyRegistry:
    def descriptors(self) -> Sequence[ModuleDescriptor]:
        return ()


def build_probe_app(token: str, *, build: str = __version__) -> FastAPI:
    """Build only health and capability discovery; application routes stay unavailable."""

    config = AppConfig(
        config_path=Path("/var/lib/ancestryllm/config.toml"),
        data_dir=Path("/var/lib/ancestryllm"),
        enabled_modules=set(),
        default_provider="none",
    )
    return create_app(
        settings=ApiSettings(
            bearer_token=token,
            app_build=build,
            sidecar_build=build,
            provider_id="none",
        ),
        registry=_EmptyRegistry(),
        executor=CommandExecutor(()),
        settings_service=SettingsService(config),
        secret_service=SecretManagementService(MemorySecretStore({})),
        mutations_allowed=lambda: False,
        surface="probe",
    )


def _acknowledge_processed_signal(_signum: int, _frame: object) -> None:
    """Let Uvicorn re-raise an already processed stop signal without exiting 128+signal."""


def run() -> None:
    """Publish an ephemeral health bearer, serve loopback probes, and shut down cleanly."""

    token = secrets.token_urlsafe(48)
    publish_private_runtime_file(PROBE_TOKEN_PATH, token)
    try:
        config = create_uvicorn_config(
            build_probe_app(token),
            port=PROBE_PORT,
            graceful_shutdown_seconds=SHUTDOWN_BUDGET_SECONDS,
        )
        previous_handlers = {
            handled_signal: signal.signal(handled_signal, _acknowledge_processed_signal)
            for handled_signal in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            Server(config).run()
        finally:
            for handled_signal, previous_handler in previous_handlers.items():
                signal.signal(handled_signal, previous_handler)
    finally:
        remove_private_runtime_file(PROBE_TOKEN_PATH)


def main() -> int:
    """Run the container gateway command and return its exit status."""
    try:
        run()
    except ContainerRuntimeError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by container lifecycle tests
    raise SystemExit(main())


__all__ = [
    "PROBE_PORT",
    "PROBE_TOKEN_PATH",
    "SHUTDOWN_BUDGET_SECONDS",
    "build_probe_app",
    "main",
    "run",
]
