"""Deterministic, framework-independent application boundary DTOs.

The objects in this module are safe to translate into a terminal, HTTP, or
desktop transport.  They deliberately exclude filesystem paths, callbacks,
exceptions, provider clients, database handles, and presentation objects.
"""

from __future__ import annotations

import json
import math
import types
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Self, TypeAlias, Union, cast, get_args, get_origin, get_type_hints

CONTRACT_VERSION = "ancestryllm.application/0.3"
MAX_BOUNDARY_JSON_BYTES = 1_048_576
MAX_ARTIFACT_BYTES = 2_147_483_648
MAX_TEXT_LENGTH = 65_536
MAX_PROGRESS_TOTAL = 1_000_000_000

Scalar = str | int | float | bool | None
JSONValue: TypeAlias = Scalar | list["JSONValue"] | dict[str, "JSONValue"]  # noqa: UP040


class BoundaryDTO:
    """Marker and deterministic JSON codec for transport-neutral DTOs."""

    __slots__ = ()

    def to_serializable(self) -> JSONValue:
        """Return the strict-JSON value represented by this boundary object."""

        return cast("JSONValue", _encode(self))

    def to_json(self) -> str:
        """Serialize with stable ordering and strict JSON scalar behavior."""

        return dump_boundary(self)

    @classmethod
    def from_json(cls, payload: str) -> Self:
        """Deserialize one exact DTO type and reject unknown fields."""

        return load_boundary(cls, payload)


class ServiceRequest(BoundaryDTO):
    """Marker for one typed application-service request."""

    __slots__ = ()


class ServiceResult(BoundaryDTO):
    """Marker for one typed application-service result."""

    __slots__ = ()


def _encode(value: object) -> object:
    if isinstance(value, BoundaryDTO):
        return {name: _encode(getattr(value, name)) for name in _field_names(type(value))}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Boundary DTO numbers must be finite.")
        return value
    raise TypeError(f"Unsupported boundary value type: {type(value).__name__}")


def _decode_union(value: object, annotation: object) -> object:
    options = get_args(annotation)
    if value is None and type(None) in options:
        return None
    failures: list[Exception] = []
    for option in options:
        if option is type(None):
            continue
        try:
            return _decode(value, option)
        except (TypeError, ValueError) as exc:
            failures.append(exc)
    raise TypeError("Boundary value does not match its declared union.") from (
        failures[-1] if failures else None
    )


def _decode(value: object, annotation: object) -> object:
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is Union:
        return _decode_union(value, annotation)
    if origin is tuple:
        if not isinstance(value, list):
            raise TypeError("Boundary tuple values must be encoded as JSON arrays.")
        arguments = get_args(annotation)
        if len(arguments) != 2 or arguments[1] is not Ellipsis:
            raise TypeError("Boundary DTOs support homogeneous variable-length tuples only.")
        return tuple(_decode(item, arguments[0]) for item in value)
    if isinstance(annotation, type) and issubclass(annotation, BoundaryDTO):
        if not isinstance(value, Mapping):
            raise TypeError("Nested boundary DTO values must be JSON objects.")
        return _decode_dataclass(annotation, value)
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        if not isinstance(value, str):
            raise TypeError("Boundary enum values must be strings.")
        return annotation(value)
    if annotation is str:
        if not isinstance(value, str):
            raise TypeError("Expected a boundary string.")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise TypeError("Expected a boundary boolean.")
        return value
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("Expected a boundary integer.")
        return value
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Expected a boundary number.")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Boundary DTO numbers must be finite.")
        return number
    if annotation is type(None):
        if value is not None:
            raise TypeError("Expected a null boundary value.")
        return None
    raise TypeError(f"Unsupported boundary annotation: {annotation!r}")


def _field_names(value_type: object) -> tuple[str, ...]:
    declared = getattr(value_type, "__dataclass_fields__", None)
    if not isinstance(declared, Mapping) or not all(isinstance(name, str) for name in declared):
        raise TypeError("Boundary DTO types must be dataclasses.")
    return tuple(cast("str", name) for name in declared)


def _decode_dataclass[BoundaryT: BoundaryDTO](
    cls: type[BoundaryT], value: Mapping[object, object]
) -> BoundaryT:
    if any(not isinstance(key, str) for key in value):
        raise TypeError("Boundary DTO object keys must be strings.")
    annotations = get_type_hints(cls)
    expected = set(_field_names(cls))
    supplied = {key for key in value if isinstance(key, str)}
    unknown = supplied - expected
    missing = expected - supplied
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"Missing {cls.__name__} fields: {', '.join(sorted(missing))}")
    decoded = {
        name: _decode(cast("Mapping[str, object]", value)[name], annotations[name])
        for name in sorted(expected)
    }
    return cls(**decoded)


def dump_boundary(value: BoundaryDTO) -> str:
    """Return canonical JSON for one boundary DTO."""

    _field_names(type(value))
    envelope = {
        "contract": CONTRACT_VERSION,
        "type": type(value).__name__,
        "value": _encode(value),
    }
    payload = json.dumps(
        envelope,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > MAX_BOUNDARY_JSON_BYTES:
        raise ValueError("Boundary DTO exceeds the serialized size limit.")
    return payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def load_boundary[BoundaryT: BoundaryDTO](cls: type[BoundaryT], payload: str) -> BoundaryT:
    """Load one exact DTO type from canonical-compatible strict JSON."""

    if len(payload.encode("utf-8")) > MAX_BOUNDARY_JSON_BYTES:
        raise ValueError("Boundary DTO exceeds the serialized size limit.")
    decoded = json.loads(payload, parse_constant=_reject_json_constant)
    if not isinstance(decoded, dict):
        raise TypeError("Boundary envelope must be a JSON object.")
    if set(decoded) != {"contract", "type", "value"}:
        raise ValueError("Boundary envelope fields do not match the contract.")
    if decoded["contract"] != CONTRACT_VERSION:
        raise ValueError("Unsupported boundary contract version.")
    if decoded["type"] != cls.__name__:
        raise ValueError("Boundary DTO type does not match the requested class.")
    value = decoded["value"]
    if not isinstance(value, dict):
        raise TypeError("Boundary DTO value must be a JSON object.")
    return _decode_dataclass(cls, value)


def _validate_identifier(label: str, value: str, *, prefix: str | None = None) -> None:
    if prefix is not None and not value.startswith(prefix):
        raise ValueError(f"{label} must use the {prefix!r} opaque prefix.")
    suffix = value[len(prefix) :] if prefix is not None else value
    minimum = 32 if prefix is not None else 1
    if not minimum <= len(suffix) <= 128:
        raise ValueError(f"{label} length is outside its bounded range.")
    if not all(character.isalnum() or character in "._:-" for character in suffix):
        raise ValueError(f"{label} contains unsupported characters.")
    if any(marker in value for marker in ("/", "\\", "..", "\n", "\r", "\x00")):
        raise ValueError(f"{label} must not contain paths or control characters.")


def _validate_operation_id(value: str) -> None:
    if (
        len(value) != 67
        or not value.startswith("op_")
        or any(character not in "0123456789abcdef" for character in value[3:])
    ):
        raise ValueError(
            "operation_id must use the form op_ followed by 64 lowercase hexadecimal characters."
        )


def _validate_code(label: str, value: str) -> None:
    if not 1 <= len(value) <= 96:
        raise ValueError(f"{label} length is outside its bounded range.")
    if not all(character.isalnum() or character in "._:-" for character in value):
        raise ValueError(f"{label} contains unsupported characters.")


def _validate_text(label: str, value: str, *, maximum: int = MAX_TEXT_LENGTH) -> None:
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds its bounded length.")
    if "\x00" in value:
        raise ValueError(f"{label} contains a null character.")


class ArtifactStatus(StrEnum):
    """Lifecycle state exposed without a host path."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    REVOKED = "revoked"


class ArtifactAccess(StrEnum):
    """Operation-scoped capability granted by an owning adapter."""

    READ = "read"
    WRITE = "write"


class MediationTransport(StrEnum):
    """Trusted execution adapter selected without exposing a host path."""

    LOCAL_CONTAINER = "local-container"
    REMOTE_SERVICE = "remote-service"


@dataclass(frozen=True, slots=True)
class ArtifactRef(BoundaryDTO):
    """Opaque application artifact descriptor with bounded metadata."""

    artifact_id: str
    media_type: str
    artifact_type: str
    size_bytes: int
    status: ArtifactStatus
    sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier("artifact_id", self.artifact_id, prefix="art_")
        _validate_code("artifact_type", self.artifact_type)
        if not 1 <= len(self.media_type) <= 127 or "/" not in self.media_type:
            raise ValueError("media_type must be a bounded MIME type.")
        if not 0 <= self.size_bytes <= MAX_ARTIFACT_BYTES:
            raise ValueError("artifact size is outside the supported range.")
        if self.sha256 is not None and (
            len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise ValueError("sha256 must be a lowercase hexadecimal digest.")


@dataclass(frozen=True, slots=True)
class ArtifactGrantRef(BoundaryDTO):
    """Opaque, scoped authorization to an adapter-owned filesystem resource."""

    grant_id: str
    operation: str
    access: ArtifactAccess

    def __post_init__(self) -> None:
        _validate_identifier("grant_id", self.grant_id, prefix="grt_")
        _validate_code("operation", self.operation)


@dataclass(frozen=True, slots=True)
class MediatedOperationRequest(ServiceRequest):
    """Path-free capability request shared by local and remote adapters."""

    operation_id: str
    operation: str
    transport: MediationTransport
    inputs: tuple[ArtifactGrantRef, ...]
    outputs: tuple[ArtifactGrantRef, ...]

    def __post_init__(self) -> None:
        _validate_operation_id(self.operation_id)
        _validate_code("operation", self.operation)
        if not 1 <= len(self.inputs) <= 16:
            raise ValueError("mediated inputs must contain between 1 and 16 grants.")
        if not 1 <= len(self.outputs) <= 8:
            raise ValueError("mediated outputs must contain between 1 and 8 grants.")
        grants = (*self.inputs, *self.outputs)
        if len({grant.grant_id for grant in grants}) != len(grants):
            raise ValueError("mediated grant identifiers must be unique.")
        if any(grant.operation != self.operation for grant in grants):
            raise ValueError("mediated grants must match the requested operation.")
        if any(grant.access is not ArtifactAccess.READ for grant in self.inputs):
            raise ValueError("mediated input grants must provide read access.")
        if any(grant.access is not ArtifactAccess.WRITE for grant in self.outputs):
            raise ValueError("mediated output grants must provide write access.")


@dataclass(frozen=True, slots=True)
class MediatedOperationResult(ServiceResult):
    """Path-free artifacts produced by one mediated operation."""

    operation_id: str
    outputs: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        _validate_operation_id(self.operation_id)
        if not 1 <= len(self.outputs) <= 8:
            raise ValueError("mediated results must contain between 1 and 8 artifacts.")
        if len({output.artifact_id for output in self.outputs}) != len(self.outputs):
            raise ValueError("mediated result artifact identifiers must be unique.")
        if any(output.status is not ArtifactStatus.READY for output in self.outputs):
            raise ValueError("mediated result artifacts must be ready.")


@dataclass(frozen=True, slots=True)
class SecretGrantRef(BoundaryDTO):
    """Write-only secret capability; the credential never enters a DTO."""

    grant_id: str
    secret_name: str

    def __post_init__(self) -> None:
        _validate_identifier("grant_id", self.grant_id, prefix="sec_")
        _validate_code("secret_name", self.secret_name)


@dataclass(frozen=True, slots=True)
class NamedValue(BoundaryDTO):
    """Bounded key/value input used instead of an unrestricted mapping."""

    name: str
    value: str

    def __post_init__(self) -> None:
        _validate_code("name", self.name)
        _validate_text("value", self.value, maximum=16_384)


@dataclass(frozen=True, slots=True)
class ProviderSelection(BoundaryDTO):
    """Provider choice without a client, credential, or SDK object."""

    provider_id: str = "none"
    profile_id: str | None = None
    model_id: str | None = None
    consent_id: str | None = None

    def __post_init__(self) -> None:
        _validate_code("provider_id", self.provider_id)
        for label, value in (
            ("profile_id", self.profile_id),
            ("model_id", self.model_id),
            ("consent_id", self.consent_id),
        ):
            if value is not None:
                _validate_code(label, value)

    @property
    def network_allowed(self) -> bool:
        """Return false for the explicit offline provider."""

        return self.provider_id != "none"


@dataclass(frozen=True, slots=True)
class ProgressUpdate(BoundaryDTO):
    """Bounded structural progress that cannot carry genealogy content."""

    operation: str
    stage: str
    sequence: int
    completed: int | None = None
    total: int | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        _validate_code("operation", self.operation)
        _validate_code("stage", self.stage)
        if not 0 <= self.sequence <= MAX_PROGRESS_TOTAL:
            raise ValueError("progress sequence is outside the supported range.")
        if (self.completed is None) is not (self.total is None):
            raise ValueError("progress requires both completed and total values.")
        if (
            self.completed is not None
            and self.total is not None
            and not 0 <= self.completed <= self.total <= MAX_PROGRESS_TOTAL
        ):
            raise ValueError("progress counts are outside the supported range.")
        if self.artifact_id is not None:
            _validate_identifier("artifact_id", self.artifact_id, prefix="art_")


class DecisionKind(StrEnum):
    """Supported categories of explicit application decision."""

    CONFIRM = "confirm"
    SELECT = "select"
    RESOLVE_CONFLICT = "resolve_conflict"


@dataclass(frozen=True, slots=True)
class DecisionOption(BoundaryDTO):
    """Stable option code; adapters own user-facing presentation."""

    option_id: str
    label_code: str
    destructive: bool = False

    def __post_init__(self) -> None:
        _validate_code("option_id", self.option_id)
        _validate_code("label_code", self.label_code)


@dataclass(frozen=True, slots=True)
class DecisionRequest(BoundaryDTO):
    """Transport-neutral request for an explicit user decision."""

    decision_id: str
    operation: str
    decision_code: str
    kind: DecisionKind
    options: tuple[DecisionOption, ...]
    default_option_id: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier("decision_id", self.decision_id)
        _validate_code("operation", self.operation)
        _validate_code("decision_code", self.decision_code)
        if not 1 <= len(self.options) <= 32:
            raise ValueError("decision options must contain between 1 and 32 items.")
        option_ids = {option.option_id for option in self.options}
        if len(option_ids) != len(self.options):
            raise ValueError("decision option identifiers must be unique.")
        if self.default_option_id is not None and self.default_option_id not in option_ids:
            raise ValueError("default decision option is not declared.")


@dataclass(frozen=True, slots=True)
class DecisionResponse(BoundaryDTO):
    """Selected stable option or an explicit cancellation."""

    decision_id: str
    option_id: str | None
    cancelled: bool = False

    def __post_init__(self) -> None:
        _validate_identifier("decision_id", self.decision_id)
        if self.option_id is not None:
            _validate_code("option_id", self.option_id)
        if self.cancelled and self.option_id is not None:
            raise ValueError("a cancelled decision cannot select an option.")


@dataclass(frozen=True, slots=True)
class IdentityCandidate(BoundaryDTO):
    """Opaque candidate identity and deterministic score."""

    candidate_ref: str
    confidence: int

    def __post_init__(self) -> None:
        _validate_identifier("candidate_ref", self.candidate_ref)
        if not 0 <= self.confidence <= 100:
            raise ValueError("identity confidence must be between 0 and 100.")


@dataclass(frozen=True, slots=True)
class IdentityResolutionRequest(BoundaryDTO):
    """Identity choice using opaque record references, never record content."""

    resolution_id: str
    source_ref: str
    candidates: tuple[IdentityCandidate, ...]

    def __post_init__(self) -> None:
        _validate_identifier("resolution_id", self.resolution_id)
        _validate_identifier("source_ref", self.source_ref)
        if not 1 <= len(self.candidates) <= 128:
            raise ValueError("identity candidates are outside the supported range.")


@dataclass(frozen=True, slots=True)
class IdentityResolutionResult(BoundaryDTO):
    """Chosen opaque identity, a new-record decision, or cancellation."""

    resolution_id: str
    selected_ref: str | None = None
    create_new: bool = False
    cancelled: bool = False

    def __post_init__(self) -> None:
        _validate_identifier("resolution_id", self.resolution_id)
        if self.selected_ref is not None:
            _validate_identifier("selected_ref", self.selected_ref)
        choices = int(self.selected_ref is not None) + int(self.create_new) + int(self.cancelled)
        if choices != 1:
            raise ValueError("identity resolution requires exactly one outcome.")


class QualitySeverity(StrEnum):
    """Stable, presentation-neutral quality severity."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class QualityResolutionRequest(BoundaryDTO):
    """Quality decision identified by stable codes and opaque finding identity."""

    resolution_id: str
    finding_ref: str
    finding_code: str
    severity: QualitySeverity
    options: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_identifier("resolution_id", self.resolution_id)
        _validate_identifier("finding_ref", self.finding_ref)
        _validate_code("finding_code", self.finding_code)
        if not 1 <= len(self.options) <= 32:
            raise ValueError("quality resolution options are outside the supported range.")
        for option in self.options:
            _validate_code("quality option", option)


@dataclass(frozen=True, slots=True)
class QualityResolutionResult(BoundaryDTO):
    """Stable quality-resolution option or explicit cancellation."""

    resolution_id: str
    option_id: str | None
    cancelled: bool = False

    def __post_init__(self) -> None:
        _validate_identifier("resolution_id", self.resolution_id)
        if self.option_id is not None:
            _validate_code("option_id", self.option_id)
        if self.cancelled == (self.option_id is not None):
            raise ValueError("quality resolution must select an option or cancel.")


@dataclass(frozen=True, slots=True)
class FailureDetail(BoundaryDTO):
    """Allowlisted scalar error metadata with path-like values rejected."""

    name: str
    value: Scalar

    def __post_init__(self) -> None:
        _validate_code("failure detail name", self.name)
        if isinstance(self.value, str):
            _validate_text("failure detail value", self.value, maximum=256)
            if any(marker in self.value for marker in ("/", "\\", "\n", "\r", "\x00")):
                raise ValueError(
                    "failure details must not contain host paths or control characters."
                )
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("failure detail numbers must be finite.")


@dataclass(frozen=True, slots=True)
class ErrorEnvelope(BoundaryDTO):
    """Stable safe error response shared by all transports."""

    code: str
    message: str
    remediation: str | None
    correlation_ref: str | None
    details: tuple[FailureDetail, ...] = ()

    def __post_init__(self) -> None:
        _validate_code("error code", self.code)
        _validate_text("error message", self.message, maximum=512)
        if self.remediation is not None:
            _validate_text("error remediation", self.remediation, maximum=512)
        if self.correlation_ref is not None:
            _validate_identifier("correlation_ref", self.correlation_ref)
        if len(self.details) > 16:
            raise ValueError("error details exceed the supported item limit.")


__all__ = [
    "CONTRACT_VERSION",
    "MAX_ARTIFACT_BYTES",
    "MAX_BOUNDARY_JSON_BYTES",
    "ArtifactAccess",
    "ArtifactGrantRef",
    "ArtifactRef",
    "ArtifactStatus",
    "BoundaryDTO",
    "DecisionKind",
    "DecisionOption",
    "DecisionRequest",
    "DecisionResponse",
    "ErrorEnvelope",
    "FailureDetail",
    "IdentityCandidate",
    "IdentityResolutionRequest",
    "IdentityResolutionResult",
    "JSONValue",
    "MediatedOperationRequest",
    "MediatedOperationResult",
    "MediationTransport",
    "NamedValue",
    "ProgressUpdate",
    "ProviderSelection",
    "QualityResolutionRequest",
    "QualityResolutionResult",
    "QualitySeverity",
    "Scalar",
    "SecretGrantRef",
    "ServiceRequest",
    "ServiceResult",
    "dump_boundary",
    "load_boundary",
]
