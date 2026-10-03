"""Fail-closed Uvicorn configuration for the future Electron supervisor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uvicorn import Config

if TYPE_CHECKING:
    from starlette.types import ASGIApp

LOOPBACK_HOST = "127.0.0.1"
EPHEMERAL_PORT = 0
GRACEFUL_SHUTDOWN_SECONDS = 10


def create_uvicorn_config(
    app: ASGIApp,
    *,
    port: int = EPHEMERAL_PORT,
    graceful_shutdown_seconds: int = GRACEFUL_SHUTDOWN_SECONDS,
) -> Config:
    """Create a listener config that cannot be redirected to a public interface."""

    if not 0 <= port <= 65_535:
        raise ValueError("API_PORT_INVALID: the listener port must be between 0 and 65535")
    if graceful_shutdown_seconds <= 0:
        raise ValueError("API_SHUTDOWN_BUDGET_INVALID: the shutdown budget must be positive")

    return Config(
        app=app,
        host=LOOPBACK_HOST,
        port=port,
        access_log=False,
        date_header=False,
        forwarded_allow_ips="",
        interface="asgi3",
        lifespan="on",
        log_config=None,
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=graceful_shutdown_seconds,
    )


__all__ = ["EPHEMERAL_PORT", "GRACEFUL_SHUTDOWN_SECONDS", "LOOPBACK_HOST", "create_uvicorn_config"]
