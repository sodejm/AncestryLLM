"""Constant-time, pre-parse authentication for the private loopback API."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast

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

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from ancestryllm.api.settings import ApiSettings

_SECRET_ROUTE = re.compile(
    rf"^{re.escape(API_NAMESPACE)}/secrets/[a-z0-9_.-]{{1,96}}/(status|set|delete)$"
)
_CONSENT_REVOKE_ROUTE = re.compile(
    rf"^{re.escape(API_NAMESPACE)}/consents/[A-Za-z0-9][A-Za-z0-9._~-]{{0,199}}/revoke$"
)
_JOB_ROUTE = re.compile(
    rf"^{re.escape(API_NAMESPACE)}/jobs/[A-Za-z0-9][A-Za-z0-9._~-]{{0,31}}"
    r"(?P<operation>/cancel|/events)?$"
)
_CHAT_SESSION_ROUTE = re.compile(
    rf"^{re.escape(API_NAMESPACE)}/chat/sessions/chat_[0-9a-f]{{32}}(?P<operation>/runs)?$"
)
_CHAT_STREAM_ROUTE = re.compile(
    rf"^{re.escape(API_NAMESPACE)}/chat/sessions/chat_[0-9a-f]{{32}}/streams"
    r"(?:/run_[0-9a-f]{32}(?P<operation>/events|/cancel))?$"
)
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


@dataclass(frozen=True, slots=True)
class _RoutePolicy:
    method: str
    accepts_json: bool = False


def _route_policy(
    path: str,
    surface: Literal["control", "probe"],
    runtime_shutdown_enabled: bool,
) -> _RoutePolicy | None:
    if path in {
        f"{API_NAMESPACE}/health",
        f"{API_NAMESPACE}/capabilities",
    }:
        return _RoutePolicy("GET")
    if surface == "probe":
        return None
    if path == f"{API_NAMESPACE}/startup-diagnostics":
        return _RoutePolicy("GET")
    if path == f"{API_NAMESPACE}/chat/capability":
        return _RoutePolicy("GET")
    if path == f"{API_NAMESPACE}/chat/sessions":
        return _RoutePolicy("POST", accepts_json=True)
    chat_stream_match = _CHAT_STREAM_ROUTE.fullmatch(path)
    if chat_stream_match is not None:
        operation = chat_stream_match.group("operation")
        if operation == "/events":
            return _RoutePolicy("GET")
        if operation == "/cancel":
            return _RoutePolicy("POST")
        return _RoutePolicy("POST", accepts_json=True)
    chat_match = _CHAT_SESSION_ROUTE.fullmatch(path)
    if chat_match is not None:
        if chat_match.group("operation") == "/runs":
            return _RoutePolicy("POST", accepts_json=True)
        return _RoutePolicy("GET")
    if path == f"{API_NAMESPACE}/jobs":
        return _RoutePolicy("GET")
    if path == f"{API_NAMESPACE}/jobs/shutdown":
        return _RoutePolicy("POST", accepts_json=True)
    if runtime_shutdown_enabled and path == f"{API_NAMESPACE}/runtime/shutdown":
        return _RoutePolicy("POST")
    job_match = _JOB_ROUTE.fullmatch(path)
    if job_match is not None:
        operation = job_match.group("operation")
        return _RoutePolicy("POST" if operation == "/cancel" else "GET")
    if path == f"{API_NAMESPACE}/settings":
        return _RoutePolicy("PATCH", accepts_json=True)
    if path == f"{API_NAMESPACE}/provider-configuration":
        return _RoutePolicy("GET")
    if path in {
        f"{API_NAMESPACE}/provider-profiles",
        f"{API_NAMESPACE}/provider-endpoints/validate",
        f"{API_NAMESPACE}/consents/preview",
        f"{API_NAMESPACE}/consents",
    }:
        return _RoutePolicy("POST", accepts_json=True)
    if _CONSENT_REVOKE_ROUTE.fullmatch(path) is not None:
        return _RoutePolicy("POST", accepts_json=True)
    match = _SECRET_ROUTE.fullmatch(path)
    if match is None:
        return None
    operation = match.group(1)
    if operation == "status":
        return _RoutePolicy("GET")
    if operation == "set":
        return _RoutePolicy("POST", accepts_json=True)
    return _RoutePolicy("POST")


def _header_map(scope: Scope) -> dict[bytes, list[bytes]]:
    raw_headers = cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
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


def _validate_request(
    scope: Scope,
    settings: ApiSettings,
    surface: Literal["control", "probe"],
    runtime_shutdown_enabled: bool,
) -> _RoutePolicy:
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

    path = cast("str", scope.get("path", ""))
    policy = _route_policy(path, surface, runtime_shutdown_enabled)
    if policy is None:
        raise request_error(
            404, "ROUTE_UNAVAILABLE", "The requested internal API route is unavailable."
        )
    method = cast("str", scope.get("method", ""))
    if path == f"{API_NAMESPACE}/settings" and method == "GET":
        policy = _RoutePolicy("GET")
    if _CHAT_SESSION_ROUTE.fullmatch(path) is not None and method == "DELETE":
        policy = _RoutePolicy("DELETE")
    if method != policy.method:
        raise request_error(
            405, "METHOD_NOT_ALLOWED", "The internal API route does not accept this method."
        )
    chat_stream_match = _CHAT_STREAM_ROUTE.fullmatch(path)
    if chat_stream_match is not None and chat_stream_match.group("operation") == "/events":
        _one_header(headers, b"last-event-id")
    if cast("bytes", scope.get("query_string", b"")):
        raise request_error(
            400,
            "REQUEST_QUERY_FORBIDDEN",
            "The internal API control routes do not accept query parameters.",
        )
    content_type = _one_header(headers, b"content-type")
    if policy.accepts_json:
        if (
            content_type is None
            or content_type.split(b";", 1)[0].strip().lower() != b"application/json"
        ):
            raise request_error(
                415,
                "REQUEST_CONTENT_TYPE_REQUIRED",
                "The internal API route accepts only JSON request bodies.",
            )
    elif content_type is not None:
        raise request_error(
            415,
            "REQUEST_CONTENT_TYPE_FORBIDDEN",
            "The internal API route does not accept a content type.",
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
        if content_length and not policy.accepts_json:
            raise request_error(
                400,
                "REQUEST_BODY_FORBIDDEN",
                "The internal API route does not accept a request body.",
            )
    return policy


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


async def _buffer_bounded_body(receive: Receive, *, maximum_bytes: int) -> Receive:
    messages: list[Message] = []
    total = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        total += len(cast("bytes", message.get("body", b"")))
        if total > maximum_bytes:
            raise request_error(
                413,
                "REQUEST_TOO_LARGE",
                "The request exceeds the internal API size limit.",
            )
        if not message.get("more_body", False):
            break

    index = 0

    async def replay_messages() -> Message:
        nonlocal index
        if index < len(messages):
            message = messages[index]
            index += 1
            return message
        return await receive()

    return replay_messages


class InternalApiMiddleware:
    """Enforce internal API origin, host, request-size, and response-header policy."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: ApiSettings,
        surface: Literal["control", "probe"] = "control",
        runtime_shutdown_enabled: bool = False,
    ) -> None:
        self._app = app
        self._settings = settings
        self._surface = surface
        self._runtime_shutdown_enabled = runtime_shutdown_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Apply internal request policy before delegating to the wrapped ASGI app."""
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        correlation_ref = new_correlation_ref()
        state = cast("dict[str, object]", scope.setdefault("state", {}))
        state["correlation_ref"] = correlation_ref
        secured_send = self._secured_send(send, correlation_ref)
        try:
            policy = _validate_request(
                scope,
                self._settings,
                self._surface,
                self._runtime_shutdown_enabled,
            )
            if policy.accepts_json:
                receive = await _buffer_bounded_body(
                    receive,
                    maximum_bytes=self._settings.request_policy.max_body_bytes,
                )
            else:
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
                raw_headers = cast("list[tuple[bytes, bytes]]", message.get("headers", []))
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
