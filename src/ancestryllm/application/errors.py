"""Central mapping from pure domain failures to stable application errors."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ancestryllm.application.dto import ErrorEnvelope, FailureDetail
from ancestryllm.core.errors import AncestryError
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode


@dataclass(frozen=True, slots=True)
class ErrorMapping:
    """One stable, sanitized transport-facing failure mapping."""

    code: str
    message: str
    remediation: str | None
    exit_code: int


DOMAIN_ERROR_MAPPINGS: Mapping[DomainFailureCode, ErrorMapping] = MappingProxyType(
    {
        DomainFailureCode.CANCELLED: ErrorMapping(
            "OPERATION_CANCELLED",
            "The operation was cancelled at a safe boundary.",
            None,
            130,
        ),
        DomainFailureCode.INVALID_REQUEST: ErrorMapping(
            "REQUEST_INVALID",
            "The operation request is invalid.",
            "Review the supplied options and try again.",
            2,
        ),
        DomainFailureCode.NOT_FOUND: ErrorMapping(
            "RESOURCE_NOT_FOUND",
            "The requested application resource was not found.",
            "Refresh available resources and choose an existing item.",
            2,
        ),
        DomainFailureCode.CONFLICT: ErrorMapping(
            "GENEALOGY_CONFLICT",
            "The genealogy operation found a conflict that requires resolution.",
            "Review the conflict choices before retrying.",
            2,
        ),
        DomainFailureCode.DECISION_REQUIRED: ErrorMapping(
            "DECISION_REQUIRED",
            "The operation requires an explicit user decision.",
            "Choose one of the declared safe options.",
            2,
        ),
        DomainFailureCode.IDENTITY_AMBIGUOUS: ErrorMapping(
            "IDENTITY_AMBIGUOUS",
            "The genealogy identity could not be resolved deterministically.",
            "Select a candidate, create a new identity, or cancel.",
            2,
        ),
        DomainFailureCode.QUALITY_REJECTED: ErrorMapping(
            "QUALITY_RESOLUTION_REJECTED",
            "The genealogy quality finding was not accepted.",
            "Choose a declared resolution or cancel the operation.",
            2,
        ),
        DomainFailureCode.ARTIFACT_INVALID: ErrorMapping(
            "ARTIFACT_INVALID",
            "The selected artifact is invalid for this operation.",
            "Select a valid artifact and retry.",
            2,
        ),
        DomainFailureCode.ARTIFACT_FORBIDDEN: ErrorMapping(
            "ARTIFACT_FORBIDDEN",
            "The artifact grant does not authorize this operation.",
            "Select the artifact again for the requested operation.",
            2,
        ),
        DomainFailureCode.ARTIFACT_TOO_LARGE: ErrorMapping(
            "ARTIFACT_TOO_LARGE",
            "The selected artifact exceeds the supported size limit.",
            "Choose a smaller artifact.",
            2,
        ),
        DomainFailureCode.PUBLICATION_FAILED: ErrorMapping(
            "PUBLICATION_FAILED",
            "The output artifact could not be published safely.",
            "Verify the destination and retry; the previous artifact was preserved.",
            1,
        ),
        DomainFailureCode.PROVIDER_CONSENT_REQUIRED: ErrorMapping(
            "PROVIDER_CONSENT_REQUIRED",
            "The selected provider operation requires explicit consent.",
            "Create or select a matching consent grant.",
            2,
        ),
        DomainFailureCode.PROVIDER_UNAVAILABLE: ErrorMapping(
            "PROVIDER_UNAVAILABLE",
            "The selected provider is unavailable.",
            "Retry later or choose the offline provider.",
            1,
        ),
        DomainFailureCode.INTERNAL: ErrorMapping(
            "APPLICATION_FAILURE",
            "The application could not complete the operation safely.",
            "Retry the operation or run diagnostics.",
            1,
        ),
    }
)


def map_domain_failure(failure: DomainFailure) -> AncestryError:
    """Map every declared domain code without exposing exception text."""

    mapping = DOMAIN_ERROR_MAPPINGS[failure.code]
    details = {detail.name: detail.value for detail in failure.details}
    return AncestryError(
        mapping.code,
        mapping.message,
        mapping.remediation,
        mapping.exit_code,
        details,
    )


def error_envelope(
    error: AncestryError,
    *,
    correlation_ref: str | None = None,
) -> ErrorEnvelope:
    """Create a strict boundary response from an already-sanitized error."""

    details: list[FailureDetail] = []
    for name, value in sorted(error.details.items()):
        if not (isinstance(value, (str, int, float, bool)) or value is None):
            continue
        try:
            details.append(FailureDetail(name, value))
        except ValueError:
            continue
    return ErrorEnvelope(
        code=error.code,
        message=error.message,
        remediation=error.remediation,
        correlation_ref=correlation_ref,
        details=tuple(details),
    )


__all__ = [
    "DOMAIN_ERROR_MAPPINGS",
    "ErrorMapping",
    "error_envelope",
    "map_domain_failure",
]
