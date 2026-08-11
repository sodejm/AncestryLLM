"""Write-only secret management over the single configured secret store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ancestryllm.core.errors import StorageError
from ancestryllm.domain.secrets import SUPPORTED_SECRET_REFERENCES


class SecretStorePort(Protocol):
    """Write-only credential capabilities required by the application service."""

    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...
    def present(self, name: str) -> bool: ...
    def register_sensitive(self, value: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SecretStatus:
    """A bounded status that can never contain credential material."""

    reference: str
    status: Literal["present", "missing", "unavailable"]


def _validate_reference(reference: str) -> None:
    if reference not in SUPPORTED_SECRET_REFERENCES:
        raise StorageError(
            "SECRET_REFERENCE_UNKNOWN",
            "The secret reference is not supported.",
        )


class SecretManagementService:
    """Expose only status, set, and verified delete operations."""

    def __init__(self, store: SecretStorePort) -> None:
        self._store = store

    def status(self, reference: str) -> SecretStatus:
        _validate_reference(reference)
        try:
            present = self._store.present(reference)
        except StorageError:
            return SecretStatus(reference=reference, status="unavailable")
        return SecretStatus(reference=reference, status="present" if present else "missing")

    def set(self, reference: str, value: str) -> SecretStatus:
        _validate_reference(reference)
        self._store.register_sensitive(value)
        self._store.set(reference, value)
        return SecretStatus(reference=reference, status="present")

    def delete(self, reference: str) -> SecretStatus:
        _validate_reference(reference)
        self._store.delete(reference)
        if self._store.present(reference):
            raise StorageError(
                "KEYRING_DELETE_UNVERIFIED",
                "Credential deletion could not be verified.",
            )
        return SecretStatus(reference=reference, status="missing")
