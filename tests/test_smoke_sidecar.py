"""Regression tests for the packaged-sidecar smoke harness."""

from __future__ import annotations

from threading import Event
from time import monotonic

import pytest
from scripts.smoke_sidecar import _readline_with_timeout


class _BlockingStream:
    def __init__(self, release: Event) -> None:
        self._release = release

    def readline(self) -> bytes:
        self._release.wait()
        return b""


def test_readline_timeout_does_not_wait_for_blocked_stream() -> None:
    release = Event()
    started = monotonic()
    try:
        with pytest.raises(TimeoutError):
            _readline_with_timeout(_BlockingStream(release), timeout_seconds=0.01)
        assert monotonic() - started < 0.5
    finally:
        release.set()
