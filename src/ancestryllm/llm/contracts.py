"""Narrow provider contract: generation only, never autonomous tool use."""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


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


class LLMProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def generate(self, request: GenerationRequest) -> GenerationResult: ...

    def stream(self, request: GenerationRequest) -> Iterator[str]: ...
