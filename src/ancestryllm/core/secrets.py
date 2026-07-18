"""One secret boundary backed by the operating-system credential store."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol, cast

from ancestryllm.core.errors import StorageError

KEYRING_SERVICE = "AncestryLLM"
ENVIRONMENT_NAMES = {
    "openai.api_key": "OPENAI_API_KEY",
    "anthropic.api_key": "ANTHROPIC_API_KEY",
    "gemini.api_key": "GEMINI_API_KEY",
    "openrouter.api_key": "OPENROUTER_API_KEY",
    "openrouter.management_key": "OPENROUTER_MANAGEMENT_KEY",
    "database.master_key": "ANCESTRYLLM_DATABASE_KEY",
}


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...
    def present(self, name: str) -> bool: ...


@dataclass(slots=True)
class KeyringSecretStore:
    """Prefer the OS keyring and accept environment injection as fallback."""

    service_name: str = KEYRING_SERVICE

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
            return cast(str, value)
        environment_name = ENVIRONMENT_NAMES.get(
            name, f"ANCESTRYLLM_SECRET_{name.upper().replace('.', '_')}"
        )
        environment_value = os.getenv(environment_name)
        if environment_value:
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


@dataclass(slots=True)
class MemorySecretStore:
    """Non-persistent store used only by tests."""

    values: dict[str, str]

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)

    def present(self, name: str) -> bool:
        return name in self.values
