"""Framework-independent contracts for bounded, transient chat."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from ancestryllm.application.dto import BoundaryDTO, ServiceRequest, ServiceResult

CHAT_SCHEMA_VERSION: Final[Literal[1]] = 1
CHAT_MODULE_ID = "chat"
CHAT_MAX_ACTIVE_SESSIONS: Final[Literal[32]] = 32
CHAT_MAX_MESSAGES: Final[Literal[32]] = 32
CHAT_MAX_MESSAGE_CHARACTERS: Final[Literal[16384]] = 16_384
CHAT_MAX_CONTEXT_CHARACTERS: Final[Literal[65536]] = 65_536
CHAT_MAX_OUTPUT_TOKENS: Final[Literal[4096]] = 4_096
CHAT_MAX_TEMPERATURE = 1.0
CHAT_MAX_TIMEOUT_SECONDS = 120.0
CHAT_MAX_SAFE_RETRIES: Final[Literal[1]] = 1
CHAT_STREAM_REPLAY_MAX_BYTES: Final[Literal[262144]] = 262_144


class ChatPurpose(StrEnum):
    """Explicit, consent-bindable purposes supported by transient chat."""

    GENEALOGY_ANALYSIS = "genealogy_analysis"
    SOURCE_ANALYSIS = "source_analysis"
    WRITING_ASSISTANCE = "writing_assistance"


class ChatDataClass(StrEnum):
    """Privacy classes declared at the transport-neutral chat boundary."""

    PUBLIC_GENEALOGY = "public_genealogy"
    DECEASED_PERSON = "deceased_person"
    LIVING_PERSON = "living_person"
    POSSIBLY_LIVING_PERSON = "possibly_living_person"
    FREE_TEXT_NOTE = "free_text_note"
    SOURCE_TRANSCRIPTION = "source_transcription"
    GOVERNMENT_IDENTIFIER = "government_identifier"


class ChatRole(StrEnum):
    """Roles retained in one transient chat history."""

    USER = "user"
    ASSISTANT = "assistant"


class ChatEventType(StrEnum):
    """Ordered lifecycle and output events emitted by one streaming run."""

    ACTIVE = "active"
    FIRST_TOKEN = "first-token"  # noqa: S105 - lifecycle label, not credential material
    DELTA = "delta"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


def _validate_schema_version(value: int) -> None:
    if isinstance(value, bool) or value != CHAT_SCHEMA_VERSION:
        raise ValueError("unsupported chat schema version")


def _validate_text(label: str, value: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-blank and at most {maximum} characters")
    if "\x00" in value:
        raise ValueError(f"{label} must not contain a null character")


def _validate_event_text(value: str) -> None:
    """Preserve provider fragments, including meaningful whitespace tokens."""

    if not isinstance(value, str) or not value or len(value) > CHAT_MAX_MESSAGE_CHARACTERS:
        raise ValueError(
            f"chat event text must be non-empty and at most {CHAT_MAX_MESSAGE_CHARACTERS} characters"
        )
    if "\x00" in value:
        raise ValueError("chat event text must not contain a null character")


def _validate_bool(label: str, value: bool, *, expected: bool | None = None) -> None:
    if not isinstance(value, bool) or (expected is not None and value is not expected):
        expectation = f" and equal {expected}" if expected is not None else ""
        raise ValueError(f"{label} must be a boolean{expectation}")


def _validate_integer(label: str, value: int | None, *, minimum: int, maximum: int) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")


def _validate_number(label: str, value: float | None, *, minimum: float, maximum: float) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{label} must be finite and between {minimum} and {maximum}")


def _validate_data_classes(value: tuple[ChatDataClass, ...]) -> None:
    if not isinstance(value, tuple) or not 1 <= len(value) <= len(ChatDataClass):
        raise ValueError("chat data classes must be a non-empty bounded tuple")
    if any(not isinstance(item, ChatDataClass) for item in value):
        raise ValueError("chat data classes contain an unsupported value")
    if len(set(value)) != len(value):
        raise ValueError("chat data classes must not contain duplicates")


def _validate_run_id(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 36
        or not value.startswith("run_")
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise ValueError("chat stream run id must be an opaque lowercase identifier")


def _validate_timestamp(value: str) -> None:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("chat event timestamp must be ISO 8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise ValueError("chat event timestamp must be ISO 8601 UTC")


@dataclass(frozen=True, slots=True)
class ChatMessage(BoundaryDTO):
    """Represent one validated user or assistant message in a chat session."""

    role: ChatRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, ChatRole):
            raise ValueError("chat message role is unsupported")
        _validate_text("chat message content", self.content, maximum=CHAT_MAX_MESSAGE_CHARACTERS)


@dataclass(frozen=True, slots=True)
class ChatSessionCreateRequest(ServiceRequest):
    """Carry validated inputs for creating an application chat session."""

    provider_profile_name: str
    model: str
    purpose: ChatPurpose
    data_classes: tuple[ChatDataClass, ...]
    consent_name: str | None = None
    schema_version: int = CHAT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_text("provider profile name", self.provider_profile_name, maximum=200)
        _validate_text("model", self.model, maximum=200)
        if not isinstance(self.purpose, ChatPurpose):
            raise ValueError("chat purpose is unsupported")
        _validate_data_classes(self.data_classes)
        if self.consent_name is not None:
            _validate_text("consent name", self.consent_name, maximum=200)


@dataclass(frozen=True, slots=True)
class ChatSession(ServiceResult):
    """Track the immutable messages and identity of an application chat session."""

    session_id: str
    provider_profile_name: str
    provider_id: str
    model: str
    purpose: ChatPurpose
    data_classes: tuple[ChatDataClass, ...]
    remote: bool
    consent_name: str | None
    message_count: int
    schema_version: int = CHAT_SCHEMA_VERSION
    transient: bool = True
    payload_retention: bool = False

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        if (
            len(self.session_id) != 37
            or not self.session_id.startswith("chat_")
            or any(character not in "0123456789abcdef" for character in self.session_id[5:])
        ):
            raise ValueError("chat session id must be an opaque lowercase identifier")
        _validate_text("provider profile name", self.provider_profile_name, maximum=200)
        _validate_text("provider id", self.provider_id, maximum=200)
        _validate_text("model", self.model, maximum=200)
        if not isinstance(self.purpose, ChatPurpose):
            raise ValueError("chat purpose is unsupported")
        _validate_data_classes(self.data_classes)
        _validate_bool("remote", self.remote)
        if self.consent_name is not None:
            _validate_text("consent name", self.consent_name, maximum=200)
        _validate_integer("message count", self.message_count, minimum=0, maximum=CHAT_MAX_MESSAGES)
        _validate_bool("transient", self.transient, expected=True)
        _validate_bool("payload retention", self.payload_retention, expected=False)


@dataclass(frozen=True, slots=True)
class ChatRunRequest(ServiceRequest):
    """Carry one consent-bound generation request for an existing chat session."""

    message: str
    max_output_tokens: int = 1_024
    temperature: float = 0.0
    timeout_seconds: float = 60.0
    max_safe_retries: int = 0
    schema_version: int = CHAT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_text("chat message", self.message, maximum=CHAT_MAX_MESSAGE_CHARACTERS)
        _validate_integer(
            "maximum output tokens",
            self.max_output_tokens,
            minimum=1,
            maximum=CHAT_MAX_OUTPUT_TOKENS,
        )
        _validate_number(
            "temperature",
            self.temperature,
            minimum=0.0,
            maximum=CHAT_MAX_TEMPERATURE,
        )
        _validate_number(
            "timeout seconds",
            self.timeout_seconds,
            minimum=1.0,
            maximum=CHAT_MAX_TIMEOUT_SECONDS,
        )
        _validate_integer(
            "maximum safe retries",
            self.max_safe_retries,
            minimum=0,
            maximum=CHAT_MAX_SAFE_RETRIES,
        )


@dataclass(frozen=True, slots=True)
class ChatRunSummary(ServiceResult):
    """Summarize the terminal state and output of a completed chat run."""

    assistant_message: ChatMessage
    provider_id: str
    model: str
    remote: bool
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    message_count: int
    schema_version: int = CHAT_SCHEMA_VERSION
    output_is_evidence: bool = False
    retained_payload: bool = False

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        if not isinstance(self.assistant_message, ChatMessage):
            raise ValueError("assistant message must use the chat contract")
        if self.assistant_message.role is not ChatRole.ASSISTANT:
            raise ValueError("chat run summaries require an assistant message")
        _validate_text("provider id", self.provider_id, maximum=200)
        _validate_text("model", self.model, maximum=200)
        _validate_bool("remote", self.remote)
        _validate_integer("input tokens", self.input_tokens, minimum=0, maximum=2**63 - 1)
        _validate_integer("output tokens", self.output_tokens, minimum=0, maximum=2**63 - 1)
        _validate_number("cost", self.cost_usd, minimum=0.0, maximum=1_000_000_000.0)
        _validate_integer("message count", self.message_count, minimum=0, maximum=CHAT_MAX_MESSAGES)
        _validate_bool("output evidence status", self.output_is_evidence, expected=False)
        _validate_bool("retained payload status", self.retained_payload, expected=False)


@dataclass(frozen=True, slots=True)
class ChatEventPayload(BoundaryDTO):
    """Sanitized, type-checked payload fields for a streaming chat event."""

    text: str | None = None
    code: str | None = None
    provider_id: str | None = None
    model: str | None = None
    remote: bool | None = None
    message_count: int | None = None

    def __post_init__(self) -> None:
        if self.text is not None:
            _validate_event_text(self.text)
        if self.code is not None:
            _validate_text("chat event code", self.code, maximum=100)
            if any(
                character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in self.code
            ):
                raise ValueError("chat event code must be a stable uppercase identifier")
        if self.provider_id is not None:
            _validate_text("chat event provider id", self.provider_id, maximum=200)
        if self.model is not None:
            _validate_text("chat event model", self.model, maximum=200)
        if self.remote is not None:
            _validate_bool("chat event remote status", self.remote)
        _validate_integer(
            "chat event message count",
            self.message_count,
            minimum=0,
            maximum=CHAT_MAX_MESSAGES,
        )


@dataclass(frozen=True, slots=True)
class ChatEvent(ServiceResult):
    """One schema-versioned event in a monotonic streaming run."""

    run_id: str
    sequence: int
    type: ChatEventType
    timestamp: str
    payload: ChatEventPayload
    schema_version: int = CHAT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_run_id(self.run_id)
        if self.sequence is None:
            raise ValueError("chat event sequence is required")
        _validate_integer("chat event sequence", self.sequence, minimum=1, maximum=2**63 - 1)
        if not isinstance(self.type, ChatEventType):
            raise ValueError("chat event type is unsupported")
        _validate_timestamp(self.timestamp)
        if not isinstance(self.payload, ChatEventPayload):
            raise ValueError("chat event payload must use the chat contract")
        populated = {
            name
            for name in ("text", "code", "provider_id", "model", "remote", "message_count")
            if getattr(self.payload, name) is not None
        }
        expected = {
            ChatEventType.ACTIVE: {"provider_id", "model", "remote"},
            ChatEventType.FIRST_TOKEN: {"text"},
            ChatEventType.DELTA: {"text"},
            ChatEventType.CANCELLING: set(),
            ChatEventType.COMPLETED: {"message_count"},
            ChatEventType.INTERRUPTED: {"code"},
            ChatEventType.FAILED: {"code"},
        }[self.type]
        if populated != expected:
            raise ValueError(f"chat event payload does not match {self.type.value}")


@dataclass(frozen=True, slots=True)
class ChatStreamRun(ServiceResult):
    """Current sanitized state for one in-memory streaming run."""

    session_id: str
    run_id: str
    state: ChatEventType
    latest_sequence: int
    terminal: bool
    schema_version: int = CHAT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        if (
            len(self.session_id) != 37
            or not self.session_id.startswith("chat_")
            or any(character not in "0123456789abcdef" for character in self.session_id[5:])
        ):
            raise ValueError("chat session id must be an opaque lowercase identifier")
        _validate_run_id(self.run_id)
        if self.state not in {
            ChatEventType.ACTIVE,
            ChatEventType.CANCELLING,
            ChatEventType.COMPLETED,
            ChatEventType.INTERRUPTED,
            ChatEventType.FAILED,
        }:
            raise ValueError("chat stream state is unsupported")
        if self.latest_sequence is None:
            raise ValueError("chat stream latest sequence is required")
        _validate_integer(
            "chat stream latest sequence",
            self.latest_sequence,
            minimum=1,
            maximum=2**63 - 1,
        )
        _validate_bool("chat stream terminal status", self.terminal)
        expected_terminal = self.state in {
            ChatEventType.COMPLETED,
            ChatEventType.INTERRUPTED,
            ChatEventType.FAILED,
        }
        if self.terminal is not expected_terminal:
            raise ValueError("chat stream terminal status does not match its state")


@dataclass(frozen=True, slots=True)
class ChatCapability(ServiceResult):
    """Describe provider availability and limits for the chat application service."""

    schema_version: int = CHAT_SCHEMA_VERSION
    max_active_sessions: int = CHAT_MAX_ACTIVE_SESSIONS
    max_messages: int = CHAT_MAX_MESSAGES
    max_message_characters: int = CHAT_MAX_MESSAGE_CHARACTERS
    max_context_characters: int = CHAT_MAX_CONTEXT_CHARACTERS
    max_output_tokens: int = CHAT_MAX_OUTPUT_TOKENS
    max_temperature: float = CHAT_MAX_TEMPERATURE
    max_timeout_seconds: float = CHAT_MAX_TIMEOUT_SECONDS
    max_safe_retries: int = CHAT_MAX_SAFE_RETRIES
    transient: bool = True
    tools_enabled: bool = False
    payload_retention: bool = False
    output_is_evidence: bool = False
    streaming: bool = True
    stream_replay_max_bytes: int = CHAT_STREAM_REPLAY_MAX_BYTES

    def __post_init__(self) -> None:
        expected = (
            self.schema_version == CHAT_SCHEMA_VERSION
            and self.max_active_sessions == CHAT_MAX_ACTIVE_SESSIONS
            and self.max_messages == CHAT_MAX_MESSAGES
            and self.max_message_characters == CHAT_MAX_MESSAGE_CHARACTERS
            and self.max_context_characters == CHAT_MAX_CONTEXT_CHARACTERS
            and self.max_output_tokens == CHAT_MAX_OUTPUT_TOKENS
            and self.max_temperature == CHAT_MAX_TEMPERATURE
            and self.max_timeout_seconds == CHAT_MAX_TIMEOUT_SECONDS
            and self.max_safe_retries == CHAT_MAX_SAFE_RETRIES
            and self.transient is True
            and self.tools_enabled is False
            and self.payload_retention is False
            and self.output_is_evidence is False
            and self.streaming is True
            and self.stream_replay_max_bytes == CHAT_STREAM_REPLAY_MAX_BYTES
        )
        if not expected:
            raise ValueError("chat capability values do not match schema version 1")


__all__ = [
    "CHAT_MAX_ACTIVE_SESSIONS",
    "CHAT_MAX_CONTEXT_CHARACTERS",
    "CHAT_MAX_MESSAGES",
    "CHAT_MAX_MESSAGE_CHARACTERS",
    "CHAT_MAX_OUTPUT_TOKENS",
    "CHAT_MAX_SAFE_RETRIES",
    "CHAT_MAX_TEMPERATURE",
    "CHAT_MAX_TIMEOUT_SECONDS",
    "CHAT_MODULE_ID",
    "CHAT_SCHEMA_VERSION",
    "CHAT_STREAM_REPLAY_MAX_BYTES",
    "ChatCapability",
    "ChatDataClass",
    "ChatEvent",
    "ChatEventPayload",
    "ChatEventType",
    "ChatMessage",
    "ChatPurpose",
    "ChatRole",
    "ChatRunRequest",
    "ChatRunSummary",
    "ChatSession",
    "ChatSessionCreateRequest",
    "ChatStreamRun",
]
