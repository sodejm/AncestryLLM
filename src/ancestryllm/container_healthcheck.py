"""Authenticated loopback health probes for gateway and optional worker images."""

from __future__ import annotations

import argparse
import http.client
import json
import sys
from typing import TYPE_CHECKING

from ancestryllm import __version__
from ancestryllm.api import API_BUILD_HEADER, API_CONTRACT, API_NAMESPACE, API_VERSION_HEADER
from ancestryllm.container_runtime import (
    PROBE_PORT,
    PROBE_TOKEN_PATH,
    WORKER_READY_PATH,
    ContainerRuntimeError,
    read_private_runtime_file,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_MAX_RESPONSE_BYTES = 16_384


def _gateway_request(*, version: str = API_CONTRACT, build: str = __version__) -> tuple[int, bytes]:
    token = read_private_runtime_file(PROBE_TOKEN_PATH)
    connection = http.client.HTTPConnection("127.0.0.1", PROBE_PORT, timeout=2)
    try:
        connection.request(
            "GET",
            f"{API_NAMESPACE}/health",
            headers={
                "Authorization": f"Bearer {token}",
                API_VERSION_HEADER: version,
                API_BUILD_HEADER: build,
                "Host": "127.0.0.1",
            },
        )
        response = connection.getresponse()
        body = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, http.client.HTTPException) as exc:
        raise ContainerRuntimeError(
            "CONTAINER_HEALTHCHECK_UNAVAILABLE",
            "The loopback health endpoint could not be reached.",
        ) from exc
    finally:
        connection.close()
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ContainerRuntimeError(
            "CONTAINER_HEALTHCHECK_INVALID",
            "The loopback health response exceeded its reviewed size limit.",
        )
    return response.status, body


def _decode_payload(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContainerRuntimeError(
            "CONTAINER_HEALTHCHECK_INVALID",
            "The loopback health response was malformed.",
        ) from exc
    if not isinstance(payload, dict):
        raise ContainerRuntimeError(
            "CONTAINER_HEALTHCHECK_INVALID",
            "The loopback health response was not an object.",
        )
    return payload


def check_gateway() -> None:
    """Probe the gateway health endpoint and return its status."""
    status, body = _gateway_request()
    if status != 200:
        raise ContainerRuntimeError(
            "CONTAINER_HEALTHCHECK_REJECTED",
            "The loopback health endpoint rejected the authenticated probe.",
        )
    payload = _decode_payload(body)
    if (
        payload.get("status") != "ready"
        or payload.get("app_build") != __version__
        or payload.get("sidecar_build") != __version__
    ):
        raise ContainerRuntimeError(
            "CONTAINER_HEALTHCHECK_MISMATCH",
            "The loopback health response did not match the running build.",
        )


def check_gateway_rejection(kind: str) -> None:
    """Prove version and build mismatches fail with their exact coded envelopes."""

    if kind == "version":
        status, body = _gateway_request(version="ancestryllm.internal-api/unsupported")
        expected = (400, "API_VERSION_UNSUPPORTED")
    elif kind == "build":
        status, body = _gateway_request(build="unsupported-build")
        expected = (409, "APP_BUILD_MISMATCH")
    else:
        raise ContainerRuntimeError(
            "CONTAINER_HEALTHCHECK_ARGUMENT_INVALID",
            "The requested rejection proof is unsupported.",
        )
    payload = _decode_payload(body)
    if (status, payload.get("code")) != expected:
        raise ContainerRuntimeError(
            "CONTAINER_HEALTHCHECK_FAIL_OPEN",
            "The loopback health endpoint did not reject an incompatible peer exactly.",
        )


def check_worker() -> None:
    """Probe the worker health endpoint and return its status."""
    marker = json.loads(read_private_runtime_file(WORKER_READY_PATH))
    if marker != {"build": __version__, "schema_version": 1, "status": "ready"}:
        raise ContainerRuntimeError(
            "CONTAINER_WORKER_HEALTHCHECK_MISMATCH",
            "The optional worker readiness marker did not match the running build.",
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the container healthcheck command and return its exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--expect-rejection", choices=("version", "build"))
    args = parser.parse_args(argv)
    if args.worker and args.expect_rejection is not None:
        parser.error("--worker and --expect-rejection cannot be combined")
    try:
        if args.worker:
            check_worker()
        elif args.expect_rejection is not None:
            check_gateway_rejection(args.expect_rejection)
        else:
            check_gateway()
    except (ContainerRuntimeError, json.JSONDecodeError):
        print("CONTAINER_HEALTHCHECK_FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by Docker HEALTHCHECK
    raise SystemExit(main())


__all__ = [
    "WORKER_READY_PATH",
    "check_gateway",
    "check_gateway_rejection",
    "check_worker",
    "main",
]
