"""Strict, versioned DTOs for the private loopback API."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ancestryllm.application.dto import CONTRACT_VERSION, MAX_BOUNDARY_JSON_BYTES

API_NAMESPACE: Literal["/api/v1"] = "/api/v1"
API_CONTRACT: Literal["ancestryllm.internal-api/1"] = "ancestryllm.internal-api/1"
API_VERSION_HEADER = "X-Ancestry-API-Version"
API_BUILD_HEADER = "X-Ancestry-App-Build"

_STRICT_MODEL = ConfigDict(extra="forbid", frozen=True, strict=True)
_SafeCode = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")]
_SafeText = Annotated[str, Field(min_length=1, max_length=512)]
_BuildIdentity = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:+-]+$")]
ErrorScalar: TypeAlias = str | int | float | bool | None  # noqa: UP040 - Pydantic schema


class ApiVersion(BaseModel):
    model_config = _STRICT_MODEL

    namespace: Literal["/api/v1"] = API_NAMESPACE
    contract: Literal["ancestryllm.internal-api/1"] = API_CONTRACT
    application_contract: Literal["ancestryllm.application/0.3"] = cast(
        "Literal['ancestryllm.application/0.3']", CONTRACT_VERSION
    )


class RequestSizePolicy(BaseModel):
    model_config = _STRICT_MODEL

    max_body_bytes: Annotated[int, Field(ge=1, le=MAX_BOUNDARY_JSON_BYTES)] = (
        MAX_BOUNDARY_JSON_BYTES
    )
    max_json_depth: Annotated[int, Field(ge=1, le=64)] = 16
    max_collection_items: Annotated[int, Field(ge=1, le=10_000)] = 1_000
    max_string_characters: Annotated[int, Field(ge=1, le=MAX_BOUNDARY_JSON_BYTES)] = 65_536


class PaginationPolicy(BaseModel):
    model_config = _STRICT_MODEL

    default_limit: Annotated[int, Field(ge=1, le=100)] = 25
    maximum_limit: Annotated[int, Field(ge=1, le=100)] = 100
    maximum_cursor_characters: Annotated[int, Field(ge=32, le=1_024)] = 256


class PaginationRequest(BaseModel):
    model_config = _STRICT_MODEL

    limit: Annotated[int, Field(ge=1, le=100)] = 25
    cursor: Annotated[str, Field(min_length=32, max_length=256)] | None = None


class PageMetadata(BaseModel):
    model_config = _STRICT_MODEL

    count: Annotated[int, Field(ge=0, le=100)] = 0
    next_cursor: Annotated[str, Field(min_length=32, max_length=256)] | None = None


class CapabilityAction(BaseModel):
    model_config = _STRICT_MODEL

    dispatch_key: Annotated[
        str, Field(min_length=3, max_length=193, pattern=r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
    ]
    name: _SafeCode
    summary: _SafeText


class CapabilityModule(BaseModel):
    model_config = _STRICT_MODEL

    module_id: _SafeCode
    name: Annotated[str, Field(min_length=1, max_length=128)]
    summary: _SafeText
    actions: Annotated[tuple[CapabilityAction, ...], Field(min_length=1, max_length=128)]


class CapabilityManifest(BaseModel):
    model_config = _STRICT_MODEL

    api: ApiVersion = Field(default_factory=ApiVersion)
    modules: Annotated[tuple[CapabilityModule, ...], Field(max_length=128)] = ()
    request_policy: RequestSizePolicy = Field(default_factory=RequestSizePolicy)
    pagination: PaginationPolicy = Field(default_factory=PaginationPolicy)


class HealthResponse(BaseModel):
    model_config = _STRICT_MODEL

    status: Literal["ready"] = "ready"
    api: ApiVersion = Field(default_factory=ApiVersion)
    app_build: _BuildIdentity
    sidecar_build: _BuildIdentity
    readiness_proof: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StartupPlatformResponse(BaseModel):
    """Non-sensitive platform details for relevant local remediation."""

    model_config = _STRICT_MODEL

    operating_system: Literal["linux", "macos", "windows", "unsupported"]
    architecture: Literal["arm64", "x64", "unsupported"]


class StartupDiagnosticComponentResponse(BaseModel):
    """One renderer-safe startup component result."""

    model_config = _STRICT_MODEL

    component: Literal["configuration", "sqlcipher", "keyring", "workspace"]
    status: Literal["ready", "warning", "blocked"]
    code: _SafeCode
    message: _SafeText
    remediation: Annotated[str, Field(min_length=1, max_length=512)] | None
    restart_required: bool
    blocks_mutations: bool

    @field_validator("message", "remediation")
    @classmethod
    def reject_private_or_control_text(cls, value: str | None) -> str | None:
        if value is not None and any(marker in value for marker in ("/", "\\", "\n", "\r", "\x00")):
            raise ValueError("startup diagnostic text must be path-free")
        return value


class StartupDiagnosticReportResponse(BaseModel):
    """Schema-v1 startup report consumed by Electron main and the renderer."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    status: Literal["ready", "degraded"]
    platform: StartupPlatformResponse
    components: Annotated[
        tuple[StartupDiagnosticComponentResponse, ...], Field(min_length=4, max_length=4)
    ]


class SettingValidationResponse(BaseModel):
    """Reviewed renderer constraints for one non-secret setting."""

    model_config = _STRICT_MODEL

    allowed_values: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    minimum: int | float | None = None
    maximum: int | float | None = None


class SettingFieldResponse(BaseModel):
    """One renderer-safe setting descriptor."""

    model_config = _STRICT_MODEL

    key: Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_.-]+$")]
    label: Annotated[str, Field(min_length=1, max_length=128)]
    help: Annotated[str, Field(min_length=1, max_length=512)]
    type: Literal["string", "integer", "number"]
    value: str | int | float
    default_value: str | int | float
    validation: SettingValidationResponse
    restart_required: bool = False
    sensitive: Literal[False] = False


class SettingsResponse(BaseModel):
    """Versioned settings snapshot with an optimistic-lock revision."""

    model_config = _STRICT_MODEL

    schema_version: Annotated[int, Field(ge=1, json_schema_extra={"const": 1})]
    revision: Annotated[int, Field(ge=0)]
    fields: Annotated[tuple[SettingFieldResponse, ...], Field(min_length=1, max_length=32)]


class SettingsPatchRequest(BaseModel):
    """Allowlisted settings changes based on a previously read revision."""

    model_config = _STRICT_MODEL

    schema_version: Annotated[int, Field(ge=1, json_schema_extra={"const": 1})]
    expected_revision: Annotated[int, Field(ge=0)]
    changes: Annotated[dict[str, Any], Field(min_length=1, max_length=32)]


class SecretSetRequest(BaseModel):
    """Write-only secret input that is never returned by the API."""

    model_config = _STRICT_MODEL

    value: Annotated[
        str,
        Field(min_length=1, max_length=65_536, repr=False, json_schema_extra={"writeOnly": True}),
    ]


class SecretStatusResponse(BaseModel):
    """Credential presence without credential readback."""

    model_config = _STRICT_MODEL

    reference: Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_.-]+$")]
    status: Literal["present", "missing", "unavailable"]


class FailureDetail(BaseModel):
    model_config = _STRICT_MODEL

    name: _SafeCode
    value: ErrorScalar

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: ErrorScalar) -> ErrorScalar:
        if isinstance(value, str) and (
            len(value) > 256 or any(marker in value for marker in ("/", "\\", "\n", "\r", "\x00"))
        ):
            raise ValueError("failure detail strings must be bounded and path-free")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("failure detail numbers must be finite")
        return value


class ErrorEnvelope(BaseModel):
    model_config = _STRICT_MODEL

    code: _SafeCode
    message: _SafeText
    remediation: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    correlation_ref: Annotated[str, Field(pattern=r"^api_[0-9a-f]{32}$")]
    details: Annotated[tuple[FailureDetail, ...], Field(max_length=16)] = ()

    @field_validator("message", "remediation")
    @classmethod
    def reject_control_text(cls, value: str | None) -> str | None:
        if value is not None and any(character in value for character in ("\x00", "\r")):
            raise ValueError("error text contains a control character")
        return value


__all__ = [
    "API_BUILD_HEADER",
    "API_CONTRACT",
    "API_NAMESPACE",
    "API_VERSION_HEADER",
    "ApiVersion",
    "CapabilityAction",
    "CapabilityManifest",
    "CapabilityModule",
    "ErrorEnvelope",
    "ErrorScalar",
    "FailureDetail",
    "HealthResponse",
    "PageMetadata",
    "PaginationPolicy",
    "PaginationRequest",
    "RequestSizePolicy",
    "SecretSetRequest",
    "SecretStatusResponse",
    "SettingFieldResponse",
    "SettingValidationResponse",
    "SettingsPatchRequest",
    "SettingsResponse",
    "StartupDiagnosticComponentResponse",
    "StartupDiagnosticReportResponse",
    "StartupPlatformResponse",
]
