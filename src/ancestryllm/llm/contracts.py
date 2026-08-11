"""Narrow provider contract: generation only, never autonomous tool use."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "DataClass",
    "GenerationRequest",
    "GenerationResult",
    "LLMProvider",
    "Message",
    "ProviderCapabilities",
    "ProviderExecution",
]


class DataClass(StrEnum):
    PUBLIC_GENEALOGY = "public_genealogy"
    DECEASED_PERSON = "deceased_person"
    LIVING_PERSON = "living_person"
    POSSIBLY_LIVING_PERSON = "possibly_living_person"
    FREE_TEXT_NOTE = "free_text_note"
    SOURCE_TRANSCRIPTION = "source_transcription"
    GOVERNMENT_IDENTIFIER = "government_identifier"


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str = Field(pattern=r"^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=100_000)


class ProviderExecution(BaseModel):
    """Bounded provider execution settings resolved from an explicit profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=2_048)
    zero_data_retention: bool = True
    keep_alive: int | str | None = None
    num_ctx: int | None = Field(default=None, ge=512, le=262_144)
    num_batch: int | None = Field(default=None, ge=1, le=4_096)
    num_thread: int | None = Field(default=None, ge=1, le=256)
    num_gpu: int | None = Field(default=None, ge=0, le=256)
    seed: int | None = Field(default=None, ge=-(2**31), le=(2**31) - 1)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    max_pending: int = Field(default=64, ge=1, le=1_024)
    cache_ttl_seconds: float = Field(default=0.0, ge=0.0, le=86_400.0)
    cache_max_entries: int = Field(default=128, ge=1, le=4_096)

    @model_validator(mode="after")
    def validate_bounds(self) -> ProviderExecution:
        if self.max_pending < self.max_concurrency:
            raise ValueError("max_pending must be greater than or equal to max_concurrency")
        if isinstance(self.keep_alive, str):
            value = self.keep_alive.strip()
            if not value or len(value) > 32:
                raise ValueError("keep_alive must be a non-empty bounded duration")
        elif isinstance(self.keep_alive, int) and self.keep_alive < 0:
            raise ValueError("keep_alive must not be negative")
        return self


class GenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    model: str
    module_id: str
    purpose: str
    messages: tuple[Message, ...]
    response_schema: dict[str, Any] | None = None
    data_classes: frozenset[DataClass] = frozenset()
    max_output_tokens: int = Field(default=1_024, ge=1, le=32_768)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    max_safe_retries: int = Field(default=0, ge=0, le=2)
    execution: ProviderExecution = Field(default_factory=ProviderExecution)


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    remote: bool
    structured_output: bool
    streaming: bool
    retention_known: bool = False
    zero_data_retention: bool = False


class GenerationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    model: str
    text: str
    parsed: Any | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    request_id: str | None = None
    remote: bool = False


class LLMProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def generate(self, request: GenerationRequest) -> GenerationResult: ...

    def stream(self, request: GenerationRequest) -> Iterator[str]: ...
