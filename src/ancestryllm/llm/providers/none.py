"""Deterministic offline provider sentinel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.core.errors import ProviderError
from ancestryllm.llm.contracts import GenerationRequest, GenerationResult, ProviderCapabilities

if TYPE_CHECKING:
    from collections.abc import Iterator


class NoneProvider:
    """Reject generation while preserving the network-free provider-none contract."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities exposed by the none provider."""
        return ProviderCapabilities(
            provider_id="none", remote=False, structured_output=False, streaming=False
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate a response through the none provider."""
        raise ProviderError(
            "PROVIDER_DISABLED",
            "No LLM provider was selected; the operation remains strictly offline.",
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        """Stream response chunks through the none provider."""
        self.generate(request)
        yield ""  # pragma: no cover
