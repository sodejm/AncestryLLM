"""Regression tests for the packaged-sidecar smoke harness."""

from __future__ import annotations

import json
from threading import Event
from time import monotonic

import pytest
from scripts.smoke_sidecar import _build_launch_frame, _readline_with_timeout

from ancestryllm.api.contracts import API_CONTRACT
from ancestryllm.api.sidecar import SIDECAR_BUILD


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
