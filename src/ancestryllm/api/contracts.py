"""Strict, versioned DTOs for the private loopback API."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ancestryllm.application.chat import (
    CHAT_MAX_ACTIVE_SESSIONS,
    CHAT_MAX_CONTEXT_CHARACTERS,
    CHAT_MAX_MESSAGE_CHARACTERS,
    CHAT_MAX_MESSAGES,
    CHAT_MAX_OUTPUT_TOKENS,
    CHAT_MAX_SAFE_RETRIES,
    CHAT_MAX_TEMPERATURE,
    CHAT_MAX_TIMEOUT_SECONDS,
)
from ancestryllm.application.chat import (
    ChatCapability as ApplicationChatCapability,
)
from ancestryllm.application.chat import (
    ChatDataClass as ApplicationChatDataClass,
)
from ancestryllm.application.chat import (
    ChatMessage as ApplicationChatMessage,
)
from ancestryllm.application.chat import (
    ChatPurpose as ApplicationChatPurpose,
)
from ancestryllm.application.chat import (
    ChatRunRequest as ApplicationChatRunRequest,
)
from ancestryllm.application.chat import (
    ChatRunSummary as ApplicationChatRunSummary,
)
from ancestryllm.application.chat import (
    ChatSession as ApplicationChatSession,
)
from ancestryllm.application.chat import (
    ChatSessionCreateRequest as ApplicationChatSessionCreateRequest,
)
from ancestryllm.application.dto import CONTRACT_VERSION, MAX_BOUNDARY_JSON_BYTES

API_NAMESPACE: Literal["/api/v1"] = "/api/v1"
API_CONTRACT: Literal["ancestryllm.internal-api/1"] = "ancestryllm.internal-api/1"
API_VERSION_HEADER = "X-Ancestry-API-Version"
API_BUILD_HEADER = "X-Ancestry-App-Build"

_STRICT_MODEL = ConfigDict(extra="forbid", frozen=True, strict=True)
_SafeCode = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")]
_SafeText = Annotated[str, Field(min_length=1, max_length=512)]
_BuildIdentity = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:+-]+$")]
_ConfigurationRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_JobId = Annotated[str, Field(pattern=r"^j[0-9]{6,12}$")]
_JobTimestamp = Annotated[str, Field(min_length=1, max_length=64)]
_JobState = Literal[
    "queued",
    "running",
    "cancelling",
    "pending-safe-point",
    "completed",
    "failed",
    "cancelled",
]
_ProviderId = Literal["ollama", "openai", "anthropic", "gemini", "openrouter"]
_DataClass = Literal[
    "public_genealogy",
    "deceased_person",
    "living_person",
    "possibly_living_person",
    "free_text_note",
    "source_transcription",
    "government_identifier",
]
_ChatPurpose = Literal["genealogy_analysis", "source_analysis", "writing_assistance"]
_ChatRole = Literal["user", "assistant"]
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


class ProviderProfileResponse(BaseModel):
    """Provider configuration safe for an untrusted renderer."""

    model_config = _STRICT_MODEL

    name: Annotated[str, Field(min_length=1, max_length=200)]
    provider_id: _ProviderId
    model: Annotated[str, Field(min_length=1, max_length=200)]
    endpoint: Annotated[str, Field(min_length=1, max_length=2_048)]
    endpoint_kind: Literal["loopback", "remote"]
    secret_reference: (
        Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_.-]+$")] | None
    )
    enabled: bool


class ConsentGrantResponse(BaseModel):
    """Persisted consent metadata without payloads or credentials."""

    model_config = _STRICT_MODEL

    name: Annotated[str, Field(min_length=1, max_length=200)]
    provider_profile_name: Annotated[str, Field(min_length=1, max_length=200)]
    provider_id: _ProviderId
    modules: Annotated[tuple[_SafeCode, ...], Field(min_length=1, max_length=128)]
    purposes: Annotated[tuple[_SafeCode, ...], Field(min_length=1, max_length=128)]
    data_classes: Annotated[tuple[_DataClass, ...], Field(min_length=1, max_length=7)]
    models: Annotated[tuple[str, ...], Field(min_length=1, max_length=128)]
    max_cost_usd: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    retain_payloads: bool
    active: bool


class ProviderConfigurationResponse(BaseModel):
    """Revisioned provider and consent snapshot."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    revision: _ConfigurationRevision
    profiles: Annotated[tuple[ProviderProfileResponse, ...], Field(max_length=256)] = ()
    consents: Annotated[tuple[ConsentGrantResponse, ...], Field(max_length=256)] = ()


class ProviderProfileCreateRequest(BaseModel):
    """Atomic profile creation based on the last reviewed snapshot."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    expected_revision: _ConfigurationRevision
    name: Annotated[str, Field(min_length=1, max_length=200)]
    provider_id: _ProviderId
    model: Annotated[str, Field(min_length=1, max_length=200)]
    endpoint: Annotated[str, Field(min_length=1, max_length=2_048)]
    endpoint_identity_sha256: _ConfigurationRevision


class EndpointValidationRequest(BaseModel):
    """One user-initiated, policy-constrained endpoint test."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    provider_id: _ProviderId
    endpoint: Annotated[str, Field(min_length=1, max_length=2_048)]


class EndpointValidationResponse(BaseModel):
    """Sanitized endpoint-test result with no destination address."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    status: Literal["reachable"]
    endpoint_kind: Literal["loopback", "remote"]
    http_status: Annotated[int, Field(ge=100, le=599)]
    destination_digest: _ConfigurationRevision


class ConsentPreviewRequest(BaseModel):
    """Requested disclosure scope rendered before consent creation."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    provider_profile_name: Annotated[str, Field(min_length=1, max_length=200)]
    modules: Annotated[list[_SafeCode], Field(min_length=1, max_length=128)]
    purposes: Annotated[list[_SafeCode], Field(min_length=1, max_length=128)]
    data_classes: Annotated[list[_DataClass], Field(min_length=1, max_length=7)]
    models: Annotated[list[str], Field(min_length=1, max_length=128)]
    max_cost_usd: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    retain_payloads: bool = False


class ConsentPreviewResponse(BaseModel):
    """Authoritative consent disclosure preview and warning set."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    provider_profile_name: Annotated[str, Field(min_length=1, max_length=200)]
    provider_id: _ProviderId
    modules: Annotated[tuple[_SafeCode, ...], Field(min_length=1, max_length=128)]
    purposes: Annotated[tuple[_SafeCode, ...], Field(min_length=1, max_length=128)]
    data_classes: Annotated[tuple[_DataClass, ...], Field(min_length=1, max_length=7)]
    models: Annotated[tuple[str, ...], Field(min_length=1, max_length=128)]
    max_cost_usd: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    retain_payloads: bool = False
    warning_codes: Annotated[tuple[_SafeCode, ...], Field(max_length=8)] = ()


class ConsentPreviewPayloadRequest(BaseModel):
    """Exact previously rendered preview submitted for atomic creation."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    provider_profile_name: Annotated[str, Field(min_length=1, max_length=200)]
    provider_id: _ProviderId
    modules: Annotated[list[_SafeCode], Field(min_length=1, max_length=128)]
    purposes: Annotated[list[_SafeCode], Field(min_length=1, max_length=128)]
    data_classes: Annotated[list[_DataClass], Field(min_length=1, max_length=7)]
    models: Annotated[list[str], Field(min_length=1, max_length=128)]
    max_cost_usd: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    retain_payloads: bool = False
    warning_codes: Annotated[list[_SafeCode], Field(max_length=8)] = Field(default_factory=list)


class ConsentCreateRequest(BaseModel):
    """Create a consent only from a current revision and exact preview."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    expected_revision: _ConfigurationRevision
    name: Annotated[str, Field(min_length=1, max_length=200)]
    preview: ConsentPreviewPayloadRequest


class ProviderConfigurationMutationRequest(BaseModel):
    """Revision precondition for a provider configuration mutation."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    expected_revision: _ConfigurationRevision


class ChatMessage(BaseModel):
    """One bounded user or assistant message at the HTTP boundary."""

    model_config = _STRICT_MODEL

    role: _ChatRole
    content: Annotated[str, Field(min_length=1, max_length=CHAT_MAX_MESSAGE_CHARACTERS)]

    @field_validator("content")
    @classmethod
    def reject_blank_or_null_content(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("chat message content must be non-blank and contain no nulls")
        return value

    @classmethod
    def from_application(cls, message: ApplicationChatMessage) -> ChatMessage:
        return cls(role=message.role.value, content=message.content)


class ChatSessionCreateRequest(BaseModel):
    """Start one explicitly scoped, transient provider-backed chat session."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    provider_profile_name: Annotated[str, Field(min_length=1, max_length=200)]
    model: Annotated[str, Field(min_length=1, max_length=200)]
    purpose: _ChatPurpose
    data_classes: Annotated[list[_DataClass], Field(min_length=1, max_length=7)]
    consent_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None

    @field_validator("provider_profile_name", "model", "consent_name")
    @classmethod
    def reject_blank_or_null_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("chat selection text must be non-blank and contain no nulls")
        return value

    @field_validator("data_classes")
    @classmethod
    def reject_duplicate_data_classes(cls, value: list[_DataClass]) -> list[_DataClass]:
        if len(set(value)) != len(value):
            raise ValueError("chat data classes must not contain duplicates")
        return value

    def to_application(self) -> ApplicationChatSessionCreateRequest:
        return ApplicationChatSessionCreateRequest(
            schema_version=self.schema_version,
            provider_profile_name=self.provider_profile_name,
            model=self.model,
            purpose=ApplicationChatPurpose(self.purpose),
            data_classes=tuple(
                ApplicationChatDataClass(value) for value in sorted(self.data_classes)
            ),
            consent_name=self.consent_name,
        )


class ChatSession(BaseModel):
    """Sanitized metadata for one in-memory chat session."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    session_id: Annotated[str, Field(pattern=r"^chat_[0-9a-f]{32}$")]
    provider_profile_name: str
    provider_id: str
    model: str
    purpose: _ChatPurpose
    data_classes: tuple[_DataClass, ...]
    remote: bool
    consent_name: str | None
    message_count: Annotated[int, Field(ge=0, le=CHAT_MAX_MESSAGES)]
    transient: Literal[True] = True
    payload_retention: Literal[False] = False

    @classmethod
    def from_application(cls, session: ApplicationChatSession) -> ChatSession:
        return cls(
            schema_version=session.schema_version,
            session_id=session.session_id,
            provider_profile_name=session.provider_profile_name,
            provider_id=session.provider_id,
            model=session.model,
            purpose=session.purpose.value,
            data_classes=tuple(item.value for item in session.data_classes),
            remote=session.remote,
            consent_name=session.consent_name,
            message_count=session.message_count,
            transient=session.transient,
            payload_retention=session.payload_retention,
        )


class ChatRunRequest(BaseModel):
    """One bounded generation request for an existing transient session."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    message: Annotated[str, Field(min_length=1, max_length=CHAT_MAX_MESSAGE_CHARACTERS)]
    max_output_tokens: Annotated[int, Field(ge=1, le=CHAT_MAX_OUTPUT_TOKENS)] = 1_024
    temperature: Annotated[float, Field(ge=0.0, le=CHAT_MAX_TEMPERATURE, allow_inf_nan=False)] = 0.0
    timeout_seconds: Annotated[
        float, Field(ge=1.0, le=CHAT_MAX_TIMEOUT_SECONDS, allow_inf_nan=False)
    ] = 60.0
    max_safe_retries: Annotated[int, Field(ge=0, le=CHAT_MAX_SAFE_RETRIES)] = 0

    @field_validator("message")
    @classmethod
    def reject_blank_or_null_message(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("chat message must be non-blank and contain no nulls")
        return value

    def to_application(self) -> ApplicationChatRunRequest:
        return ApplicationChatRunRequest(
            schema_version=self.schema_version,
            message=self.message,
            max_output_tokens=self.max_output_tokens,
            temperature=self.temperature,
            timeout_seconds=self.timeout_seconds,
            max_safe_retries=self.max_safe_retries,
        )


class ChatRunSummary(BaseModel):
    """Sanitized provider result with explicit non-evidentiary status."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    assistant_message: ChatMessage
    provider_id: str
    model: str
    remote: bool
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    cost_usd: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    message_count: Annotated[int, Field(ge=0, le=CHAT_MAX_MESSAGES)]
    output_is_evidence: Literal[False] = False
    retained_payload: Literal[False] = False

    @classmethod
    def from_application(cls, summary: ApplicationChatRunSummary) -> ChatRunSummary:
        return cls(
            schema_version=summary.schema_version,
            assistant_message=ChatMessage.from_application(summary.assistant_message),
            provider_id=summary.provider_id,
            model=summary.model,
            remote=summary.remote,
            input_tokens=summary.input_tokens,
            output_tokens=summary.output_tokens,
            cost_usd=summary.cost_usd,
            message_count=summary.message_count,
            output_is_evidence=summary.output_is_evidence,
            retained_payload=summary.retained_payload,
        )


class ChatCapability(BaseModel):
    """Fixed schema-v1 limits and denied capabilities for transient chat."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    max_active_sessions: Literal[32] = CHAT_MAX_ACTIVE_SESSIONS
    max_messages: Literal[32] = CHAT_MAX_MESSAGES
    max_message_characters: Literal[16384] = CHAT_MAX_MESSAGE_CHARACTERS
    max_context_characters: Literal[65536] = CHAT_MAX_CONTEXT_CHARACTERS
    max_output_tokens: Literal[4096] = CHAT_MAX_OUTPUT_TOKENS
    max_temperature: Annotated[float, Field(ge=1.0, le=1.0)] = CHAT_MAX_TEMPERATURE
    max_timeout_seconds: Annotated[float, Field(ge=120.0, le=120.0)] = CHAT_MAX_TIMEOUT_SECONDS
    max_safe_retries: Literal[1] = CHAT_MAX_SAFE_RETRIES
    transient: Literal[True] = True
    tools_enabled: Literal[False] = False
    payload_retention: Literal[False] = False
    output_is_evidence: Literal[False] = False
    streaming: Literal[False] = False

    @classmethod
    def from_application(cls, capability: ApplicationChatCapability) -> ChatCapability:
        return cls(
            schema_version=capability.schema_version,
            max_active_sessions=capability.max_active_sessions,
            max_messages=capability.max_messages,
            max_message_characters=capability.max_message_characters,
            max_context_characters=capability.max_context_characters,
            max_output_tokens=capability.max_output_tokens,
            max_temperature=capability.max_temperature,
            max_timeout_seconds=capability.max_timeout_seconds,
            max_safe_retries=capability.max_safe_retries,
            transient=capability.transient,
            tools_enabled=capability.tools_enabled,
            payload_retention=capability.payload_retention,
            output_is_evidence=capability.output_is_evidence,
            streaming=capability.streaming,
        )


class JobProgressResponse(BaseModel):
    """Bounded progress metadata without operation payloads."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    operation: Annotated[str, Field(min_length=1, max_length=512)]
    timestamp: _JobTimestamp
    completed: Annotated[int, Field(ge=0, le=1_000_000_000)] | None
    total: Annotated[int, Field(ge=0, le=1_000_000_000)] | None


class JobArtifactResponse(BaseModel):
    """Opaque completed-job artifact descriptor; host paths are never exposed."""

    model_config = _STRICT_MODEL

    artifact_id: Annotated[
        str, Field(min_length=36, max_length=132, pattern=r"^art_[A-Za-z0-9._:-]+$")
    ]
    media_type: Annotated[
        str,
        Field(
            min_length=3,
            max_length=127,
            pattern=r"^[^/\r\n\x00]+/[^/\r\n\x00]+$",
        ),
    ]
    artifact_type: _SafeCode
    size_bytes: Annotated[int, Field(ge=0, le=2_147_483_648)]
    status: Literal["pending", "ready", "failed", "revoked"]
    sha256: _ConfigurationRevision | None = None


class JobSnapshotResponse(BaseModel):
    """Renderer-safe current state for one restart-safe background job."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    sequence: Annotated[int, Field(ge=1, le=9_999_999_999)]
    job_id: _JobId
    name: Annotated[str, Field(min_length=1, max_length=256)]
    state: _JobState
    submitted_at: _JobTimestamp
    started_at: _JobTimestamp | None
    finished_at: _JobTimestamp | None
    resource_refs: Annotated[
        tuple[Annotated[str, Field(pattern=r"^resource_[0-9a-f]{64}$")], ...],
        Field(max_length=32),
    ] = ()
    artifact: JobArtifactResponse | None
    outcome_summary: Annotated[str, Field(min_length=1, max_length=2_048)] | None
    next_action: Annotated[str, Field(min_length=1, max_length=2_048)] | None
    error_code: _SafeCode | None
    error_message: Annotated[str, Field(min_length=1, max_length=2_048)] | None
    error_remediation: Annotated[str, Field(min_length=1, max_length=2_048)] | None
    progress: JobProgressResponse | None
    cancellation_requested_at: _JobTimestamp | None
    cancellation_deferred_by: Annotated[str, Field(min_length=1, max_length=512)] | None


class JobEventResponse(BaseModel):
    """One monotonically ordered replay or live event."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    sequence: Annotated[int, Field(ge=1, le=9_999_999_999)]
    kind: Literal["snapshot", "progress", "cancellation", "terminal"]
    created_at: _JobTimestamp
    snapshot: JobSnapshotResponse


class JobListResponse(BaseModel):
    """Bounded newest-first job listing."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    jobs: Annotated[tuple[JobSnapshotResponse, ...], Field(max_length=1_000)] = ()


class JobShutdownRequest(BaseModel):
    """Fail-closed bounded shutdown preparation request."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    action: Literal["cancel", "wait"]
    timeout_seconds: Annotated[float, Field(ge=0, le=30, allow_inf_nan=False)]


class JobShutdownResponse(BaseModel):
    """Explicit authorization for Electron to terminate the sidecar."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    safe_to_quit: bool
    active_jobs: Annotated[tuple[JobSnapshotResponse, ...], Field(max_length=1_000)] = ()


class JobStreamFailureResponse(BaseModel):
    """Stable in-band SSE failure emitted after response headers are committed."""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = 1
    code: Literal["JOB_EVENT_REPLAY_EXPIRED"]
    message: _SafeText
    remediation: _SafeText


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
    "ChatCapability",
    "ChatMessage",
    "ChatRunRequest",
    "ChatRunSummary",
    "ChatSession",
    "ChatSessionCreateRequest",
    "ConsentCreateRequest",
    "ConsentGrantResponse",
    "ConsentPreviewPayloadRequest",
    "ConsentPreviewRequest",
    "ConsentPreviewResponse",
    "EndpointValidationRequest",
    "EndpointValidationResponse",
    "ErrorEnvelope",
    "ErrorScalar",
    "FailureDetail",
    "HealthResponse",
    "JobArtifactResponse",
    "JobEventResponse",
    "JobListResponse",
    "JobProgressResponse",
    "JobShutdownRequest",
    "JobShutdownResponse",
    "JobSnapshotResponse",
    "JobStreamFailureResponse",
    "PageMetadata",
    "PaginationPolicy",
    "PaginationRequest",
    "ProviderConfigurationMutationRequest",
    "ProviderConfigurationResponse",
    "ProviderProfileCreateRequest",
    "ProviderProfileResponse",
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
