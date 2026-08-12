"""Explicit, proxy-free endpoint probes for the desktop settings boundary."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ancestryllm.core.errors import SecurityPolicyError
from ancestryllm.llm.policy import endpoint_is_loopback, validate_endpoint

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class EndpointProbeRequest:
    """One direct connection plan with a DNS result pinned as a numeric address."""

    scheme: str
    hostname: str
    port: int
    target: str
    pinned_address: str


@dataclass(frozen=True, slots=True)
class EndpointProbeResponse:
    """Internal response facts; never cross the renderer boundary."""

    status_code: int
    peer_address: str


@dataclass(frozen=True, slots=True)
class EndpointValidationResult:
    """Sanitized endpoint-test result safe to expose to an untrusted renderer."""

    schema_version: int
    status: str
    endpoint_kind: str
    http_status: int
    destination_digest: str


def _resolve_addresses(hostname: str, port: int) -> tuple[str, ...]:
    addresses = {
        str(item[4][0])
        for item in socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    }
    return tuple(sorted(addresses))


def _host_header(hostname: str, scheme: str, port: int) -> str:
    formatted = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    return formatted if port == default_port else f"{formatted}:{port}"


def _direct_probe(request: EndpointProbeRequest) -> EndpointProbeResponse:
    """Send a bounded HEAD request to a numeric address without a proxy or redirect client."""

    connection = socket.create_connection(
        (request.pinned_address, request.port),
        timeout=5.0,
    )
    transport: socket.socket | ssl.SSLSocket = connection
    try:
        if request.scheme == "https":
            transport = ssl.create_default_context().wrap_socket(
                connection,
                server_hostname=request.hostname,
            )
        transport.settimeout(5.0)
        peer_address = str(transport.getpeername()[0])
        request_bytes = (
            f"HEAD {request.target} HTTP/1.1\r\n"
            f"Host: {_host_header(request.hostname, request.scheme, request.port)}\r\n"
            "User-Agent: ancestryllm-endpoint-check/1\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        transport.sendall(request_bytes)
        response = bytearray()
        while b"\r\n" not in response and len(response) < 4_096:
            chunk = transport.recv(min(1_024, 4_096 - len(response)))
            if not chunk:
                break
            response.extend(chunk)
        status_line = bytes(response).split(b"\r\n", maxsplit=1)[0]
        parts = status_line.split(b" ", maxsplit=2)
        if len(parts) < 2 or not parts[0].startswith(b"HTTP/"):
            raise ValueError("invalid HTTP status line")
        status_code = int(parts[1])
        if not 100 <= status_code <= 599:
            raise ValueError("invalid HTTP status code")
        return EndpointProbeResponse(status_code=status_code, peer_address=peer_address)
    finally:
        transport.close()


def _canonical_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    if not addresses:
        raise SecurityPolicyError(
            "ENDPOINT_RESOLUTION_REJECTED",
            "The endpoint did not resolve to an approved destination.",
        )
    try:
        return tuple(sorted({str(ipaddress.ip_address(address)) for address in addresses}))
    except ValueError as exc:
        raise SecurityPolicyError(
            "ENDPOINT_RESOLUTION_REJECTED",
            "The endpoint resolved to an invalid destination.",
        ) from exc


class EndpointValidationService:
    """Validate one reviewed endpoint through a direct, pinned network connection."""

    def __init__(
        self,
        *,
        resolver: Callable[[str, int], tuple[str, ...]] = _resolve_addresses,
        probe: Callable[[EndpointProbeRequest], EndpointProbeResponse] = _direct_probe,
    ) -> None:
        self._resolver = resolver
        self._probe = probe

    def validate(self, provider_id: str, endpoint: str) -> EndpointValidationResult:
        validate_endpoint(provider_id, endpoint)
        parsed = urlparse(endpoint)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.params
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise SecurityPolicyError(
                "ENDPOINT_REJECTED",
                "The endpoint contains unsupported URL components.",
            )

        loopback = endpoint_is_loopback(endpoint)
        if provider_id == "ollama" and not loopback:
            raise SecurityPolicyError(
                "ENDPOINT_REJECTED",
                "Desktop local-runtime endpoints must use an explicit loopback address.",
            )
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise SecurityPolicyError(
                "ENDPOINT_REJECTED",
                "The endpoint contains an invalid port.",
            ) from exc
        try:
            first_addresses = _canonical_addresses(self._resolver(parsed.hostname, port))
        except SecurityPolicyError:
            raise
        except (OSError, ValueError) as exc:
            raise SecurityPolicyError(
                "ENDPOINT_RESOLUTION_REJECTED",
                "The endpoint could not be resolved to an approved destination.",
                details={"error_type": type(exc).__name__},
            ) from exc

        parsed_addresses = tuple(ipaddress.ip_address(address) for address in first_addresses)
        destinations_approved = (
            all(address.is_loopback for address in parsed_addresses)
            if loopback
            else all(address.is_global for address in parsed_addresses)
        )
        if not destinations_approved:
            raise SecurityPolicyError(
                "ENDPOINT_RESOLUTION_REJECTED",
                "The endpoint resolved to a destination outside its approved network boundary.",
            )

        target = parsed.path or "/"
        request = EndpointProbeRequest(
            scheme=parsed.scheme,
            hostname=parsed.hostname,
            port=port,
            target=target,
            pinned_address=first_addresses[0],
        )
        try:
            response = self._probe(request)
        except (OSError, TimeoutError, ValueError, ssl.SSLError) as exc:
            raise SecurityPolicyError(
                "ENDPOINT_TEST_FAILED",
                "The endpoint could not be verified through a direct connection.",
                details={"error_type": type(exc).__name__},
            ) from exc

        try:
            peer_address = str(ipaddress.ip_address(response.peer_address))
        except ValueError as exc:
            raise SecurityPolicyError(
                "ENDPOINT_DESTINATION_CHANGED",
                "The endpoint connection did not use its pinned destination.",
            ) from exc
        if peer_address != request.pinned_address:
            raise SecurityPolicyError(
                "ENDPOINT_DESTINATION_CHANGED",
                "The endpoint connection did not use its pinned destination.",
            )
        if 300 <= response.status_code <= 399:
            raise SecurityPolicyError(
                "ENDPOINT_REDIRECT_REJECTED",
                "Endpoint redirects are not followed or accepted.",
            )

        try:
            second_addresses = _canonical_addresses(self._resolver(parsed.hostname, port))
        except SecurityPolicyError:
            raise
        except (OSError, ValueError) as exc:
            raise SecurityPolicyError(
                "ENDPOINT_DESTINATION_CHANGED",
                "The endpoint destination changed during validation.",
                details={"error_type": type(exc).__name__},
            ) from exc
        if second_addresses != first_addresses:
            raise SecurityPolicyError(
                "ENDPOINT_DESTINATION_CHANGED",
                "The endpoint destination changed during validation.",
            )

        digest_payload = "\n".join((provider_id, endpoint, *first_addresses)).encode()
        return EndpointValidationResult(
            schema_version=1,
            status="reachable",
            endpoint_kind="loopback" if loopback else "remote",
            http_status=response.status_code,
            destination_digest=hashlib.sha256(digest_payload).hexdigest(),
        )
