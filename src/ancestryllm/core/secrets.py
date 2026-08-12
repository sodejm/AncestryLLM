"""One secret boundary backed by the operating-system credential store."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from hmac import compare_digest
from typing import Any, Protocol, cast

from ancestryllm.core.errors import StorageError
from ancestryllm.domain.secrets import SUPPORTED_SECRET_REFERENCES

KEYRING_SERVICE = "AncestryLLM"
REDACTED_VALUE = "[REDACTED]"
ENVIRONMENT_NAMES = {
    "openai.api_key": "OPENAI_API_KEY",
    "anthropic.api_key": "ANTHROPIC_API_KEY",
    "gemini.api_key": "GEMINI_API_KEY",
    "openrouter.api_key": "OPENROUTER_API_KEY",
    "openrouter.management_key": "OPENROUTER_MANAGEMENT_KEY",
    "database.master_key": "ANCESTRYLLM_DATABASE_KEY",
}
if frozenset(ENVIRONMENT_NAMES) != SUPPORTED_SECRET_REFERENCES:
    raise RuntimeError("Secret reference configuration is inconsistent.")


class SecretSourceMode(StrEnum):
    """Explicit secret-source policy for one application boundary."""

    KEYRING_WITH_ENVIRONMENT_FALLBACK = "keyring-with-environment-fallback"
    KEYRING_ONLY = "keyring-only"


@dataclass(slots=True)
class SensitiveValueRedactor:
    """Keep process-local secret values available only for output scrubbing."""

    _values: set[str] = field(default_factory=set, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def register(self, value: str) -> None:
        """Register a non-empty value without persisting or rendering it."""
        if value:
            with self._lock:
                self._values.add(value)

    def redact(self, text: str) -> str:
        """Replace registered values, longest first, with a stable marker."""
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            text = text.replace(value, REDACTED_VALUE)
        return text


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...
    def present(self, name: str) -> bool: ...
    def register_sensitive(self, value: str) -> None: ...
    def redact(self, text: str) -> str: ...


@dataclass(slots=True)
class KeyringSecretStore:
    """Use the OS keyring with an explicit, boundary-owned fallback policy."""

    service_name: str = KEYRING_SERVICE
    source_mode: SecretSourceMode = SecretSourceMode.KEYRING_WITH_ENVIRONMENT_FALLBACK
    _redactor: SensitiveValueRedactor = field(
        default_factory=SensitiveValueRedactor, init=False, repr=False
    )

    @staticmethod
    def _keyring() -> Any:
        try:
            import keyring
        except ImportError as exc:  # pragma: no cover - packaging contract
            raise StorageError(
                "KEYRING_UNAVAILABLE",
                "The operating-system keyring integration is unavailable.",
                "Install the core package and configure a supported OS keyring backend.",
            ) from exc
        return keyring

    def get(self, name: str) -> str | None:
        environment_name = self._environment_name(name)
        keyring_error: Exception | None
        try:
            value = self._keyring().get_password(self.service_name, name)
        except Exception as exc:  # noqa: BLE001 - backends expose platform errors
            value = None
            keyring_error = exc
        else:
            keyring_error = None
        if value:
            secret_value = cast("str", value)
            self.register_sensitive(secret_value)
            return secret_value
        if self.source_mode is SecretSourceMode.KEYRING_WITH_ENVIRONMENT_FALLBACK:
            environment_value = os.getenv(environment_name)
            if environment_value:
                self.register_sensitive(environment_value)
                return environment_value
        if keyring_error is not None:
            raise StorageError(
                "KEYRING_READ_FAILED",
                "The OS keyring could not read the requested credential.",
                "Unlock or repair the OS credential store; never place the value on the command line.",
            ) from keyring_error
        return None

    def set(self, name: str, value: str) -> None:
        environment_name = self._environment_name(name)
        if self.source_mode is SecretSourceMode.KEYRING_WITH_ENVIRONMENT_FALLBACK:
            self._reject_environment_managed(environment_name)
        if not value:
            raise StorageError("SECRET_EMPTY", "Empty secret values are not stored.")
        self.register_sensitive(value)
        try:
            keyring = self._keyring()
            keyring.set_password(self.service_name, name, value)
            stored = keyring.get_password(self.service_name, name)
        except Exception as exc:
            raise StorageError(
                "KEYRING_WRITE_UNVERIFIED",
                "The OS keyring could not verify the credential write.",
                "Unlock or configure a supported OS credential store.",
            ) from exc
        if not isinstance(stored, str) or not compare_digest(stored, value):
            raise StorageError(
                "KEYRING_WRITE_UNVERIFIED",
                "The OS keyring could not verify the credential write.",
            )

    def delete(self, name: str) -> None:
        environment_name = self._environment_name(name)
        if self.source_mode is SecretSourceMode.KEYRING_WITH_ENVIRONMENT_FALLBACK:
            self._reject_environment_managed(environment_name)
        try:
            keyring = self._keyring()
            existing = keyring.get_password(self.service_name, name)
        except Exception as exc:
            raise StorageError(
                "KEYRING_READ_FAILED",
                "The OS keyring could not read the requested credential.",
            ) from exc
        if existing is None:
            return
        delete_error: Exception | None = None
        try:
            keyring.delete_password(self.service_name, name)
        except Exception as exc:  # noqa: BLE001 - deletion must be verified below
            delete_error = exc
        try:
            remaining = keyring.get_password(self.service_name, name)
        except Exception as exc:
            raise StorageError(
                "KEYRING_DELETE_UNVERIFIED",
                "Credential deletion could not be verified.",
            ) from exc
        if remaining is not None:
            raise StorageError(
                "KEYRING_DELETE_UNVERIFIED",
                "Credential deletion could not be verified.",
            ) from delete_error

    def present(self, name: str) -> bool:
        return self.get(name) is not None

    def register_sensitive(self, value: str) -> None:
        self._redactor.register(value)

    def redact(self, text: str) -> str:
        return self._redactor.redact(text)

    @staticmethod
    def _environment_name(name: str) -> str:
        try:
            return ENVIRONMENT_NAMES[name]
        except KeyError as exc:
            raise StorageError(
                "SECRET_REFERENCE_UNKNOWN",
                "The secret reference is not supported.",
            ) from exc

    @staticmethod
    def _reject_environment_managed(environment_name: str) -> None:
        if os.getenv(environment_name):
            raise StorageError(
                "SECRET_ENVIRONMENT_MANAGED",
                "Environment-injected credentials are read-only.",
                "Remove the environment injection before changing this credential.",
            )


@dataclass(slots=True)
class MemorySecretStore:
    """Non-persistent store used only by tests."""

    values: dict[str, str]
    _redactor: SensitiveValueRedactor = field(
        default_factory=SensitiveValueRedactor, init=False, repr=False
    )

    def __post_init__(self) -> None:
        for value in self.values.values():
            self.register_sensitive(value)

    def get(self, name: str) -> str | None:
        value = self.values.get(name)
        if value:
            self.register_sensitive(value)
        return value

    def set(self, name: str, value: str) -> None:
        self.register_sensitive(value)
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)

    def present(self, name: str) -> bool:
        return name in self.values

    def register_sensitive(self, value: str) -> None:
        self._redactor.register(value)

    def redact(self, text: str) -> str:
        return self._redactor.redact(text)
