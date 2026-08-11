"""Bounded provider scheduling and exact, process-local single-flight caching."""

from __future__ import annotations

import collections
import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TypeVar

from ancestryllm.core.errors import ProviderError

ResultT = TypeVar("ResultT")
CancellationCheck = Callable[[], None]


def _no_cancellation() -> None:
    return None


@dataclass(slots=True)
class _ExecutionLane:
    condition: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.RLock())
    )
    admitted: int = 0
    active: int = 0
    waiting: int = 0
    closed: bool = False


class ProviderExecutionCoordinator:
    """Bound concurrency and queue depth independently for each provider route/model."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._lanes: dict[tuple[str, ...], _ExecutionLane] = {}
        self._closed = False

    def run(
        self,
        key: tuple[str, ...],
        function: Callable[[], ResultT],
        *,
        max_concurrency: int,
        max_pending: int,
        timeout_seconds: float,
        cancellation_check: CancellationCheck = _no_cancellation,
    ) -> ResultT:
        with self.lease(
            key,
            max_concurrency=max_concurrency,
            max_pending=max_pending,
            timeout_seconds=timeout_seconds,
            cancellation_check=cancellation_check,
        ):
            return function()

    @contextlib.contextmanager
    def admission(
        self,
        key: tuple[str, ...],
        *,
        max_pending: int,
    ) -> Iterator[None]:
        with self._lock:
            if self._closed:
                raise ProviderError(
                    "PROVIDER_SERVICE_CLOSED",
                    "The provider execution service is shutting down.",
                )
            lane = self._lanes.setdefault(key, _ExecutionLane())

        admitted = False
        try:
            with lane.condition:
                if lane.closed:
                    raise ProviderError(
                        "PROVIDER_SERVICE_CLOSED",
                        "The provider execution service is shutting down.",
                    )
                if lane.admitted >= max_pending:
                    raise ProviderError(
                        "PROVIDER_QUEUE_FULL",
                        "The selected provider profile reached its bounded request limit.",
                        "Wait for an active request to finish, then retry.",
                        details={"max_pending": max_pending},
                    )
                lane.admitted += 1
                admitted = True
            yield
        finally:
            if admitted:
                with lane.condition:
                    lane.admitted -= 1
                    lane.condition.notify_all()

    @contextlib.contextmanager
    def capacity(
        self,
        key: tuple[str, ...],
        *,
        max_concurrency: int,
        timeout_seconds: float,
        cancellation_check: CancellationCheck = _no_cancellation,
    ) -> Iterator[None]:
        with self._lock:
            if self._closed:
                raise ProviderError(
                    "PROVIDER_SERVICE_CLOSED",
                    "The provider execution service is shutting down.",
                )
            lane = self._lanes.setdefault(key, _ExecutionLane())

        deadline = time.monotonic() + timeout_seconds
        acquired = False
        queued = False
        try:
            with lane.condition:
                if lane.closed:
                    raise ProviderError(
                        "PROVIDER_SERVICE_CLOSED",
                        "The provider execution service is shutting down.",
                    )
                if lane.active >= max_concurrency:
                    lane.waiting += 1
                    queued = True
                while lane.active >= max_concurrency:
                    cancellation_check()
                    if lane.closed:
                        raise ProviderError(
                            "PROVIDER_SERVICE_CLOSED",
                            "The provider execution service is shutting down.",
                        )
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProviderError(
                            "PROVIDER_QUEUE_TIMEOUT",
                            "The provider request timed out while waiting for bounded capacity.",
                            "Retry after another provider request finishes.",
                        )
                    lane.condition.wait(min(remaining, 0.05))
                cancellation_check()
                lane.active += 1
                acquired = True
                if queued:
                    lane.waiting -= 1
                    queued = False
            yield
        finally:
            with lane.condition:
                if queued:
                    lane.waiting -= 1
                if acquired:
                    lane.active -= 1
                lane.condition.notify_all()

    @contextlib.contextmanager
    def lease(
        self,
        key: tuple[str, ...],
        *,
        max_concurrency: int,
        max_pending: int,
        timeout_seconds: float,
        cancellation_check: CancellationCheck = _no_cancellation,
    ) -> Iterator[None]:
        """Admit one request and lease provider capacity for its active call."""

        with (
            self.admission(key, max_pending=max_pending),
            self.capacity(
                key,
                max_concurrency=max_concurrency,
                timeout_seconds=timeout_seconds,
                cancellation_check=cancellation_check,
            ),
        ):
            yield

    def close(self) -> None:
        with self._lock:
            self._closed = True
            lanes = tuple(self._lanes.values())
        for lane in lanes:
            with lane.condition:
                lane.closed = True
                lane.condition.notify_all()


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    result: object
    expires_at: float


@dataclass(slots=True)
class _Flight:
    condition: threading.Condition
    complete: bool = False
    result: object | None = None
    failure: BaseException | None = None


class ExactResultCache:
    """Keep successful deterministic results in bounded memory and collapse duplicates."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: collections.OrderedDict[str, _CacheEntry] = collections.OrderedDict()
        self._flights: dict[str, _Flight] = {}
        self._closed = False

    def get_or_execute(
        self,
        key: str,
        function: Callable[[], ResultT],
        *,
        ttl_seconds: float,
        max_entries: int,
        timeout_seconds: float,
        cancellation_check: CancellationCheck = _no_cancellation,
        cache_when: Callable[[ResultT], bool] = lambda _result: True,
    ) -> tuple[ResultT, bool]:
        now = time.monotonic()
        owner = False
        with self._lock:
            if self._closed:
                raise ProviderError(
                    "PROVIDER_SERVICE_CLOSED",
                    "The provider execution service is shutting down.",
                )
            self._discard_expired(now)
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry.result, True  # type: ignore[return-value]
            flight = self._flights.get(key)
            if flight is None:
                flight = _Flight(threading.Condition(self._lock))
                self._flights[key] = flight
                owner = True

        if owner:
            try:
                result = function()
            except BaseException as exc:
                with self._lock:
                    flight.failure = exc
                    flight.complete = True
                    self._flights.pop(key, None)
                    flight.condition.notify_all()
                raise
            with self._lock:
                if not self._closed and cache_when(result):
                    self._entries[key] = _CacheEntry(
                        result=result,
                        expires_at=time.monotonic() + ttl_seconds,
                    )
                    self._entries.move_to_end(key)
                    while len(self._entries) > max_entries:
                        self._entries.popitem(last=False)
                flight.result = result
                flight.complete = True
                self._flights.pop(key, None)
                flight.condition.notify_all()
            return result, False

        deadline = time.monotonic() + timeout_seconds
        with flight.condition:
            while not flight.complete:
                cancellation_check()
                if self._closed:
                    raise ProviderError(
                        "PROVIDER_SERVICE_CLOSED",
                        "The provider execution service is shutting down.",
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError(
                        "PROVIDER_CACHE_WAIT_TIMEOUT",
                        "The provider request timed out while waiting for an identical request.",
                        "Retry after the active request finishes.",
                    )
                flight.condition.wait(min(remaining, 0.05))
            cancellation_check()
            if flight.failure is not None:
                raise flight.failure
            return flight.result, True  # type: ignore[return-value]

    def _discard_expired(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._entries.clear()
            flights = tuple(self._flights.values())
        for flight in flights:
            with flight.condition:
                flight.condition.notify_all()


__all__ = [
    "CancellationCheck",
    "ExactResultCache",
    "ProviderExecutionCoordinator",
]
