"""Constant-time, pre-parse authentication for the private loopback API."""

from __future__ import annotations

import hmac
from typing import Final, cast

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ancestryllm.api.contracts import (
    API_BUILD_HEADER,
    API_CONTRACT,
    API_NAMESPACE,
    API_VERSION_HEADER,
)
from ancestryllm.api.errors import (
    ApiRequestError,
    error_response,
    new_correlation_ref,
    request_error,
)
from ancestryllm.api.settings import ApiSettings

_ALLOWED_ROUTES: Final = frozenset({f"{API_NAMESPACE}/health", f"{API_NAMESPACE}/capabilities"})
_FORBIDDEN_REQUEST_HEADERS: Final = frozenset(
    {
        b"cookie",
        b"forwarded",
        b"origin",
        b"proxy-authorization",
        b"proxy-connection",
        b"referer",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
        b"via",
    }
)
_REMOVED_RESPONSE_HEADERS: Final = frozenset(
    {
        b"access-control-allow-credentials",
        b"access-control-allow-headers",
        b"access-control-allow-methods",
        b"access-control-allow-origin",
        b"set-cookie",
    }
)
_SECURITY_HEADERS: Final = (
    (b"cache-control", b"no-store"),
    (b"pragma", b"no-cache"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
)


def _header_map(scope: Scope) -> dict[bytes, list[bytes]]:
    raw_headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
    headers: dict[bytes, list[bytes]] = {}
    for raw_name, value in raw_headers:
        headers.setdefault(raw_name.lower(), []).append(value)
    return headers


def _one_header(headers: dict[bytes, list[bytes]], name: bytes) -> bytes | None:
    values = headers.get(name, [])
    if len(values) > 1:
        raise request_error(
            400, "REQUEST_HEADER_INVALID", "A security-sensitive request header was repeated."
        )
    return values[0] if values else None


def _host_allowed(raw_host: bytes | None, allowed_hosts: tuple[str, ...]) -> bool:
    if raw_host is None:
        return False
    try:
        host = raw_host.decode("ascii")
    except UnicodeDecodeError:
        return False
    for allowed in allowed_hosts:
        if host == allowed:
            return True
        prefix = f"{allowed}:"
        if host.startswith(prefix):
            port = host[len(prefix) :]
            return port.isdigit() and 0 < int(port) <= 65_535
    return False


def _authenticate(headers: dict[bytes, list[bytes]], settings: ApiSettings) -> None:
    authorization = _one_header(headers, b"authorization")
    expected = f"Bearer {settings.bearer_token}".encode()
    if not hmac.compare_digest(authorization if authorization is not None else b"", expected):
        raise request_error(
            401,
            "AUTHENTICATION_REQUIRED",
            "The internal API bearer is missing or invalid.",
        )


def _validate_request(scope: Scope, settings: ApiSettings) -> None:
    headers = _header_map(scope)
    _authenticate(headers, settings)

    if not _host_allowed(_one_header(headers, b"host"), settings.allowed_hosts):
        raise request_error(
            400, "REQUEST_HOST_INVALID", "The request Host is not the configured loopback listener."
        )
    if any(
        name in _FORBIDDEN_REQUEST_HEADERS or name.startswith(b"x-forwarded-") for name in headers
    ):
        raise request_error(
            400,
            "REQUEST_HEADER_FORBIDDEN",
            "Browser, cookie, or proxy forwarding headers are not accepted.",
        )

    version = _one_header(headers, API_VERSION_HEADER.casefold().encode())
    if version is None or not hmac.compare_digest(version, API_CONTRACT.encode()):
        raise request_error(
            400, "API_VERSION_UNSUPPORTED", "The internal API contract version is unsupported."
        )
    build = _one_header(headers, API_BUILD_HEADER.casefold().encode())
    if build is None or not hmac.compare_digest(build, settings.app_build.encode()):
        raise request_error(
            409, "APP_BUILD_MISMATCH", "The desktop and sidecar build identities do not match."
        )

    path = cast(str, scope.get("path", ""))
    if path not in _ALLOWED_ROUTES:
        raise request_error(
            404, "ROUTE_UNAVAILABLE", "The requested internal API route is unavailable."
        )
    if scope.get("method") != "GET":
        raise request_error(
            405, "METHOD_NOT_ALLOWED", "The internal API route does not accept this method."
        )
    if cast(bytes, scope.get("query_string", b"")):
        raise request_error(
            400,
            "REQUEST_QUERY_FORBIDDEN",
            "The internal API control routes do not accept query parameters.",
        )
    if b"content-type" in headers:
        raise request_error(
            415,
            "REQUEST_CONTENT_TYPE_FORBIDDEN",
            "The internal API control routes do not accept a content type.",
        )

    raw_length = _one_header(headers, b"content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise request_error(
                400, "REQUEST_SIZE_INVALID", "The request content length is invalid."
            ) from exc
        if content_length < 0:
            raise request_error(
                400, "REQUEST_SIZE_INVALID", "The request content length is invalid."
            )
        if content_length > settings.request_policy.max_body_bytes:
            raise request_error(
                413, "REQUEST_TOO_LARGE", "The request exceeds the internal API size limit."
            )
        if content_length:
            raise request_error(
                400,
                "REQUEST_BODY_FORBIDDEN",
                "The internal API control routes do not accept a request body.",
            )


async def _verify_empty_body(receive: Receive) -> Receive:
    first_message = await receive()
    if first_message["type"] == "http.request" and (
        first_message.get("body", b"") or first_message.get("more_body", False)
    ):
        raise request_error(
            400,
            "REQUEST_BODY_FORBIDDEN",
            "The internal API control routes do not accept a request body.",
        )

    replayed = False

    async def replay_first_message() -> Message:
        nonlocal replayed
        if not replayed:
            replayed = True
            return first_message
        return await receive()

    return replay_first_message


class InternalApiMiddleware:
    def __init__(self, app: ASGIApp, *, settings: ApiSettings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        correlation_ref = new_correlation_ref()
        state = cast(dict[str, object], scope.setdefault("state", {}))
        state["correlation_ref"] = correlation_ref
        secured_send = self._secured_send(send, correlation_ref)
        try:
            _validate_request(scope, self._settings)
            receive = await _verify_empty_body(receive)
        except ApiRequestError as error:
            await error_response(error, correlation_ref=correlation_ref)(
                scope, receive, secured_send
            )
            return

        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await secured_send(message)

        try:
            await self._app(scope, receive, tracked_send)
        except Exception as error:
            if response_started:
                raise
            await error_response(error, correlation_ref=correlation_ref)(
                scope, receive, secured_send
            )

    @staticmethod
    def _secured_send(send: Send, correlation_ref: str) -> Send:
        async def secured(message: Message) -> None:
            if message.get("type") == "http.response.start":
                raw_headers = cast(list[tuple[bytes, bytes]], message.get("headers", []))
                replaced = (
                    {name for name, _value in _SECURITY_HEADERS}
                    | _REMOVED_RESPONSE_HEADERS
                    | {b"x-correlation-ref"}
                )
                headers = [
                    (name, value) for name, value in raw_headers if name.lower() not in replaced
                ]
                headers.extend(_SECURITY_HEADERS)
                headers.append((b"x-correlation-ref", correlation_ref.encode()))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        return secured


__all__ = ["InternalApiMiddleware"]
