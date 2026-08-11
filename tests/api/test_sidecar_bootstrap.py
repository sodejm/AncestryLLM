"""Tests for sidecar bootstrap readiness and loopback safety."""

from __future__ import annotations

import io
import json
import socket
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest
from fastapi.routing import APIRoute

from ancestryllm.api.contracts import API_CONTRACT
from ancestryllm.api.sidecar import (
    SIDECAR_BUILD,
    LaunchFrame,
    acquire_windows_process_tree_guard,
    create_listener,
    create_sidecar_app,
    parse_launch_frame,
    readiness_line,
)

if TYPE_CHECKING:
    from pathlib import Path


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


def test_windows_process_tree_guard_is_retained_and_other_platforms_are_noops() -> None:
    handle = object()

    def create_native() -> object:
        return handle

    assert acquire_windows_process_tree_guard("win32", create_native) is handle
    assert acquire_windows_process_tree_guard("linux", create_native) is None


def test_windows_process_tree_guard_fails_closed_without_native_error_details() -> None:
    def fail() -> object:
        raise OSError(5, "sensitive native setup detail")

    with pytest.raises(RuntimeError, match="Windows process-tree guard unavailable") as error:
        acquire_windows_process_tree_guard("win32", fail)

    assert "sensitive" not in str(error.value)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires the native Windows Job Object implementation",
)
def test_windows_process_tree_guard_kills_descendant_when_owner_exits(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "orphaned-descendant"
    descendant = (
        "import pathlib, sys, time; "
        "time.sleep(2); "
        "pathlib.Path(sys.argv[1]).write_text('orphaned', encoding='utf-8')"
    )
    owner = "\n".join(
        (
            "import subprocess, sys",
            "from ancestryllm.api.sidecar import acquire_windows_process_tree_guard",
            "_guard = acquire_windows_process_tree_guard()",
            "subprocess.Popen(",
            "    [sys.executable, '-c', sys.argv[1], sys.argv[2]],",
            "    stdin=subprocess.DEVNULL,",
            "    stdout=subprocess.DEVNULL,",
            "    stderr=subprocess.DEVNULL,",
            ")",
        )
    )

    subprocess.run(
        [sys.executable, "-c", owner, descendant, str(marker)],
        check=True,
        timeout=10,
    )
    time.sleep(3)

    assert not marker.exists()
