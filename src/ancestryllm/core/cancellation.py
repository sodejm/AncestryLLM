"""Cooperative cancellation primitives shared by jobs and application services."""

from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class CancellationError(BaseException):
    """Signal that work stopped at a cooperative cancellation boundary."""


@dataclass(frozen=True, slots=True)
class CancellationState:
    """Serializable cancellation state without operation inputs or private data."""

    requested_at: str | None
    pending: bool
    deferred_by: str | None


CancellationListener = Callable[[CancellationState], None]


class CancellationToken:
    """Thread-safe cancellation request with protected non-interruptible sections."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._requested_at: str | None = None
        self._deferred_operations: list[str] = []
        self._listeners: list[CancellationListener] = []

    @property
    def state(self) -> CancellationState:
        with self._lock:
            return CancellationState(
                requested_at=self._requested_at,
                pending=self._requested_at is not None and bool(self._deferred_operations),
                deferred_by=self._deferred_operations[-1] if self._deferred_operations else None,
            )

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> bool:
        """Request cancellation and return whether this was the first request."""

        with self._lock:
            first_request = self._requested_at is None
            if first_request:
                self._requested_at = datetime.now(UTC).isoformat()
                self._event.set()
        if first_request:
            self._notify(self.state)
        return first_request

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for a cancellation request without busy polling."""

        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        """Raise at an interruptible boundary, but never inside a protected section."""

        state = self.state
        if state.requested_at is not None and not state.pending:
            raise CancellationError("The background job was cancelled at a safe boundary.")

    @contextlib.contextmanager
    def defer(self, operation: str) -> Iterator[None]:
        """Defer cancellation while one atomic operation completes or rolls back."""

        normalized = operation.strip()
        if not normalized:
            raise ValueError("A protected cancellation section requires an operation name.")
        if len(normalized) > 200:
            raise ValueError("A protected cancellation section name is limited to 200 characters.")
        with self._lock:
            if self._requested_at is not None and not self._deferred_operations:
                raise CancellationError("The background job was cancelled at a safe boundary.")
            self._deferred_operations.append(normalized)
        try:
            yield
        finally:
            with self._lock:
                if not self._deferred_operations:
                    raise RuntimeError("Cancellation section state was unbalanced.")
                self._deferred_operations.pop()
                state = self.state
            if state.requested_at is not None:
                self._notify(state)
        self.raise_if_cancelled()

    def subscribe(self, listener: CancellationListener) -> Callable[[], None]:
        """Subscribe to state changes and return an idempotent unsubscriber."""

        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def _notify(self, state: CancellationState) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(state)
            except BaseException as exc:  # noqa: BLE001 - observers cannot break cancellation
                logger.warning("Cancellation listener failed: %s", type(exc).__name__)


_CURRENT_TOKEN: contextvars.ContextVar[CancellationToken | None] = contextvars.ContextVar(
    "ancestryllm_cancellation_token",
    default=None,
)


@contextlib.contextmanager
def bind_cancellation_token(token: CancellationToken) -> Iterator[None]:
    """Bind a job token to the current execution context."""

    reset_token = _CURRENT_TOKEN.set(token)
    try:
        yield
    finally:
        _CURRENT_TOKEN.reset(reset_token)


def current_cancellation_token() -> CancellationToken | None:
    """Return the token for the current job, if execution is job-managed."""

    return _CURRENT_TOKEN.get()


def cancellation_checkpoint() -> None:
    """Stop current job-managed work at an interruptible boundary."""

    token = current_cancellation_token()
    if token is not None:
        token.raise_if_cancelled()


@contextlib.contextmanager
def non_interruptible_section(operation: str) -> Iterator[None]:
    """Protect a small atomic publication or rollback section from interruption."""

    token = current_cancellation_token()
    if token is None:
        yield
        return
    with token.defer(operation):
        yield


def interruptible_sleep(seconds: float) -> None:
    """Sleep normally outside a job, or wake promptly when its token is cancelled."""

    if seconds < 0:
        raise ValueError("sleep length must be non-negative")
    token = current_cancellation_token()
    if token is None:
        time.sleep(seconds)
        return
    if token.wait(seconds):
        token.raise_if_cancelled()


__all__ = [
    "CancellationError",
    "CancellationState",
    "CancellationToken",
    "bind_cancellation_token",
    "cancellation_checkpoint",
    "current_cancellation_token",
    "interruptible_sleep",
    "non_interruptible_section",
]
