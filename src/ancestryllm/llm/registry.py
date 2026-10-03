"""Explicit built-in provider registry; installed packages are never auto-loaded."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ancestryllm.core.errors import ProviderError

if TYPE_CHECKING:
    from collections.abc import Callable

    from ancestryllm.core.secrets import SecretStore
    from ancestryllm.llm.contracts import LLMProvider

PROVIDER_IDS = ("none", "ollama", "openai", "anthropic", "gemini", "openrouter")
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProviderRegistry:
    """Register and resolve provider implementations by stable identifier."""

    secrets: SecretStore
    _shared: dict[tuple[str, str, str], LLMProvider] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def create(
        self,
        provider_id: str,
        *,
        base_url: str | None = None,
        zero_data_retention: bool = True,
        profile_name: str | None = None,
    ) -> LLMProvider:
        """Create a provider implementation from its registered factory."""
        if provider_id == "none":
            from ancestryllm.llm.providers.none import NoneProvider

            return self._shared_provider(("none", "", ""), NoneProvider)
        if provider_id == "ollama":
            from ancestryllm.llm.providers.ollama import OllamaProvider

            endpoint = base_url or "http://127.0.0.1:11434"
            return self._shared_provider(
                ("ollama", profile_name or "", endpoint),
                lambda: OllamaProvider(endpoint),
            )
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

    def _shared_provider(
        self,
        key: tuple[str, str, str],
        factory: Callable[[], LLMProvider],
    ) -> LLMProvider:
        with self._lock:
            if self._closed:
                raise ProviderError(
                    "PROVIDER_SERVICE_CLOSED",
                    "The provider registry is shutting down.",
                )
            provider = self._shared.get(key)
            if provider is None:
                provider = factory()
                self._shared[key] = provider
            return provider

    def close(self) -> None:
        """Close shared provider clients exactly once and reject new work."""

        with self._lock:
            self._closed = True
            providers = tuple(self._shared.values())
            self._shared.clear()
        for provider in providers:
            close = getattr(provider, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - adapters own arbitrary client types
                    logger.warning("Provider client close failed: %s", type(exc).__name__)
