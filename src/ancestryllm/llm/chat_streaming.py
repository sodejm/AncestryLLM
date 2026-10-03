"""Ordered, bounded lifecycle coordination for transient chat streams."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import secrets
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from ancestryllm.application.chat import (
    CHAT_MAX_ACTIVE_SESSIONS,
    CHAT_STREAM_REPLAY_MAX_BYTES,
    ChatEvent,
    ChatEventPayload,
    ChatEventType,
    ChatRunRequest,
    ChatStreamRun,
)
from ancestryllm.core.errors import AncestryError, ProviderError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ancestryllm.llm.chat import ChatService, ChatStreamHandle
    from ancestryllm.llm.service import LLMService

_TERMINAL_EVENT_TYPES = frozenset(
    {
        ChatEventType.COMPLETED,
        ChatEventType.INTERRUPTED,
        ChatEventType.FAILED,
    }
)


@dataclass(slots=True)
class _RunState:
    session_id: str
    run_id: str
    handle: ChatStreamHandle
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    events: deque[tuple[ChatEvent, int]] = field(default_factory=deque)
    replay_bytes: int = 0
    latest_sequence: int = 0
    lifecycle: ChatEventType = ChatEventType.ACTIVE
    task: asyncio.Task[None] | None = None
    cancel_code: str = "CHAT_STREAM_CANCELLED"

    @property
    def terminal(self) -> bool:
        return self.lifecycle in _TERMINAL_EVENT_TYPES


class ChatStreamingService:
    """Own active stream tasks and expose owner-scoped ordered event replay."""

    def __init__(
        self,
        chat: ChatService,
        llm: LLMService,
        *,
        replay_max_bytes: int = CHAT_STREAM_REPLAY_MAX_BYTES,
    ) -> None:
        if (
            isinstance(replay_max_bytes, bool)
            or not isinstance(replay_max_bytes, int)
            or replay_max_bytes < 1
            or replay_max_bytes > CHAT_STREAM_REPLAY_MAX_BYTES
        ):
            raise ValueError("chat stream replay bytes exceed the bounded contract")
        self._chat = chat
        self._llm = llm
        self._replay_max_bytes = replay_max_bytes
        self._runs: dict[str, _RunState] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._closed = False

    async def startup(self) -> int:
        """Reconcile interrupted audits before accepting any new stream."""

        async with self._lock:
            if self._closed:
                raise AncestryError(
                    "CHAT_STREAM_SERVICE_CLOSED",
                    "Chat streaming is shutting down.",
                )
            if self._started:
                return 0
            reconciled = self._llm.reconcile_interrupted_stream_runs()
            self._started = True
            return reconciled

    def _require_started(self) -> None:
        if not self._started:
            raise AncestryError(
                "CHAT_STREAM_SERVICE_NOT_READY",
                "Chat streaming has not completed startup reconciliation.",
            )
        if self._closed:
            raise AncestryError(
                "CHAT_STREAM_SERVICE_CLOSED",
                "Chat streaming is shutting down.",
            )

    @staticmethod
    def _run_id() -> str:
        return f"run_{secrets.token_hex(16)}"

    @staticmethod
    def _timestamp() -> str:
        return dt.datetime.now(dt.UTC).isoformat()

    @staticmethod
    def _event_size(event: ChatEvent) -> int:
        return len(
            json.dumps(
                asdict(event),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )

    async def _publish(
        self,
        state: _RunState,
        event_type: ChatEventType,
        payload: ChatEventPayload,
    ) -> ChatEvent:
        async with state.condition:
            if state.terminal:
                return state.events[-1][0]
            state.latest_sequence += 1
            event = ChatEvent(
                run_id=state.run_id,
                sequence=state.latest_sequence,
                type=event_type,
                timestamp=self._timestamp(),
                payload=payload,
            )
            size = self._event_size(event)
            state.events.append((event, size))
            state.replay_bytes += size
            while state.replay_bytes > self._replay_max_bytes and len(state.events) > 1:
                _, removed_size = state.events.popleft()
                state.replay_bytes -= removed_size
            state.lifecycle = event_type
            state.condition.notify_all()
            return event

    @staticmethod
    def _public_run(state: _RunState) -> ChatStreamRun:
        return ChatStreamRun(
            session_id=state.session_id,
            run_id=state.run_id,
            state=state.lifecycle,
            latest_sequence=state.latest_sequence,
            terminal=state.terminal,
        )

    async def start(
        self,
        session_id: str,
        request: ChatRunRequest,
    ) -> ChatStreamRun:
        """Start one provider request and return after its audit row exists."""

        self._require_started()
        async with self._lock:
            self._require_started()
            if len(self._runs) >= CHAT_MAX_ACTIVE_SESSIONS:
                terminal_ids = tuple(
                    run_id for run_id, state in self._runs.items() if state.terminal
                )
                for run_id in terminal_ids:
                    self._runs.pop(run_id)
                    if len(self._runs) < CHAT_MAX_ACTIVE_SESSIONS:
                        break
            if len(self._runs) >= CHAT_MAX_ACTIVE_SESSIONS:
                raise AncestryError(
                    "CHAT_STREAM_LIMIT",
                    "The active chat stream limit has been reached.",
                )
            run_id = self._run_id()
            handle = self._chat.open_stream(session_id, request, run_id=run_id)
            state = _RunState(session_id=session_id, run_id=run_id, handle=handle)
            self._runs[run_id] = state
            try:
                await self._publish(
                    state,
                    ChatEventType.ACTIVE,
                    ChatEventPayload(
                        provider_id=handle.state.session.provider_id,
                        model=handle.state.session.model,
                        remote=handle.state.session.remote,
                    ),
                )
                state.task = asyncio.create_task(
                    self._execute(state),
                    name=f"chat-stream-{run_id}",
                )
            except BaseException:
                self._chat.abandon_stream(handle, error_code="CHAT_STREAM_START_FAILED")
                self._runs.pop(run_id, None)
                raise
            return self._public_run(state)

    async def _execute(self, state: _RunState) -> None:
        first_chunk = True

        async def on_chunk(chunk: str) -> None:
            nonlocal first_chunk
            event_type = ChatEventType.FIRST_TOKEN if first_chunk else ChatEventType.DELTA
            first_chunk = False
            await self._publish(state, event_type, ChatEventPayload(text=chunk))

        try:
            summary = await self._chat.consume_stream(state.handle, on_chunk)
        except asyncio.CancelledError:
            self._llm.terminalize_stream_audit(
                state.run_id,
                error_code=state.cancel_code,
            )
            await self._publish(
                state,
                ChatEventType.INTERRUPTED,
                ChatEventPayload(code=state.cancel_code),
            )
            return
        except ProviderError as exc:
            interrupted = first_chunk is False or exc.code == "PROVIDER_CANCELLED"
            event_type = ChatEventType.INTERRUPTED if interrupted else ChatEventType.FAILED
            self._llm.terminalize_stream_audit(state.run_id, error_code=exc.code)
            await self._publish(state, event_type, ChatEventPayload(code=exc.code))
            return
        # Provider adapters are an extensibility boundary, so unknown failures must
        # still terminalize the audit record without exposing provider details.
        except Exception:  # noqa: BLE001
            self._llm.terminalize_stream_audit(
                state.run_id,
                error_code="CHAT_STREAM_INTERNAL",
            )
            await self._publish(
                state,
                ChatEventType.FAILED if first_chunk else ChatEventType.INTERRUPTED,
                ChatEventPayload(code="CHAT_STREAM_INTERNAL"),
            )
            return

        await self._publish(
            state,
            ChatEventType.COMPLETED,
            ChatEventPayload(message_count=summary.message_count),
        )

    async def _owned_state(self, session_id: str, run_id: str) -> _RunState:
        self._require_started()
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None or state.session_id != session_id:
                raise AncestryError(
                    "CHAT_STREAM_NOT_FOUND",
                    "The chat stream does not exist.",
                )
            return state

    @staticmethod
    def _validate_cursor(after_sequence: int) -> None:
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise AncestryError(
                "CHAT_STREAM_CURSOR_INVALID",
                "The chat stream replay cursor is invalid.",
            )

    async def subscribe(
        self,
        session_id: str,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[ChatEvent]:
        """Prepare an owner-scoped replay before SSE headers are sent."""

        self._validate_cursor(after_sequence)
        state = await self._owned_state(session_id, run_id)
        async with state.condition:
            if after_sequence > state.latest_sequence:
                raise AncestryError(
                    "CHAT_STREAM_CURSOR_INVALID",
                    "The chat stream replay cursor is ahead of the stream.",
                )
            if state.events and after_sequence < state.events[0][0].sequence - 1:
                raise AncestryError(
                    "CHAT_STREAM_REPLAY_EXPIRED",
                    "The requested chat stream events are no longer buffered.",
                )
            initial = tuple(event for event, _ in state.events if event.sequence > after_sequence)

        async def subscription() -> AsyncIterator[ChatEvent]:
            cursor = after_sequence
            pending = initial
            while True:
                for event in pending:
                    cursor = event.sequence
                    yield event
                async with state.condition:
                    terminal = state.terminal and cursor >= state.latest_sequence
                    if terminal:
                        return
                    if state.events and cursor < state.events[0][0].sequence - 1:
                        raise AncestryError(
                            "CHAT_STREAM_REPLAY_EXPIRED",
                            "The requested chat stream events are no longer buffered.",
                        )
                    pending = tuple(event for event, _ in state.events if event.sequence > cursor)
                    if not pending:
                        await state.condition.wait()

        return subscription()

    async def events(
        self,
        session_id: str,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[ChatEvent]:
        """Yield ordered owner-scoped events, waiting until one terminal event."""

        subscription = await self.subscribe(
            session_id,
            run_id,
            after_sequence=after_sequence,
        )
        async for event in subscription:
            yield event

    async def cancel(self, session_id: str, run_id: str) -> ChatStreamRun:
        """Cancel an owner-scoped stream and wait for its terminal event."""

        state = await self._owned_state(session_id, run_id)
        if state.terminal:
            return self._public_run(state)
        state.cancel_code = "CHAT_STREAM_CANCELLED"
        await self._publish(state, ChatEventType.CANCELLING, ChatEventPayload())
        self._llm.terminalize_stream_audit(
            state.run_id,
            error_code=state.cancel_code,
        )
        if state.task is not None and not state.task.done():
            state.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.task
        return self._public_run(state)

    async def shutdown(self) -> None:
        """Interrupt every active stream and wait for audit terminalization."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            states = tuple(self._runs.values())
        for state in states:
            if state.terminal:
                continue
            state.cancel_code = "CHAT_STREAM_SHUTDOWN"
            await self._publish(state, ChatEventType.CANCELLING, ChatEventPayload())
            self._llm.terminalize_stream_audit(
                state.run_id,
                error_code=state.cancel_code,
            )
            if state.task is not None and not state.task.done():
                state.task.cancel()
        for state in states:
            if state.task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await state.task


__all__ = ["ChatStreamingService"]
