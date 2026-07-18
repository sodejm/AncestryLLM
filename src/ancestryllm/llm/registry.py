"""Explicit built-in provider registry; installed packages are never auto-loaded."""

from __future__ import annotations

from dataclasses import dataclass

from ancestryllm.core.errors import ProviderError
from ancestryllm.core.secrets import SecretStore
from ancestryllm.llm.contracts import LLMProvider

PROVIDER_IDS = ("none", "ollama", "openai", "anthropic", "gemini", "openrouter")


@dataclass(slots=True)
class ProviderRegistry:
    secrets: SecretStore

    def create(
        self, provider_id: str, *, base_url: str | None = None, zero_data_retention: bool = True
    ) -> LLMProvider:
        if provider_id == "none":
            from ancestryllm.llm.providers.none import NoneProvider

            return NoneProvider()
        if provider_id == "ollama":
            from ancestryllm.llm.providers.ollama import OllamaProvider

            return OllamaProvider(base_url or "http://127.0.0.1:11434")
        if provider_id == "openai":
            from ancestryllm.llm.providers.openai import OpenAIProvider

            return OpenAIProvider(self.secrets.get("openai.api_key") or "")
        if provider_id == "anthropic":
            from ancestryllm.llm.providers.anthropic import AnthropicProvider

            return AnthropicProvider(self.secrets.get("anthropic.api_key") or "")
        if provider_id == "gemini":
            from ancestryllm.llm.providers.gemini import GeminiProvider

            return GeminiProvider(self.secrets.get("gemini.api_key") or "")
        if provider_id == "openrouter":
            from ancestryllm.llm.providers.openai import OpenAIProvider

            return OpenAIProvider(
                self.secrets.get("openrouter.api_key") or "",
                provider_id="openrouter",
                base_url=base_url or "https://openrouter.ai/api/v1",
                zero_data_retention=zero_data_retention,
            )
        raise ProviderError("PROVIDER_UNKNOWN", f"Unknown provider: {provider_id}")
