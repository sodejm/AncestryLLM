"""Transport-neutral secret-reference identifiers."""

from typing import Final

SUPPORTED_SECRET_REFERENCES: Final = frozenset(
    {
        "openai.api_key",
        "anthropic.api_key",
        "gemini.api_key",
        "openrouter.api_key",
        "openrouter.management_key",
        "database.master_key",
    }
)

__all__ = ["SUPPORTED_SECRET_REFERENCES"]
