"""Versioned, non-secret deployment-profile configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from ancestryllm.core.errors import ConfigurationError

DEPLOYMENT_SCHEMA_VERSION = 1
_PROFILE_KEYS = {
    "schema_version",
    "mode",
    "topology",
    "endpoint_origin",
    "endpoint_identity_sha256",
}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class DeploymentMode(StrEnum):
    """Persistent deployment choices shared by every adapter."""

    LOCAL_DESKTOP = "local-desktop"
    CONNECT_REMOTE = "connect-remote"
    HOST_REMOTE_SERVER = "host-remote-server"


class DeploymentTopology(StrEnum):
    """Expected runtime topology for a deployment mode."""

    LOCAL_ONLY = "local-only"
    REMOTE_CLIENT = "remote-client"
    REMOTE_HOST = "remote-host"


_EXPECTED_TOPOLOGY = {
    DeploymentMode.LOCAL_DESKTOP: DeploymentTopology.LOCAL_ONLY,
    DeploymentMode.CONNECT_REMOTE: DeploymentTopology.REMOTE_CLIENT,
    DeploymentMode.HOST_REMOTE_SERVER: DeploymentTopology.REMOTE_HOST,
}


def _profile_error(message: str, *, field: str | None = None) -> ConfigurationError:
    details = {"field": field} if field is not None else {}
    return ConfigurationError(
        "DEPLOYMENT_PROFILE_INVALID",
        message,
        "Restore a reviewed deployment profile or select Local Desktop explicitly.",
        exit_code=2,
        details=details,
    )


def normalize_endpoint_origin(value: object) -> str:
    """Return one canonical HTTPS origin without accepting embedded authority."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\\" in value
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise _profile_error("The deployment endpoint origin is invalid.", field="endpoint_origin")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise _profile_error(
            "The deployment endpoint origin is invalid.", field="endpoint_origin"
        ) from exc
    hostname = parsed.hostname
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not hostname.isascii()
        or "%" in hostname
        or any(character.isspace() or ord(character) < 32 for character in hostname)
        or (port is not None and port < 1)
    ):
        raise _profile_error("The deployment endpoint origin is invalid.", field="endpoint_origin")
    normalized_host = hostname.lower()
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    port_suffix = "" if port in {None, 443} else f":{port}"
    return f"https://{normalized_host}{port_suffix}"


def normalize_endpoint_identity(value: object) -> str:
    """Validate one reviewed endpoint-identity SHA-256 digest."""

    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise _profile_error(
            "The deployment endpoint identity is invalid.",
            field="endpoint_identity_sha256",
        )
    return value


@dataclass(frozen=True, slots=True)
class DeploymentProfile:
    """One schema-v1 deployment choice with no credentials or local paths."""

    schema_version: int
    mode: DeploymentMode
    topology: DeploymentTopology
    endpoint_origin: str | None = None
    endpoint_identity_sha256: str | None = None

    @classmethod
    def local(cls) -> DeploymentProfile:
        return cls(
            DEPLOYMENT_SCHEMA_VERSION,
            DeploymentMode.LOCAL_DESKTOP,
            DeploymentTopology.LOCAL_ONLY,
        )

    @classmethod
    def connect_remote(
        cls,
        *,
        endpoint_origin: object,
        endpoint_identity_sha256: object,
    ) -> DeploymentProfile:
        return cls(
            DEPLOYMENT_SCHEMA_VERSION,
            DeploymentMode.CONNECT_REMOTE,
            DeploymentTopology.REMOTE_CLIENT,
            normalize_endpoint_origin(endpoint_origin),
            normalize_endpoint_identity(endpoint_identity_sha256),
        )

    @classmethod
    def host_remote_server(cls) -> DeploymentProfile:
        return cls(
            DEPLOYMENT_SCHEMA_VERSION,
            DeploymentMode.HOST_REMOTE_SERVER,
            DeploymentTopology.REMOTE_HOST,
        )

    @classmethod
    def from_mapping(cls, value: object) -> DeploymentProfile:
        """Parse one complete profile and reject omissions or schema drift."""

        if not isinstance(value, Mapping):
            raise _profile_error("The deployment profile must be a table.")
        unknown = set(value) - _PROFILE_KEYS
        if unknown:
            raise _profile_error("The deployment profile contains an unsupported field.")
        schema_version = value.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != DEPLOYMENT_SCHEMA_VERSION
        ):
            raise _profile_error(
                "The deployment profile schema version is unsupported.",
                field="schema_version",
            )
        raw_mode = value.get("mode")
        raw_topology = value.get("topology")
        if not isinstance(raw_mode, str) or not isinstance(raw_topology, str):
            raise _profile_error("The deployment mode or topology is invalid.")
        try:
            mode = DeploymentMode(raw_mode)
            topology = DeploymentTopology(raw_topology)
        except (TypeError, ValueError) as exc:
            raise _profile_error("The deployment mode or topology is invalid.") from exc
        if topology is not _EXPECTED_TOPOLOGY[mode]:
            raise _profile_error("The deployment mode and topology do not match.")
        endpoint_origin = value.get("endpoint_origin")
        endpoint_identity = value.get("endpoint_identity_sha256")
        if mode is DeploymentMode.CONNECT_REMOTE:
            return cls.connect_remote(
                endpoint_origin=endpoint_origin,
                endpoint_identity_sha256=endpoint_identity,
            )
        if endpoint_origin is not None or endpoint_identity is not None:
            raise _profile_error("This deployment mode cannot contain a remote endpoint.")
        if mode is DeploymentMode.HOST_REMOTE_SERVER:
            return cls.host_remote_server()
        return cls.local()

    def to_mapping(self) -> dict[str, object]:
        """Serialize the exact schema-v1 TOML contract deterministically."""

        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "mode": self.mode.value,
            "topology": self.topology.value,
        }
        if self.endpoint_origin is not None:
            payload["endpoint_origin"] = self.endpoint_origin
        if self.endpoint_identity_sha256 is not None:
            payload["endpoint_identity_sha256"] = self.endpoint_identity_sha256
        return payload


__all__ = [
    "DEPLOYMENT_SCHEMA_VERSION",
    "DeploymentMode",
    "DeploymentProfile",
    "DeploymentTopology",
    "normalize_endpoint_identity",
    "normalize_endpoint_origin",
]
