"""Private one-shot capability registry for write-only secret submission."""

from __future__ import annotations

import secrets
import threading

from ancestryllm.application.dto import SecretGrantRef
from ancestryllm.core.errors import AncestryError


class SecretGrantRegistry:
    """Bind a credential to one opaque, single-use in-process capability."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    def issue(self, secret_name: str, value: str) -> SecretGrantRef:
        grant_id = f"sec_{secrets.token_hex(32)}"
        with self._lock:
            self._values[grant_id] = (secret_name, value)
        return SecretGrantRef(grant_id=grant_id, secret_name=secret_name)

    def consume(self, grant: SecretGrantRef, secret_name: str) -> str:
        with self._lock:
            bound = self._values.pop(grant.grant_id, None)
        if bound is None or bound[0] != secret_name or grant.secret_name != secret_name:
            raise AncestryError(
                "SECRET_GRANT_INVALID",
                "The one-time secret capability is missing, expired, or out of scope.",
                exit_code=2,
            )
        return bound[1]

    def revoke_all(self) -> None:
        with self._lock:
            self._values.clear()


__all__ = ["SecretGrantRegistry"]
