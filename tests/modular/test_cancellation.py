from __future__ import annotations

import logging
import threading
from datetime import datetime

import pytest

from ancestryllm.core.cancellation import (
    CancellationError,
    CancellationToken,
    bind_cancellation_token,
    cancellation_checkpoint,
    current_cancellation_token,
    interruptible_sleep,
    non_interruptible_section,
)


def test_cancellation_request_is_idempotent_and_notifies_once() -> None:
    token = CancellationToken()
    observed = []
    token.subscribe(observed.append)

    assert token.request() is True
    assert token.request() is False

    state = token.state
    assert len(observed) == 1
    assert observed == [state]
    assert state.requested_at is not None
    assert datetime.fromisoformat(state.requested_at).utcoffset() is not None
    assert state.pending is False
    assert state.deferred_by is None


def test_listener_failure_cannot_break_or_disclose_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = CancellationToken()

    def fail(_state) -> None:
        raise RuntimeError("private cancellation listener detail")

    token.subscribe(fail)
    with caplog.at_level(logging.WARNING):
        assert token.request() is True

    assert "Cancellation listener failed: RuntimeError" in caplog.text
    assert "private cancellation listener detail" not in caplog.text


def test_nested_non_interruptible_sections_report_innermost_operation() -> None:
    token = CancellationToken()
    with bind_cancellation_token(token):
        with pytest.raises(CancellationError):
            with non_interruptible_section("publishing fictional bundle"):
                with non_interruptible_section("rolling back fictional bundle"):
                    assert token.request() is True
                    assert token.state.pending is True
                    assert token.state.deferred_by == "rolling back fictional bundle"
                assert token.state.pending is True
                assert token.state.deferred_by == "publishing fictional bundle"


def test_request_before_protected_section_prevents_entry() -> None:
    token = CancellationToken()
    token.request()
    entered = False

    with bind_cancellation_token(token):
        with pytest.raises(CancellationError):
            with non_interruptible_section("publishing fictional bundle"):
                entered = True

    assert entered is False


def test_cancellation_is_raised_immediately_after_protected_section() -> None:
    token = CancellationToken()
    completed = False

    with bind_cancellation_token(token):
        with pytest.raises(CancellationError):
            with non_interruptible_section("publishing fictional bundle"):
                token.request()
                completed = True

    assert completed is True
    assert token.state.pending is False
    assert token.state.deferred_by is None


def test_context_binding_is_scoped_and_checkpoint_is_noop_without_token() -> None:
    token = CancellationToken()
    assert current_cancellation_token() is None
    cancellation_checkpoint()

    with bind_cancellation_token(token):
        assert current_cancellation_token() is token
    assert current_cancellation_token() is None


def test_interruptible_sleep_wakes_when_cancellation_is_requested() -> None:
    token = CancellationToken()
    started = threading.Event()
    stopped = threading.Event()

    def wait_for_cancellation() -> None:
        with bind_cancellation_token(token):
            started.set()
            with pytest.raises(CancellationError):
                interruptible_sleep(10)
            stopped.set()

    thread = threading.Thread(target=wait_for_cancellation)
    thread.start()
    assert started.wait(2)
    token.request()
    assert stopped.wait(2)
    thread.join(timeout=2)
    assert thread.is_alive() is False


@pytest.mark.parametrize("operation", ("", " ", "x" * 201))
def test_protected_section_names_are_bounded(operation: str) -> None:
    token = CancellationToken()
    with bind_cancellation_token(token), pytest.raises(ValueError):
        with non_interruptible_section(operation):
            pass
