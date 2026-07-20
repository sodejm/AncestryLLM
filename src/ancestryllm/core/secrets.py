"""One secret boundary backed by the operating-system credential store."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from ancestryllm.core.errors import StorageError

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
    """Prefer the OS keyring and accept environment injection as fallback."""

    service_name: str = KEYRING_SERVICE
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
        keyring_error: Exception | None
        try:
            value = self._keyring().get_password(self.service_name, name)
        except Exception as exc:  # noqa: BLE001 - backends expose platform errors
            value = None
            keyring_error = exc
        else:
            keyring_error = None
        if value:
            secret_value = cast(str, value)
            self.register_sensitive(secret_value)
            return secret_value
        environment_name = ENVIRONMENT_NAMES.get(
            name, f"ANCESTRYLLM_SECRET_{name.upper().replace('.', '_')}"
        )
        environment_value = os.getenv(environment_name)
        if environment_value:
            self.register_sensitive(environment_value)
            return environment_value
        if keyring_error is not None:
            raise StorageError(
                "KEYRING_READ_FAILED",
                f"The OS keyring could not read secret reference {name!r}.",
                "Unlock or repair the OS credential store; never place the value on the command line.",
                details={"error_type": type(keyring_error).__name__},
            ) from keyring_error
        return None

    def set(self, name: str, value: str) -> None:
        if not value:
            raise StorageError("SECRET_EMPTY", "Empty secret values are not stored.")
        self.register_sensitive(value)
        try:
            self._keyring().set_password(self.service_name, name, value)
        except Exception as exc:
            raise StorageError(
                "KEYRING_WRITE_FAILED",
                f"The OS keyring could not store secret reference {name!r}.",
                "Unlock or configure a supported OS credential store.",
                details={"error_type": type(exc).__name__},
            ) from exc

    def delete(self, name: str) -> None:
        try:
            self._keyring().delete_password(self.service_name, name)
        except Exception as exc:
            error_name = type(exc).__name__
            if error_name not in {"PasswordDeleteError", "KeyringError"}:
                raise StorageError(
                    "KEYRING_DELETE_FAILED",
                    f"The OS keyring could not delete secret reference {name!r}.",
                    details={"error_type": error_name},
                ) from exc

    def present(self, name: str) -> bool:
        return self.get(name) is not None

    def register_sensitive(self, value: str) -> None:
        self._redactor.register(value)

    def redact(self, text: str) -> str:
        return self._redactor.redact(text)


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
