"""Fail-closed Uvicorn configuration for the future Electron supervisor."""

from __future__ import annotations

from starlette.types import ASGIApp
from uvicorn import Config

LOOPBACK_HOST = "127.0.0.1"
EPHEMERAL_PORT = 0
GRACEFUL_SHUTDOWN_SECONDS = 10


def create_uvicorn_config(app: ASGIApp) -> Config:
    """Create a listener config that cannot be redirected to a public interface."""

    return Config(
        app=app,
        host=LOOPBACK_HOST,
        port=EPHEMERAL_PORT,
        access_log=False,
        date_header=False,
        forwarded_allow_ips="",
        interface="asgi3",
        lifespan="on",
        log_config=None,
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )


__all__ = ["EPHEMERAL_PORT", "GRACEFUL_SHUTDOWN_SECONDS", "LOOPBACK_HOST", "create_uvicorn_config"]
