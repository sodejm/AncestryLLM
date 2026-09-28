"""Regression tests for the packaged-sidecar smoke harness."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest
from scripts import smoke_sidecar
from scripts.smoke_sidecar import _build_launch_frame, _readline_with_timeout

from ancestryllm.api.contracts import API_CONTRACT
from ancestryllm.api.sidecar import SIDECAR_BUILD

if TYPE_CHECKING:
    from pathlib import Path
    from urllib.request import Request


class _BlockingStream:
    def __init__(self, release: Event) -> None:
        self._release = release

    def readline(self) -> bytes:
        self._release.wait()
        return b""


def test_launch_frame_matches_the_private_sidecar_contract() -> None:
    frame = json.loads(
        _build_launch_frame(
            "T" * 43,
            "123e4567-e89b-42d3-a456-426614174000",
            "/fictional/app-data/diagnostics",
        )
    )

    assert frame == {
        "contract": API_CONTRACT,
        "app_build": SIDECAR_BUILD,
        "bearer_token": "T" * 43,
        "diagnostic_run_id": "123e4567-e89b-42d3-a456-426614174000",
        "diagnostic_directory": "/fictional/app-data/diagnostics",
    }


def test_readline_timeout_does_not_wait_for_blocked_stream() -> None:
    release = Event()
    started = monotonic()
    try:
        with pytest.raises(TimeoutError):
            _readline_with_timeout(_BlockingStream(release), timeout_seconds=0.01)
        assert monotonic() - started < 0.5
    finally:
        release.set()


class _InputPipe(io.BytesIO):
    def close(self) -> None:
        """Keep the launch frame available to the fake process."""


class _SmokeProcess:
    def __init__(self) -> None:
        self.stdin = _InputPipe()
        self.stdout = io.BytesIO(
            json.dumps(
                {"contract": API_CONTRACT, "sidecar_build": SIDECAR_BUILD, "port": 12345}
            ).encode()
        )
        self.terminated = False

    def poll(self) -> int | None:
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> int:
        return 0


class _JsonResponse(io.BytesIO):
    status = 200

    def __init__(self, value: object) -> None:
        super().__init__(json.dumps(value).encode())


def test_smoke_rejects_packaged_inspection_failure_without_private_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _SmokeProcess()

    def launch(*args: object, **kwargs: object) -> _SmokeProcess:
        stderr = kwargs["stderr"]
        if not hasattr(stderr, "write"):
            stderr = io.BytesIO()
        stderr.write(
            b"private SQL and /private/fictional/payload.rmtree\n"
            b"ROOTSMAGIC_SCHEMA_PARSE_FAILED: ModuleNotFoundError module=sqlglot.generators.sqlite\n"
            b"ROOTSMAGIC_SCHEMA_PARSE_FAILED: /private/fictional/payload.rmtree\n"
            b"ROOTSMAGIC_SCHEMA_VALIDATION_FAILED: stage=people_columns error_type=none\n"
            b"ROOTSMAGIC_SCHEMA_VALIDATION_FAILED: stage=/private/fictional error_type=none\n"
        )
        stderr.flush()
        return process

    monkeypatch.setattr(smoke_sidecar.subprocess, "Popen", launch)
    requests: list[str] = []

    class Opener:
        def open(self, request: Request, timeout: float) -> _JsonResponse:
            if request.data is None:
                assert request.get_header("Content-type") is None
            else:
                assert request.get_header("Content-type") == "application/json"
            path = urlsplit(request.full_url).path
            requests.append(path)
            authorization = request.get_header("Authorization")
            assert authorization is not None
            token = authorization.removeprefix("Bearer ")
            if path.endswith("/health"):
                proof = hmac.new(
                    token.encode(),
                    f"{API_CONTRACT}\n{SIDECAR_BUILD}\n{SIDECAR_BUILD}".encode(),
                    hashlib.sha256,
                ).hexdigest()
                return _JsonResponse({"status": "ready", "readiness_proof": proof})
            if path.endswith("/capabilities"):
                return _JsonResponse({"modules": []})
            if path.endswith("/sources"):
                return _JsonResponse({"job_id": "inspection-job"})
            if path.endswith("/jobs/inspection-job"):
                return _JsonResponse(
                    {
                        "state": "failed",
                        "error_code": "ROOTSMAGIC_SCHEMA_UNSUPPORTED",
                        "error_message": "/private/fictional/payload.rmtree: private SQL",
                    }
                )
            raise AssertionError(path)

    monkeypatch.setattr(smoke_sidecar.urllib.request, "build_opener", lambda *args: Opener())

    with pytest.raises(RuntimeError, match="ROOTSMAGIC_SCHEMA_UNSUPPORTED") as caught:
        smoke_sidecar.smoke(tmp_path / "fictional-sidecar")

    assert "/private/fictional" not in str(caught.value)
    assert "private SQL" not in str(caught.value)
    assert "ModuleNotFoundError module=sqlglot.generators.sqlite" in str(caught.value)
    assert "stage=people_columns error_type=none" in str(caught.value)
    assert "/api/v1/rootsmagic/sources" in requests
    assert process.terminated
