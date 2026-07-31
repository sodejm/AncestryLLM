"""Immutable runtime inputs supplied by the future sidecar supervisor."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ancestryllm.api.contracts import RequestSizePolicy

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_BUILD_PATTERN = re.compile(r"^[A-Za-z0-9._:+-]{1,128}$")
_PROVIDER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Validated launch secret and exact paired build identity."""

    bearer_token: str = field(repr=False)
    app_build: str
    sidecar_build: str
    provider_id: str = "none"
    allowed_hosts: tuple[str, ...] = ("127.0.0.1",)
    request_policy: RequestSizePolicy = field(default_factory=RequestSizePolicy)

    def __post_init__(self) -> None:
        if _TOKEN_PATTERN.fullmatch(self.bearer_token) is None:
            raise ValueError("bearer_token must be a 256-bit-or-greater URL-safe value")
        for label, value in (("app_build", self.app_build), ("sidecar_build", self.sidecar_build)):
            if _BUILD_PATTERN.fullmatch(value) is None:
                raise ValueError(f"{label} must be a bounded build identity")
        if self.app_build != self.sidecar_build:
            raise ValueError("the control API requires matching app and sidecar builds")
        if _PROVIDER_PATTERN.fullmatch(self.provider_id) is None:
            raise ValueError("provider_id must be a bounded stable identifier")
        if not self.allowed_hosts or set(self.allowed_hosts) != {"127.0.0.1"}:
            raise ValueError("the internal API accepts the IPv4 loopback host only")


__all__ = ["ApiSettings"]
