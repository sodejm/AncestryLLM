"""Stable, sanitized application errors shared by every interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AncestryError(Exception):
    """An error safe to render to a local user or future API client."""

    code: str
    message: str
    remediation: str | None = None
    exit_code: int = 1
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def render(self) -> str:
        lines = [f"[{self.code}] {self.message}"]
        if self.remediation:
            lines.append(f"How to fix: {self.remediation}")
        return "\n".join(lines)


class ConfigurationError(AncestryError):
    """Invalid or unsafe application configuration."""


class SecurityPolicyError(AncestryError):
    """A requested operation violates an explicit security policy."""


class StorageError(AncestryError):
    """Encrypted application storage could not be opened safely."""


class ProviderError(AncestryError):
    """An LLM provider request failed without exposing provider secrets."""
