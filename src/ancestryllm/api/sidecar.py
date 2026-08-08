"""Packaged private control-sidecar bootstrap.

The Electron main process supplies the launch secret through one bounded stdin
frame.  No launch secret is accepted through command-line arguments or the
environment, and the public readiness frame deliberately contains no secret.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from dataclasses import dataclass, field
from typing import BinaryIO, NoReturn, Sequence

from fastapi import FastAPI
from uvicorn import Server

from ancestryllm.api.app import create_app
from ancestryllm.api.contracts import API_CONTRACT
from ancestryllm.api.server import LOOPBACK_HOST, create_uvicorn_config
from ancestryllm.api.settings import ApiSettings
from ancestryllm.application.executor import CommandExecutor
from ancestryllm.core.commands import ModuleDescriptor

SIDECAR_BUILD = "0.5.0"
MAX_LAUNCH_FRAME_BYTES = 4096
STARTUP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class LaunchFrame:
    """One private, exact-build launch request from Electron main."""

    contract: str
    app_build: str
    bearer_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.contract != API_CONTRACT:
            raise ValueError("unsupported sidecar contract")
        if self.app_build != SIDECAR_BUILD:
            raise ValueError("sidecar build does not match the application build")
        # Reuse the API boundary's validation rather than creating a second
        # token/build/host policy here.
        self.settings()

    def settings(self) -> ApiSettings:
        return ApiSettings(
            bearer_token=self.bearer_token,
            app_build=self.app_build,
            sidecar_build=SIDECAR_BUILD,
            provider_id="none",
        )


class _EmptyRegistry:
    """Expose no domain modules until separately owned routes are implemented."""

    def descriptors(self) -> Sequence[ModuleDescriptor]:
        return ()


def parse_launch_frame(stream: BinaryIO) -> LaunchFrame:
    """Parse exactly one newline-terminated, bounded JSON frame and then EOF."""

    raw = stream.read(MAX_LAUNCH_FRAME_BYTES + 1)
    if not raw or len(raw) > MAX_LAUNCH_FRAME_BYTES or not raw.endswith(b"\n"):
        raise ValueError("invalid sidecar launch frame")
    if stream.read(1) != b"":
        raise ValueError("unexpected data after sidecar launch frame")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid sidecar launch frame") from error
    if not isinstance(document, dict) or set(document) != {
        "contract",
        "app_build",
        "bearer_token",
    }:
        raise ValueError("invalid sidecar launch frame fields")
    if not all(isinstance(value, str) for value in document.values()):
        raise ValueError("invalid sidecar launch frame values")
    return LaunchFrame(
        contract=document["contract"],
        app_build=document["app_build"],
        bearer_token=document["bearer_token"],
    )


def create_listener() -> socket.socket:
    """Pre-bind one IPv4 loopback listener on an OS-selected port."""

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.set_inheritable(False)
        listener.bind((LOOPBACK_HOST, 0))
        listener.listen()
    except OSError:
        listener.close()
        raise
    return listener


def readiness_line(frame: LaunchFrame, port: int) -> str:
    """Render the intentionally public portion of the startup handshake."""

    return json.dumps(
        {"contract": frame.contract, "sidecar_build": SIDECAR_BUILD, "port": port},
        separators=(",", ":"),
        sort_keys=True,
    )


def create_sidecar_app(frame: LaunchFrame) -> FastAPI:
    """Compose the packaged control sidecar without any domain modules or routes."""

    return create_app(
        settings=frame.settings(),
        registry=_EmptyRegistry(),
        executor=CommandExecutor(()),
    )


async def _serve(frame: LaunchFrame) -> int:
    listener = create_listener()
    server = Server(create_uvicorn_config(create_sidecar_app(frame)))
    serve_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
            while not server.started:
                if serve_task.done():
                    await serve_task
                    raise RuntimeError("sidecar stopped before readiness")
                await asyncio.sleep(0.01)
        sys.stdout.write(readiness_line(frame, listener.getsockname()[1]) + "\n")
        sys.stdout.flush()
        await serve_task
        return 0
    finally:
        if not serve_task.done():
            server.should_exit = True
            await serve_task
        listener.close()


def _fail() -> NoReturn:
    # Keep diagnostics structural: configuration details and secrets never
    # enter stderr, command arguments, or logs.
    sys.stderr.write("SIDECAR_START_FAILED\n")
    raise SystemExit(1)


def main() -> int:
    """Start the packaged sidecar from its private standard-input frame."""

    try:
        frame = parse_launch_frame(sys.stdin.buffer)
        return asyncio.run(_serve(frame))
    except (ValueError, OSError, RuntimeError, TimeoutError):
        _fail()


if __name__ == "__main__":  # pragma: no cover - exercised by packaged smoke tests
    raise SystemExit(main())


__all__ = [
    "MAX_LAUNCH_FRAME_BYTES",
    "SIDECAR_BUILD",
    "LaunchFrame",
    "create_listener",
    "create_sidecar_app",
    "main",
    "parse_launch_frame",
    "readiness_line",
]
