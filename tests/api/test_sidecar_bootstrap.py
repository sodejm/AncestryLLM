"""Tests for sidecar bootstrap readiness and loopback safety."""

from __future__ import annotations

import io
import json
import socket
import subprocess
import sys
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import ancestryllm.api.sidecar as sidecar_module
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
from ancestryllm.core.config import AppConfig
from ancestryllm.core.errors import ConfigurationError
from ancestryllm.core.secrets import MemorySecretStore, SecretSourceMode

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


def test_packaged_sidecar_exposes_only_bounded_control_routes() -> None:
    app = create_sidecar_app(
        LaunchFrame(
            contract=API_CONTRACT,
            app_build=SIDECAR_BUILD,
            bearer_token="A" * 43,
        )
    )

    assert {route.path for route in app.routes if isinstance(route, APIRoute)} == {
        "/api/v1/capabilities",
        "/api/v1/chat/capability",
        "/api/v1/chat/sessions",
        "/api/v1/chat/sessions/{session_id}",
        "/api/v1/chat/sessions/{session_id}/runs",
        "/api/v1/chat/sessions/{session_id}/streams",
        "/api/v1/chat/sessions/{session_id}/streams/{run_id}/cancel",
        "/api/v1/chat/sessions/{session_id}/streams/{run_id}/events",
        "/api/v1/consents",
        "/api/v1/consents/preview",
        "/api/v1/consents/{name}/revoke",
        "/api/v1/health",
        "/api/v1/jobs",
        "/api/v1/jobs/shutdown",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/jobs/{job_id}/events",
        "/api/v1/provider-configuration",
        "/api/v1/provider-endpoints/validate",
        "/api/v1/provider-profiles",
        "/api/v1/secrets/{reference}/delete",
        "/api/v1/secrets/{reference}/set",
        "/api/v1/secrets/{reference}/status",
        "/api/v1/startup-diagnostics",
        "/api/v1/settings",
    }


def test_packaged_sidecar_runtime_shutdown_is_authenticated_bodyless_and_private(
    tmp_path: Path,
) -> None:
    frame = LaunchFrame(
        contract=API_CONTRACT,
        app_build=SIDECAR_BUILD,
        bearer_token="A" * 43,
    )
    request_runtime_shutdown = Mock()
    app = create_sidecar_app(
        frame,
        config=AppConfig(config_path=tmp_path / "config.toml", data_dir=tmp_path),
        secret_store=MemorySecretStore({}),
        request_runtime_shutdown=request_runtime_shutdown,
    )
    headers = {
        "Authorization": f"Bearer {frame.bearer_token}",
        "X-Ancestry-API-Version": API_CONTRACT,
        "X-Ancestry-App-Build": frame.app_build,
    }

    with TestClient(app, base_url="http://127.0.0.1:8421") as client:
        unauthenticated = client.post("/api/v1/runtime/shutdown")
        with_body = client.post(
            "/api/v1/runtime/shutdown",
            headers=headers,
            json={},
        )
        accepted = client.post("/api/v1/runtime/shutdown", headers=headers)

    assert unauthenticated.status_code == 401
    assert with_body.status_code == 415
    assert with_body.json()["code"] == "REQUEST_CONTENT_TYPE_FORBIDDEN"
    assert accepted.status_code == 204
    assert accepted.content == b""
    assert "/api/v1/runtime/shutdown" not in app.openapi()["paths"]
    request_runtime_shutdown.assert_called_once_with()


def test_packaged_sidecar_runtime_shutdown_is_unavailable_without_callback(
    tmp_path: Path,
) -> None:
    frame = LaunchFrame(
        contract=API_CONTRACT,
        app_build=SIDECAR_BUILD,
        bearer_token="A" * 43,
    )
    app = create_sidecar_app(
        frame,
        config=AppConfig(config_path=tmp_path / "config.toml", data_dir=tmp_path),
        secret_store=MemorySecretStore({}),
    )
    headers = {
        "Authorization": f"Bearer {frame.bearer_token}",
        "X-Ancestry-API-Version": API_CONTRACT,
        "X-Ancestry-App-Build": frame.app_build,
    }

    with TestClient(app, base_url="http://127.0.0.1:8421") as client:
        response = client.post("/api/v1/runtime/shutdown", headers=headers)

    assert response.status_code == 404
    assert response.json()["code"] == "ROUTE_UNAVAILABLE"


def test_packaged_sidecar_composes_provider_configuration_services(tmp_path: Path) -> None:
    frame = LaunchFrame(
        contract=API_CONTRACT,
        app_build=SIDECAR_BUILD,
        bearer_token="A" * 43,
    )
    app = create_sidecar_app(
        frame,
        config=AppConfig(config_path=tmp_path / "config.toml", data_dir=tmp_path),
        secret_store=MemorySecretStore({}),
    )
    headers = {
        "Authorization": f"Bearer {frame.bearer_token}",
        "X-Ancestry-API-Version": API_CONTRACT,
        "X-Ancestry-App-Build": frame.app_build,
    }

    with TestClient(app, base_url="http://127.0.0.1:8421") as client:
        response = client.get("/api/v1/provider-configuration", headers=headers)
        jobs = client.get("/api/v1/jobs", headers=headers)
        chat = client.get("/api/v1/chat/capability", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "revision": "22d8b1f4f428f4f4b395f0d5079d85ecdc8219a66ac7ce2f9b3b3d1a20bdfdcf",
        "profiles": [],
        "consents": [],
    }
    assert jobs.status_code == 200
    assert jobs.json() == {"schema_version": 1, "jobs": []}
    assert chat.status_code == 200
    assert chat.json()["transient"] is True
    assert chat.json()["tools_enabled"] is False
    assert chat.json()["payload_retention"] is False


def test_packaged_sidecar_closes_chat_and_llm_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chat_service = Mock()
    streaming_service = Mock()
    streaming_service.startup = AsyncMock()
    streaming_service.shutdown = AsyncMock()
    llm_service = Mock()
    monkeypatch.setattr(sidecar_module, "LLMService", Mock(return_value=llm_service))
    monkeypatch.setattr(sidecar_module, "ChatService", Mock(return_value=chat_service))
    monkeypatch.setattr(
        sidecar_module,
        "ChatStreamingService",
        Mock(return_value=streaming_service),
    )
    frame = LaunchFrame(
        contract=API_CONTRACT,
        app_build=SIDECAR_BUILD,
        bearer_token="A" * 43,
    )
    app = create_sidecar_app(
        frame,
        config=AppConfig(config_path=tmp_path / "config.toml", data_dir=tmp_path),
        secret_store=MemorySecretStore({}),
    )

    with TestClient(app, base_url="http://127.0.0.1:8421"):
        chat_service.close.assert_not_called()
        llm_service.close.assert_not_called()

    chat_service.close.assert_called_once_with()
    llm_service.close.assert_called_once_with()
    streaming_service.startup.assert_awaited_once_with()
    streaming_service.shutdown.assert_awaited_once_with()


def test_sidecar_cleanup_continues_after_an_interruption(tmp_path: Path) -> None:
    database = Mock()
    chat_service = Mock()
    llm_service = Mock()
    chat_service.close.side_effect = KeyboardInterrupt("fictional cleanup interruption")
    lifecycle = sidecar_module._SidecarLifecycle(
        database=database,
        startup_diagnostics=Mock(),
        chat_service=chat_service,
        chat_streaming_service=Mock(),
        llm_service=llm_service,
    )

    with pytest.raises(KeyboardInterrupt, match="fictional cleanup interruption"):
        lifecycle._close_owned_resources()

    chat_service.close.assert_called_once_with()
    llm_service.close.assert_called_once_with()
    database.close.assert_called_once_with()


def test_packaged_sidecar_uses_keyring_only_secret_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selected_modes: list[SecretSourceMode] = []

    def secret_store_factory(*, source_mode: SecretSourceMode) -> MemorySecretStore:
        selected_modes.append(source_mode)
        return MemorySecretStore({})

    monkeypatch.setattr(sidecar_module, "KeyringSecretStore", secret_store_factory)
    create_sidecar_app(
        LaunchFrame(
            contract=API_CONTRACT,
            app_build=SIDECAR_BUILD,
            bearer_token="A" * 43,
        ),
        config=AppConfig(
            config_path=tmp_path / "config.toml",
            data_dir=tmp_path,
        ),
    )

    assert selected_modes == [SecretSourceMode.KEYRING_ONLY]


def test_corrupt_config_opens_sanitized_degraded_shell_and_blocks_mutations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_marker = "PRIVATE-CONFIG-PAYLOAD-MARKER"
    config_path = tmp_path / "config.toml"
    config_path.write_text(private_marker, encoding="utf-8")
    fallback = AppConfig(config_path=config_path, data_dir=tmp_path / "data")

    def fail_load(_cls: type[AppConfig]) -> AppConfig:
        raise ConfigurationError(
            "CONFIG_INVALID",
            "Configuration is not valid TOML.",
            "Correct the TOML syntax or restore a known-good configuration file.",
            details={"payload": private_marker},
        )

    monkeypatch.setattr(AppConfig, "load", classmethod(fail_load))
    monkeypatch.setattr(
        sidecar_module,
        "_packaged_fallback_config",
        lambda: fallback,
        raising=False,
    )
    frame = LaunchFrame(
        contract=API_CONTRACT,
        app_build=SIDECAR_BUILD,
        bearer_token="A" * 43,
    )
    app = create_sidecar_app(frame, secret_store=MemorySecretStore({}))
    headers = {
        "Authorization": f"Bearer {frame.bearer_token}",
        "X-Ancestry-API-Version": API_CONTRACT,
        "X-Ancestry-App-Build": frame.app_build,
    }

    with TestClient(app, base_url="http://127.0.0.1:8421", raise_server_exceptions=False) as client:
        diagnostics = client.get("/api/v1/startup-diagnostics", headers=headers)
        blocked = client.patch(
            "/api/v1/settings",
            headers=headers,
            json={
                "schema_version": 1,
                "expected_revision": 0,
                "changes": {"limits.max_query_rows": 250},
            },
        )
        blocked_chat = client.post(
            "/api/v1/chat/sessions",
            headers=headers,
            json={
                "schema_version": 1,
                "provider_profile_name": "fictional-local",
                "model": "fictional-model",
                "purpose": "genealogy_analysis",
                "data_classes": ["deceased_person"],
            },
        )
        jobs = client.get("/api/v1/jobs", headers=headers)
        shutdown = client.post(
            "/api/v1/jobs/shutdown",
            headers=headers,
            json={"schema_version": 1, "action": "wait", "timeout_seconds": 0},
        )

    assert diagnostics.status_code == 200
    payload = diagnostics.json()
    assert payload["schema_version"] == 1
    assert payload["status"] == "degraded"
    configuration = next(
        item for item in payload["components"] if item["component"] == "configuration"
    )
    assert configuration == {
        "component": "configuration",
        "status": "blocked",
        "code": "CONFIG_INVALID",
        "message": "The desktop configuration could not be validated.",
        "remediation": "Repair or restore config.toml, then retry startup diagnostics.",
        "restart_required": False,
        "blocks_mutations": True,
    }
    assert private_marker not in diagnostics.text
    assert str(tmp_path) not in diagnostics.text
    assert blocked.status_code == 503
    assert blocked.json()["code"] == "STARTUP_MUTATION_BLOCKED"
    assert blocked_chat.status_code == 503
    assert blocked_chat.json()["code"] == "STARTUP_MUTATION_BLOCKED"
    assert jobs.status_code == 503
    assert jobs.json()["code"] == "JOB_SERVICE_UNAVAILABLE"
    assert shutdown.status_code == 200
    assert shutdown.json() == {
        "schema_version": 1,
        "safe_to_quit": True,
        "active_jobs": [],
    }
    assert private_marker not in jobs.text
    assert str(tmp_path) not in jobs.text
    assert private_marker not in blocked.text
    assert private_marker not in blocked_chat.text
    assert config_path.read_text(encoding="utf-8") == private_marker
    assert not fallback.data_dir.exists()


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
