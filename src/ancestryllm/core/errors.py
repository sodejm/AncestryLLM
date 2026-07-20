"""Stable, sanitized application errors shared by every interface."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx


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


def is_provider_cancellation(exc: BaseException) -> bool:
    """Return whether an exception represents an explicitly cancelled request."""

    return isinstance(exc, (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt)) or type(
        exc
    ).__name__ in {"CancelledError", "CanceledError"}


def normalize_provider_error(
    exc: BaseException,
    provider_id: str,
    *,
    streaming: bool = False,
    stream_started: bool = False,
) -> ProviderError:
    """Map SDK and transport failures to stable, sanitized provider errors."""

    if isinstance(exc, ProviderError):
        return exc

    error_type = type(exc).__name__
    details: dict[str, Any] = {"error_type": error_type}
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        details["status_code"] = status_code

    headers = getattr(response, "headers", None)
    retry_after = headers.get("retry-after") if headers is not None else None
    if retry_after is not None:
        try:
            details["retry_after_seconds"] = min(max(float(retry_after), 0.0), 60.0)
        except (TypeError, ValueError):
            pass

    if is_provider_cancellation(exc):
        return ProviderError(
            "PROVIDER_CANCELLED",
            f"The {provider_id} request was cancelled.",
            details=details,
        )

    timeout_names = {"APITimeoutError", "ReadTimeout", "WriteTimeout", "ConnectTimeout"}
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or error_type in timeout_names:
        if streaming and stream_started:
            return ProviderError(
                "PROVIDER_STREAM_TIMEOUT",
                f"The {provider_id} stream timed out after output began.",
                details=details,
            )
        return ProviderError(
            "PROVIDER_TIMEOUT",
            f"The {provider_id} request timed out before output began.",
            details=details,
        )

    if status_code == 429 or error_type in {"RateLimitError", "ResourceExhausted"}:
        return ProviderError(
            "PROVIDER_RATE_LIMITED",
            f"The {provider_id} provider rate-limited the request.",
            details=details,
        )

    transient_names = {
        "APIConnectionError",
        "ConnectError",
        "ConnectionError",
        "InternalServerError",
        "NetworkError",
        "RemoteProtocolError",
        "ServiceUnavailable",
    }
    if (
        isinstance(exc, (ConnectionError, httpx.NetworkError))
        or error_type in transient_names
        or (isinstance(status_code, int) and (status_code in {408, 409, 425} or status_code >= 500))
    ):
        return ProviderError(
            "PROVIDER_TRANSIENT",
            f"The {provider_id} provider is temporarily unavailable.",
            details=details,
        )

    operation = "stream" if streaming else "request"
    return ProviderError(
        "PROVIDER_REQUEST_FAILED",
        f"The {provider_id} {operation} failed.",
        details=details,
    )
