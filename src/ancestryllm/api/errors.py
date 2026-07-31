"""Safe HTTP translation for request-boundary and application failures."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi.responses import JSONResponse

from ancestryllm.api.contracts import ErrorEnvelope, FailureDetail
from ancestryllm.application.errors import domain_failure_from_exception, map_domain_failure
from ancestryllm.application.errors import error_envelope as application_error_envelope


@dataclass(frozen=True, slots=True)
class ApiRequestError(Exception):
    status_code: int
    code: str
    message: str
    remediation: str | None = None


def new_correlation_ref() -> str:
    return f"api_{secrets.token_hex(16)}"


def _http_status(code: str) -> int:
    if code in {"REQUEST_INVALID", "ARTIFACT_INVALID", "ARTIFACT_TOO_LARGE"}:
        return 400
    if code == "RESOURCE_NOT_FOUND":
        return 404
    if code in {
        "DECISION_REQUIRED",
        "GENEALOGY_CONFLICT",
        "IDENTITY_AMBIGUOUS",
        "QUALITY_RESOLUTION_REJECTED",
    }:
        return 409
    if code in {"ARTIFACT_FORBIDDEN", "PROVIDER_CONSENT_REQUIRED"}:
        return 403
    if code == "OPERATION_CANCELLED":
        return 409
    if code == "PROVIDER_UNAVAILABLE":
        return 503
    return 500


def error_envelope(error: Exception, *, correlation_ref: str) -> tuple[int, ErrorEnvelope]:
    if isinstance(error, ApiRequestError):
        return error.status_code, ErrorEnvelope(
            code=error.code,
            message=error.message,
            remediation=error.remediation,
            correlation_ref=correlation_ref,
        )
    sanitized = map_domain_failure(domain_failure_from_exception(error))
    boundary = application_error_envelope(sanitized, correlation_ref=correlation_ref)
    return _http_status(boundary.code), ErrorEnvelope(
        code=boundary.code,
        message=boundary.message,
        remediation=boundary.remediation,
        correlation_ref=correlation_ref,
        details=tuple(
            FailureDetail(name=detail.name, value=detail.value) for detail in boundary.details
        ),
    )


def error_response(error: Exception, *, correlation_ref: str | None = None) -> JSONResponse:
    resolved_ref = correlation_ref or new_correlation_ref()
    status_code, envelope = error_envelope(error, correlation_ref=resolved_ref)
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def request_error(
    status_code: int, code: str, message: str, remediation: str | None = None
) -> ApiRequestError:
    return ApiRequestError(status_code, code, message, remediation)


__all__ = [
    "ApiRequestError",
    "error_envelope",
    "error_response",
    "new_correlation_ref",
    "request_error",
]
