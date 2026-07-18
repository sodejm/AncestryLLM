"""Deterministic offline provider sentinel."""

from __future__ import annotations

from collections.abc import Iterator

from ancestryllm.core.errors import ProviderError
from ancestryllm.llm.contracts import GenerationRequest, GenerationResult, ProviderCapabilities


class NoneProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id="none", remote=False, structured_output=False, streaming=False
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise ProviderError(
            "PROVIDER_DISABLED",
            "No LLM provider was selected; the operation remains strictly offline.",
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        self.generate(request)
        yield ""  # pragma: no cover
