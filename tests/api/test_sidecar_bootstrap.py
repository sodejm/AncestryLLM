"""Tests for sidecar bootstrap readiness and loopback safety."""

from __future__ import annotations

import io
import json
import socket

import pytest
from fastapi.routing import APIRoute

from ancestryllm.api.contracts import API_CONTRACT
from ancestryllm.api.sidecar import (
    SIDECAR_BUILD,
    LaunchFrame,
    create_listener,
    create_sidecar_app,
    parse_launch_frame,
    readiness_line,
)


def _launch_payload(**updates: str) -> bytes:
    payload = {
        "contract": API_CONTRACT,
        "app_build": SIDECAR_BUILD,
        "bearer_token": "A" * 43,
    }
    payload.update(updates)
    return (json.dumps(payload) + "\n").encode()


def test_private_stdin_frame_is_strict_bounded_and_provider_none() -> None:
    frame = parse_launch_frame(io.BytesIO(_launch_payload()))

    assert frame == LaunchFrame(
        contract=API_CONTRACT,
        app_build=SIDECAR_BUILD,
        bearer_token="A" * 43,
    )
    assert frame.settings().provider_id == "none"
    assert "bearer_token" not in repr(frame)


@pytest.mark.parametrize(
    "payload",
    (
        _launch_payload(contract="ancestryllm.internal-api/2"),
        _launch_payload(app_build="different-build"),
        _launch_payload() + b"unexpected",
        b"{}\n",
        b"x" * 4097,
    ),
)
def test_private_stdin_frame_fails_closed(payload: bytes) -> None:
    with pytest.raises(ValueError):
        parse_launch_frame(io.BytesIO(payload))


def test_listener_is_os_selected_ipv4_loopback_only() -> None:
    listener = create_listener()
    try:
        host, port = listener.getsockname()
        assert host == "127.0.0.1"
        assert 0 < port < 65536
        assert listener.family == socket.AF_INET
    finally:
        listener.close()


def test_readiness_line_contains_only_public_handshake_metadata() -> None:
    frame = LaunchFrame(
        contract=API_CONTRACT,
        app_build=SIDECAR_BUILD,
        bearer_token="secret-value-that-must-never-appear-000000000",
    )

    rendered = readiness_line(frame, 49152)
    assert json.loads(rendered) == {
        "contract": API_CONTRACT,
        "sidecar_build": SIDECAR_BUILD,
        "port": 49152,
    }
    assert frame.bearer_token not in rendered


def test_packaged_sidecar_adds_no_domain_routes() -> None:
    app = create_sidecar_app(
        LaunchFrame(
            contract=API_CONTRACT,
            app_build=SIDECAR_BUILD,
            bearer_token="A" * 43,
        )
    )

    assert {route.path for route in app.routes if isinstance(route, APIRoute)} == {
        "/api/v1/capabilities",
        "/api/v1/health",
    }
