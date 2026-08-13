"""Bounded bridge from synchronous provider iterators to asyncio consumers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import threading
from collections.abc import Callable
from dataclasses import dataclass

from ancestryllm.core.errors import ProviderError

DEFAULT_ASYNC_STREAM_QUEUE_ITEMS = 16
DEFAULT_ASYNC_STREAM_MAX_CHUNK_BYTES = 64 * 1024
MAX_ASYNC_STREAM_QUEUE_ITEMS = 256
MAX_ASYNC_STREAM_CHUNK_BYTES = 1024 * 1024
MAX_ASYNC_STREAM_BUFFER_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class StreamQueueItem:
    """One bounded bridge item without provider or request metadata."""

    chunk: str | None = None
    failure: BaseException | None = None
    complete: bool = False


ChunkPublisher = Callable[[str], None]
StreamProducer = Callable[[ChunkPublisher, threading.Event], None]


class BoundedAsyncStreamBridge:
    """Run a synchronous producer off-loop and apply queue backpressure."""

    def __init__(
        self,
        producer: StreamProducer,
        *,
        max_items: int,
        max_chunk_bytes: int,
    ) -> None:
        validate_async_stream_bounds(
            max_items=max_items,
            max_chunk_bytes=max_chunk_bytes,
        )
        self._producer = producer
        self._max_items = max_items
        self._max_chunk_bytes = max_chunk_bytes
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[StreamQueueItem] | None = None

    def start(self) -> None:
        """Start one context-preserving daemon worker from a running event loop."""

        if self._loop is not None:
            raise RuntimeError("The asynchronous stream bridge has already started.")
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._max_items)
        context = contextvars.copy_context()
        worker = threading.Thread(
            target=context.run,
            args=(self._run,),
            name="ancestryllm-provider-stream",
            daemon=True,
        )
        worker.start()

    async def receive(self) -> StreamQueueItem:
        queue = self._queue
        if queue is None:
            raise RuntimeError("The asynchronous stream bridge has not started.")
        return await queue.get()

    def cancel(self) -> None:
        """Request cooperative producer shutdown without blocking the event loop."""

        self._stop.set()

    def _run(self) -> None:
        failure: BaseException | None = None
        try:
            self._producer(self._publish_chunk, self._stop)
        except BaseException as exc:  # noqa: BLE001 - transports cancellation too
            failure = exc

        if self._stop.is_set():
            return
        terminal = (
            StreamQueueItem(failure=failure)
            if failure is not None
            else StreamQueueItem(complete=True)
        )
        self._put(terminal)

    def _publish_chunk(self, chunk: str) -> None:
        if not isinstance(chunk, str):
            raise ProviderError(
                "PROVIDER_STREAM_CHUNK_INVALID",
                "The provider returned a non-text stream chunk.",
                "Check the provider adapter before retrying.",
            )
        chunk_bytes = len(chunk.encode("utf-8"))
        if chunk_bytes > self._max_chunk_bytes:
            raise ProviderError(
                "PROVIDER_STREAM_CHUNK_TOO_LARGE",
                "The provider returned a stream chunk above the safe byte limit.",
                "Reduce the provider chunk size before retrying.",
                details={"max_chunk_bytes": self._max_chunk_bytes},
            )
        if not self._put(StreamQueueItem(chunk=chunk)):
            raise asyncio.CancelledError

    def _put(self, item: StreamQueueItem) -> bool:
        loop = self._loop
        queue = self._queue
        if loop is None or queue is None or self._stop.is_set():
            return False
        put = queue.put(item)
        try:
            pending = asyncio.run_coroutine_threadsafe(put, loop)
        except RuntimeError:
            put.close()
            return False
        while True:
            try:
                pending.result(timeout=0.05)
            except concurrent.futures.TimeoutError:
                if not self._stop.is_set():
                    continue
                pending.cancel()
                return False
            except concurrent.futures.CancelledError:
                return False
            else:
                return True


def validate_async_stream_bounds(*, max_items: int, max_chunk_bytes: int) -> None:
    """Reject unbounded or nonsensical bridge configuration."""

    if (
        isinstance(max_items, bool)
        or not isinstance(max_items, int)
        or not 1 <= max_items <= MAX_ASYNC_STREAM_QUEUE_ITEMS
    ):
        raise ValueError(
            "async stream queue items must be an integer between 1 and "
            f"{MAX_ASYNC_STREAM_QUEUE_ITEMS}"
        )
    if (
        isinstance(max_chunk_bytes, bool)
        or not isinstance(max_chunk_bytes, int)
        or not 1 <= max_chunk_bytes <= MAX_ASYNC_STREAM_CHUNK_BYTES
    ):
        raise ValueError(
            "async stream maximum chunk bytes must be an integer between 1 and "
            f"{MAX_ASYNC_STREAM_CHUNK_BYTES}"
        )
    if max_items * max_chunk_bytes > MAX_ASYNC_STREAM_BUFFER_BYTES:
        raise ValueError(
            f"async stream queue capacity must not exceed {MAX_ASYNC_STREAM_BUFFER_BYTES} bytes"
        )
