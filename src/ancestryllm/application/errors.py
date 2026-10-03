"""Central mapping from pure domain failures to stable application errors."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from ancestryllm.application.dto import ErrorEnvelope, FailureDetail
from ancestryllm.core.errors import AncestryError, ProviderError
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode

if TYPE_CHECKING:
    from collections.abc import Mapping


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

_ARTIFACT_TOO_LARGE_CODES = frozenset(
    {
        "FILE_COLLECTION_LIMIT_EXCEEDED",
        "FILE_INPUT_TOO_LARGE",
        "FILE_LINE_TOO_LONG",
        "FILE_NESTING_LIMIT_EXCEEDED",
        "FILE_RECORD_LIMIT_EXCEEDED",
        "FILE_RECORD_TOO_LARGE",
    }
)
_INVALID_REQUEST_CODES = frozenset(
    {
        "ARGUMENT_INVALID",
        "CONFIG_INVALID",
        "GEDCOM_INPUT_REQUIRED",
        "GEDCOM_OVERWRITE_INPUT",
        "GEDCOM_REPORT_ALIAS",
        "GEDCOM_VERSION_INVALID",
        "QUALITY_ROOT_REQUIRED",
        "SYNC_CONFIGURATION",
    }
)
_ARTIFACT_INVALID_CODES = frozenset(
    {
        "GEDCOM_PARSE_INVALID",
        "MANIFEST_INVALID",
        "MANIFEST_MASTER_MISMATCH",
        "SYNC_PARSE",
    }
)
_PUBLICATION_CODES = frozenset(
    {
        "GEDCOM_VALIDATION_FAILED",
        "PUBLICATION_FAILED",
        "SYNC_OUTPUT",
        "SYNC_PUBLICATION_INCOMPLETE",
    }
)


def domain_failure_from_exception(error: Exception) -> DomainFailure:
    """Classify a current internal exception without retaining unsafe detail."""

    if isinstance(error, DomainFailure):
        return error
    if isinstance(error, AncestryError):
        code = error.code
        if code in {"CANCELLED", "JOB_CANCELLED", "PROVIDER_CANCELLED"}:
            failure_code = DomainFailureCode.CANCELLED
        elif code in {"GEDCOM_ROOT_PERSON_UNRESOLVED", "SYNC_AMBIGUOUS"}:
            failure_code = DomainFailureCode.IDENTITY_AMBIGUOUS
        elif code == "SYNC_UNSAFE_REMOVAL":
            failure_code = DomainFailureCode.CONFLICT
        elif code in _ARTIFACT_TOO_LARGE_CODES:
            failure_code = DomainFailureCode.ARTIFACT_TOO_LARGE
        elif code.startswith("FILE_") or code in _ARTIFACT_INVALID_CODES:
            failure_code = DomainFailureCode.ARTIFACT_INVALID
        elif code in _PUBLICATION_CODES:
            failure_code = DomainFailureCode.PUBLICATION_FAILED
        elif code.startswith("CONSENT_") or code == "CLOUD_CONSENT_REQUIRED":
            failure_code = DomainFailureCode.PROVIDER_CONSENT_REQUIRED
        elif isinstance(error, ProviderError) or code.startswith(("PROVIDER_", "LLM_")):
            failure_code = DomainFailureCode.PROVIDER_UNAVAILABLE
        elif code in _INVALID_REQUEST_CODES:
            failure_code = DomainFailureCode.INVALID_REQUEST
        elif code.endswith("_NOT_FOUND"):
            failure_code = DomainFailureCode.NOT_FOUND
        else:
            failure_code = DomainFailureCode.INTERNAL
        return DomainFailure(failure_code)
    if isinstance(error, OSError):
        return DomainFailure(DomainFailureCode.PUBLICATION_FAILED)
    return DomainFailure(DomainFailureCode.INTERNAL)


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
    "domain_failure_from_exception",
    "error_envelope",
    "map_domain_failure",
]
