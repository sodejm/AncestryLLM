"""FastAPI composition for the authenticated private control plane."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import StreamingResponse

from ancestryllm.api.capabilities import (
    ModuleDescriptorRegistry,
    capability_manifest,
    health_response,
)
from ancestryllm.api.contracts import (
    API_BUILD_HEADER,
    API_CONTRACT,
    API_NAMESPACE,
    API_VERSION_HEADER,
    CapabilityManifest,
    ChatCapability,
    ChatEvent,
    ChatRunRequest,
    ChatRunSummary,
    ChatSession,
    ChatSessionCreateRequest,
    ChatStreamRun,
    ConsentCreateRequest,
    ConsentGrantResponse,
    ConsentPreviewRequest,
    ConsentPreviewResponse,
    EndpointValidationRequest,
    EndpointValidationResponse,
    ErrorEnvelope,
    HealthResponse,
    JobArtifactResponse,
    JobEventResponse,
    JobListResponse,
    JobProgressResponse,
    JobShutdownRequest,
    JobShutdownResponse,
    JobSnapshotResponse,
    JobStreamFailureResponse,
    PageMetadata,
    PaginationRequest,
    ProviderConfigurationMutationRequest,
    ProviderConfigurationResponse,
    ProviderProfileCreateRequest,
    ProviderProfileResponse,
    SecretSetRequest,
    SecretStatusResponse,
    SettingFieldResponse,
    SettingsPatchRequest,
    SettingsResponse,
    SettingValidationResponse,
    StartupDiagnosticComponentResponse,
    StartupDiagnosticReportResponse,
    StartupPlatformResponse,
)
from ancestryllm.api.errors import error_response, request_error
from ancestryllm.api.middleware import InternalApiMiddleware
from ancestryllm.core.errors import AncestryError, StorageError
from ancestryllm.llm.contracts import DataClass
from ancestryllm.llm.provider_configuration import ConsentPreview
from ancestryllm.storage.diagnostics import (
    StartupDiagnosticComponent,
    StartupDiagnosticReport,
    StartupPlatformDetails,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from fastapi.responses import JSONResponse

    from ancestryllm.api.settings import ApiSettings
    from ancestryllm.application.executor import CommandExecutor
    from ancestryllm.application.jobs import (
        JobEvent,
        JobLifecycleService,
        PublicJobProgress,
        PublicJobSnapshot,
        ShutdownAssessment,
    )
    from ancestryllm.application.secret_management import SecretManagementService, SecretStatus
    from ancestryllm.application.settings import SettingField, SettingsService, SettingsSnapshot
    from ancestryllm.llm.chat import ChatService
    from ancestryllm.llm.chat_streaming import ChatStreamingService
    from ancestryllm.llm.endpoint_validation import (
        EndpointValidationResult,
        EndpointValidationService,
    )
    from ancestryllm.llm.provider_configuration import (
        ConsentGrantSummary,
        ProviderConfigurationService,
        ProviderConfigurationSnapshot,
        ProviderProfileSummary,
    )


class ApiLifecycle(Protocol):
    """Adapter hook that a later sidecar supervisor can use for orderly startup and shutdown."""

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "The request failed closed."},
    401: {"model": ErrorEnvelope, "description": "The private bearer is invalid."},
    403: {
        "model": ErrorEnvelope,
        "description": "Cloud-provider consent is absent, stale, mismatched, or revoked.",
    },
    404: {"model": ErrorEnvelope, "description": "The resource or route is unavailable."},
    405: {"model": ErrorEnvelope, "description": "The method is not accepted."},
    409: {
        "model": ErrorEnvelope,
        "description": "The request conflicts with the current protected state.",
    },
    410: {
        "model": ErrorEnvelope,
        "description": "The bounded replay window has expired; fetch a fresh snapshot.",
    },
    413: {"model": ErrorEnvelope, "description": "The request is too large."},
    415: {"model": ErrorEnvelope, "description": "The content type is not accepted."},
    429: {
        "model": ErrorEnvelope,
        "description": "A bounded queue or subscriber limit was reached.",
    },
    500: {"model": ErrorEnvelope, "description": "The request failed safely."},
    503: {"model": ErrorEnvelope, "description": "A protected local dependency is unavailable."},
}
_HANDSHAKE_PARAMETERS: list[dict[str, object]] = [
    {
        "in": "header",
        "name": API_VERSION_HEADER,
        "required": True,
        "schema": {"type": "string", "const": API_CONTRACT},
    },
    {
        "in": "header",
        "name": API_BUILD_HEADER,
        "required": True,
        "schema": {"type": "string", "minLength": 1, "maxLength": 128},
    },
]
_JOB_EVENT_PARAMETERS = [
    *_HANDSHAKE_PARAMETERS,
    {
        "in": "header",
        "name": "Last-Event-ID",
        "required": False,
        "schema": {
            "type": "string",
            "pattern": r"^[0-9]{1,10}$",
            "description": "Last durably processed job-event sequence; defaults to zero.",
        },
    },
]
_CHAT_EVENT_PARAMETERS = [
    *_HANDSHAKE_PARAMETERS,
    {
        "in": "header",
        "name": "Last-Event-ID",
        "required": False,
        "schema": {
            "type": "string",
            "pattern": r"^[0-9]{1,19}$",
            "description": "Last durably processed chat-event sequence; defaults to zero.",
        },
    },
]
_TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}


def _correlation_ref(request: Request) -> str:
    return cast("str", request.state.correlation_ref)


def _setting_field_response(field: SettingField) -> SettingFieldResponse:
    return SettingFieldResponse(
        key=field.key,
        label=field.label,
        help=field.help,
        type=field.type,
        value=field.value,
        default_value=field.default_value,
        validation=SettingValidationResponse(
            allowed_values=field.validation.allowed_values,
            minimum=field.validation.minimum,
            maximum=field.validation.maximum,
        ),
        restart_required=field.restart_required,
        sensitive=False,
    )


def _settings_response(snapshot: SettingsSnapshot) -> SettingsResponse:
    return SettingsResponse(
        schema_version=snapshot.schema_version,
        revision=snapshot.revision,
        fields=tuple(_setting_field_response(field) for field in snapshot.fields),
    )


def _secret_status_response(status: SecretStatus) -> SecretStatusResponse:
    return SecretStatusResponse(reference=status.reference, status=status.status)


def _provider_profile_response(profile: ProviderProfileSummary) -> ProviderProfileResponse:
    return ProviderProfileResponse(
        name=profile.name,
        provider_id=cast("Any", profile.provider_id),
        model=profile.model,
        endpoint=profile.endpoint,
        endpoint_kind=cast("Any", profile.endpoint_kind),
        secret_reference=profile.secret_reference,
        enabled=profile.enabled,
    )


def _consent_grant_response(consent: ConsentGrantSummary) -> ConsentGrantResponse:
    return ConsentGrantResponse(
        name=consent.name,
        provider_profile_name=consent.provider_profile_name,
        provider_id=cast("Any", consent.provider_id),
        modules=consent.modules,
        purposes=consent.purposes,
        data_classes=cast("Any", consent.data_classes),
        models=consent.models,
        max_cost_usd=consent.max_cost_usd,
        retain_payloads=consent.retain_payloads,
        active=consent.active,
    )


def _provider_configuration_response(
    snapshot: ProviderConfigurationSnapshot,
) -> ProviderConfigurationResponse:
    return ProviderConfigurationResponse(
        schema_version=1,
        revision=snapshot.revision,
        profiles=tuple(_provider_profile_response(item) for item in snapshot.profiles),
        consents=tuple(_consent_grant_response(item) for item in snapshot.consents),
    )


def _consent_preview_response(preview: ConsentPreview) -> ConsentPreviewResponse:
    return ConsentPreviewResponse(
        schema_version=1,
        provider_profile_name=preview.provider_profile_name,
        provider_id=cast("Any", preview.provider_id),
        modules=preview.modules,
        purposes=preview.purposes,
        data_classes=cast("Any", preview.data_classes),
        models=preview.models,
        max_cost_usd=preview.max_cost_usd,
        retain_payloads=preview.retain_payloads,
        warning_codes=preview.warning_codes,
    )


def _endpoint_validation_response(
    result: EndpointValidationResult,
) -> EndpointValidationResponse:
    return EndpointValidationResponse(
        schema_version=1,
        status=cast("Any", result.status),
        endpoint_kind=cast("Any", result.endpoint_kind),
        http_status=result.http_status,
        destination_digest=result.destination_digest,
    )


def _job_progress_response(progress: PublicJobProgress) -> JobProgressResponse:
    return JobProgressResponse(
        schema_version=1,
        operation=progress.operation,
        timestamp=progress.timestamp,
        completed=progress.completed,
        total=progress.total,
    )


def _job_snapshot_response(snapshot: PublicJobSnapshot) -> JobSnapshotResponse:
    artifact = snapshot.artifact
    return JobSnapshotResponse(
        schema_version=1,
        sequence=snapshot.sequence,
        job_id=snapshot.job_id,
        name=snapshot.name,
        state=cast("Any", snapshot.state.value),
        submitted_at=snapshot.submitted_at,
        started_at=snapshot.started_at,
        finished_at=snapshot.finished_at,
        resource_refs=snapshot.resource_refs,
        artifact=(
            JobArtifactResponse(
                artifact_id=artifact.artifact_id,
                media_type=artifact.media_type,
                artifact_type=artifact.artifact_type,
                size_bytes=artifact.size_bytes,
                status=cast("Any", artifact.status.value),
                sha256=artifact.sha256,
            )
            if artifact is not None
            else None
        ),
        outcome_summary=snapshot.outcome_summary,
        next_action=snapshot.next_action,
        error_code=snapshot.error_code,
        error_message=snapshot.error_message,
        error_remediation=snapshot.error_remediation,
        progress=(
            _job_progress_response(snapshot.progress) if snapshot.progress is not None else None
        ),
        cancellation_requested_at=snapshot.cancellation_requested_at,
        cancellation_deferred_by=snapshot.cancellation_deferred_by,
    )


def _job_event_response(event: JobEvent) -> JobEventResponse:
    return JobEventResponse(
        schema_version=1,
        sequence=event.sequence,
        kind=cast("Any", event.kind.value),
        created_at=event.created_at,
        snapshot=_job_snapshot_response(event.snapshot),
    )


def _job_shutdown_response(assessment: ShutdownAssessment) -> JobShutdownResponse:
    return JobShutdownResponse(
        schema_version=1,
        safe_to_quit=assessment.safe_to_quit,
        active_jobs=tuple(_job_snapshot_response(item) for item in assessment.active_jobs),
    )


def _sse_record(event: JobEvent) -> str:
    payload = json.dumps(
        _job_event_response(event).model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"id: {event.sequence}\nevent: {event.kind.value}\ndata: {payload}\n\n"


def _sse_resync_required() -> str:
    payload = JobStreamFailureResponse(
        schema_version=1,
        code="JOB_EVENT_REPLAY_EXPIRED",
        message="The bounded job-event replay window is no longer available.",
        remediation="Fetch the current job snapshot, then reconnect from its sequence.",
    )
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"event: resync-required\ndata: {encoded}\n\n"


def _acknowledged_sequence(request: Request) -> int:
    raw = request.headers.get("last-event-id")
    if raw is None:
        return 0
    if not 1 <= len(raw) <= 10 or not raw.isascii() or not raw.isdecimal():
        raise AncestryError(
            "JOB_EVENT_CURSOR_INVALID",
            "The acknowledged job-event sequence is invalid.",
            exit_code=2,
        )
    value = int(raw)
    if value > 9_999_999_999:
        raise AncestryError(
            "JOB_EVENT_CURSOR_INVALID",
            "The acknowledged job-event sequence is invalid.",
            exit_code=2,
        )
    return value


def _chat_acknowledged_sequence(request: Request) -> int:
    raw = request.headers.get("last-event-id")
    if raw is None:
        return 0
    if not 1 <= len(raw) <= 19 or not raw.isascii() or not raw.isdecimal():
        raise AncestryError(
            "CHAT_STREAM_CURSOR_INVALID",
            "The acknowledged chat-event sequence is invalid.",
            exit_code=2,
        )
    value = int(raw)
    if value > 2**63 - 1:
        raise AncestryError(
            "CHAT_STREAM_CURSOR_INVALID",
            "The acknowledged chat-event sequence is invalid.",
            exit_code=2,
        )
    return value


def _chat_sse_record(event: object) -> str:
    response = ChatEvent.from_application(cast("Any", event))
    payload = json.dumps(
        response.model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"id: {response.sequence}\nevent: {response.type}\ndata: {payload}\n\n"


def _default_startup_diagnostics() -> StartupDiagnosticReport:
    components = tuple(
        StartupDiagnosticComponent(
            component=component,
            status="ready",
            code=code,
            message=message,
            remediation=None,
            restart_required=False,
            blocks_mutations=False,
        )
        for component, code, message in (
            ("configuration", "CONFIGURATION_READY", "The desktop configuration is ready."),
            ("sqlcipher", "SQLCIPHER_READY", "SQLCipher encryption support is available."),
            ("keyring", "KEYRING_READY", "The credential store is ready."),
            ("workspace", "DATABASE_DIRECTORY_READY", "The local workspace is ready."),
        )
    )
    return StartupDiagnosticReport(
        schema_version=1,
        status="ready",
        platform=StartupPlatformDetails("unsupported", "unsupported"),
        components=components,
    )


def _startup_diagnostics_response(
    report: StartupDiagnosticReport,
) -> StartupDiagnosticReportResponse:
    return StartupDiagnosticReportResponse(
        schema_version=1,
        status=cast("Any", report.status),
        platform=StartupPlatformResponse(
            operating_system=cast("Any", report.platform.operating_system),
            architecture=cast("Any", report.platform.architecture),
        ),
        components=tuple(
            StartupDiagnosticComponentResponse(
                component=cast("Any", component.component),
                status=cast("Any", component.status),
                code=component.code,
                message=component.message,
                remediation=component.remediation,
                restart_required=component.restart_required,
                blocks_mutations=component.blocks_mutations,
            )
            for component in report.components
        ),
    )


class InternalApiApplication(FastAPI):
    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is None:
            schema = get_openapi(
                title=self.title,
                version=self.version,
                openapi_version=self.openapi_version,
                summary=self.summary,
                description=self.description,
                routes=self.routes,
            )
            components = schema.setdefault("components", {})
            cast("dict[str, object]", components)["securitySchemes"] = {
                "PrivateBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Fresh per-launch 256-bit bearer supplied privately by Electron main.",
                }
            }
            schemas = cast("dict[str, object]", components.setdefault("schemas", {}))
            for model in (PaginationRequest, PageMetadata):
                model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
                definitions = cast("dict[str, object]", model_schema.pop("$defs", {}))
                schemas.update(definitions)
                schemas[model.__name__] = model_schema
            schema["security"] = [{"PrivateBearer": []}]
            schema["x-ancestryllm-internal"] = True
            self.openapi_schema = schema
        return self.openapi_schema


def create_app(
    *,
    settings: ApiSettings,
    registry: ModuleDescriptorRegistry,
    executor: CommandExecutor,
    settings_service: SettingsService,
    secret_service: SecretManagementService,
    provider_configuration_service: ProviderConfigurationService | None = None,
    endpoint_validation_service: EndpointValidationService | None = None,
    chat_service: ChatService | None = None,
    chat_streaming_service: ChatStreamingService | None = None,
    job_service: Callable[[], JobLifecycleService] | None = None,
    job_shutdown: Callable[[str, float], ShutdownAssessment] | None = None,
    lifecycle: ApiLifecycle | None = None,
    startup_diagnostics: Callable[[], StartupDiagnosticReport] | None = None,
    mutations_allowed: Callable[[], bool] | None = None,
    surface: Literal["control", "probe"] = "control",
) -> FastAPI:
    """Create the internal control surface over existing application contracts."""

    if surface not in {"control", "probe"}:
        raise ValueError("API_SURFACE_INVALID: the API surface must be control or probe")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if lifecycle is not None:
            await lifecycle.startup()
        try:
            yield
        finally:
            if lifecycle is not None:
                await lifecycle.shutdown()

    app = InternalApiApplication(
        title="AncestryLLM Internal API",
        summary="Private loopback control plane for the AncestryLLM desktop application.",
        description="Authenticated internal discovery only. This is not a public, LAN, or browser API.",
        version="1.0.0",
        openapi_version="3.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    app.add_middleware(InternalApiMiddleware, settings=settings, surface=surface)
    diagnostics_provider = startup_diagnostics or _default_startup_diagnostics

    def assert_mutations_allowed() -> None:
        allowed = mutations_allowed() if mutations_allowed is not None else True
        if not allowed:
            raise StorageError(
                "STARTUP_MUTATION_BLOCKED",
                "Changes are disabled while startup diagnostics are degraded.",
                "Resolve the blocked startup checks and retry diagnostics before making changes.",
            )

    def provider_configuration() -> ProviderConfigurationService:
        if provider_configuration_service is None:
            raise StorageError(
                "PROVIDER_CONFIGURATION_UNAVAILABLE",
                "Provider configuration is unavailable.",
                "Restart the desktop application and retry.",
            )
        return provider_configuration_service

    def endpoint_validation() -> EndpointValidationService:
        if endpoint_validation_service is None:
            raise StorageError(
                "ENDPOINT_VALIDATION_UNAVAILABLE",
                "Endpoint validation is unavailable.",
                "Restart the desktop application and retry.",
            )
        return endpoint_validation_service

    def chat() -> ChatService:
        if chat_service is None:
            raise StorageError(
                "CHAT_SERVICE_UNAVAILABLE",
                "Transient chat is unavailable.",
                "Resolve startup diagnostics and restart the desktop application.",
            )
        return chat_service

    def chat_streaming() -> ChatStreamingService:
        if chat_streaming_service is None:
            raise StorageError(
                "CHAT_STREAM_SERVICE_UNAVAILABLE",
                "Transient chat streaming is unavailable.",
                "Resolve startup diagnostics and restart the desktop application.",
            )
        return chat_streaming_service

    def jobs() -> JobLifecycleService:
        if job_service is None:
            raise StorageError(
                "JOB_SERVICE_UNAVAILABLE",
                "Background-job coordination is unavailable.",
                "Resolve startup diagnostics and restart the desktop application.",
            )
        return job_service()

    def prepare_jobs_for_shutdown(action: str, timeout_seconds: float) -> ShutdownAssessment:
        if job_shutdown is not None:
            return job_shutdown(action, timeout_seconds)
        return jobs().prepare_shutdown(action=action, timeout_seconds=timeout_seconds)

    @app.exception_handler(AncestryError)
    async def handle_ancestry_error(request: Request, error: AncestryError) -> JSONResponse:
        return error_response(error, correlation_ref=_correlation_ref(request))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            request_error(400, "REQUEST_INVALID", "The internal API request is invalid."),
            correlation_ref=_correlation_ref(request),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        return error_response(error, correlation_ref=_correlation_ref(request))

    @app.get(
        f"{API_NAMESPACE}/health",
        response_model=HealthResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="getInternalHealth",
        tags=["control"],
    )
    def get_health() -> HealthResponse:
        return health_response(settings)

    @app.get(
        f"{API_NAMESPACE}/capabilities",
        response_model=CapabilityManifest,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="getInternalCapabilities",
        tags=["control"],
    )
    def get_capabilities() -> CapabilityManifest:
        return capability_manifest(registry, executor, settings)

    if surface == "probe":
        return app

    @app.get(
        f"{API_NAMESPACE}/startup-diagnostics",
        response_model=StartupDiagnosticReportResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="getInternalStartupDiagnostics",
        tags=["control"],
    )
    def get_startup_diagnostics() -> StartupDiagnosticReportResponse:
        return _startup_diagnostics_response(diagnostics_provider())

    @app.get(
        f"{API_NAMESPACE}/chat/capability",
        response_model=ChatCapability,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="getInternalChatCapability",
        tags=["chat"],
    )
    def get_chat_capability() -> ChatCapability:
        return ChatCapability.from_application(chat().capability())

    @app.post(
        f"{API_NAMESPACE}/chat/sessions",
        response_model=ChatSession,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="startInternalChatSession",
        tags=["chat"],
    )
    def start_chat_session(request: ChatSessionCreateRequest) -> ChatSession:
        assert_mutations_allowed()
        return ChatSession.from_application(chat().start(request.to_application()))

    @app.get(
        f"{API_NAMESPACE}/chat/sessions/{{session_id}}",
        response_model=ChatSession,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="getInternalChatSession",
        tags=["chat"],
    )
    def get_chat_session(session_id: str) -> ChatSession:
        return ChatSession.from_application(chat().get(session_id))

    @app.delete(
        f"{API_NAMESPACE}/chat/sessions/{{session_id}}",
        status_code=204,
        response_class=Response,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="deleteInternalChatSession",
        tags=["chat"],
    )
    def delete_chat_session(session_id: str) -> Response:
        assert_mutations_allowed()
        chat().teardown(session_id)
        return Response(status_code=204)

    @app.post(
        f"{API_NAMESPACE}/chat/sessions/{{session_id}}/runs",
        response_model=ChatRunSummary,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="runInternalChatSession",
        tags=["chat"],
    )
    def run_chat_session(session_id: str, request: ChatRunRequest) -> ChatRunSummary:
        assert_mutations_allowed()
        return ChatRunSummary.from_application(chat().run(session_id, request.to_application()))

    @app.post(
        f"{API_NAMESPACE}/chat/sessions/{{session_id}}/streams",
        response_model=ChatStreamRun,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="startInternalChatStream",
        tags=["chat"],
    )
    async def start_chat_stream(session_id: str, request: ChatRunRequest) -> ChatStreamRun:
        assert_mutations_allowed()
        run = await chat_streaming().start(session_id, request.to_application())
        return ChatStreamRun.from_application(run)

    @app.get(
        f"{API_NAMESPACE}/chat/sessions/{{session_id}}/streams/{{run_id}}/events",
        response_class=StreamingResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _CHAT_EVENT_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="streamInternalChatEvents",
        tags=["chat"],
    )
    async def stream_chat_events(
        request: Request,
        session_id: str,
        run_id: str,
    ) -> StreamingResponse:
        subscription = await chat_streaming().subscribe(
            session_id,
            run_id,
            after_sequence=_chat_acknowledged_sequence(request),
        )

        async def event_stream() -> AsyncIterator[str]:
            async for event in subscription:
                yield _chat_sse_record(event)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post(
        f"{API_NAMESPACE}/chat/sessions/{{session_id}}/streams/{{run_id}}/cancel",
        response_model=ChatStreamRun,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="cancelInternalChatStream",
        tags=["chat"],
    )
    async def cancel_chat_stream(session_id: str, run_id: str) -> ChatStreamRun:
        assert_mutations_allowed()
        run = await chat_streaming().cancel(session_id, run_id)
        return ChatStreamRun.from_application(run)

    @app.get(
        f"{API_NAMESPACE}/jobs",
        response_model=JobListResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="listInternalJobs",
        tags=["jobs"],
    )
    def list_jobs() -> JobListResponse:
        return JobListResponse(
            schema_version=1,
            jobs=tuple(_job_snapshot_response(item) for item in jobs().list(limit=100)),
        )

    @app.post(
        f"{API_NAMESPACE}/jobs/shutdown",
        response_model=JobShutdownResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="prepareInternalJobShutdown",
        tags=["jobs"],
    )
    def prepare_job_shutdown(request: JobShutdownRequest) -> JobShutdownResponse:
        return _job_shutdown_response(
            prepare_jobs_for_shutdown(request.action, request.timeout_seconds)
        )

    @app.get(
        f"{API_NAMESPACE}/jobs/{{job_id}}",
        response_model=JobSnapshotResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="getInternalJob",
        tags=["jobs"],
    )
    def get_job(job_id: str) -> JobSnapshotResponse:
        return _job_snapshot_response(jobs().get(job_id))

    @app.post(
        f"{API_NAMESPACE}/jobs/{{job_id}}/cancel",
        response_model=JobSnapshotResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="cancelInternalJob",
        tags=["jobs"],
    )
    def cancel_job(job_id: str) -> JobSnapshotResponse:
        assert_mutations_allowed()
        return _job_snapshot_response(jobs().cancel(job_id))

    @app.get(
        f"{API_NAMESPACE}/jobs/{{job_id}}/events",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "A bounded replay followed by live job events.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            },
            **_ERROR_RESPONSES,
        },
        openapi_extra={"parameters": _JOB_EVENT_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="streamInternalJobEvents",
        tags=["jobs"],
    )
    def stream_job_events(job_id: str, request: Request) -> StreamingResponse:
        service = jobs()
        subscription = service.subscribe(job_id, after=_acknowledged_sequence(request))

        def event_stream() -> Iterator[str]:
            try:
                for event in subscription.replay.events:
                    yield _sse_record(event)
                    if event.snapshot.state.value in _TERMINAL_JOB_STATES:
                        return
                if service.get(job_id).state.value in _TERMINAL_JOB_STATES:
                    return
                while True:
                    try:
                        event = subscription.next(timeout=15.0)
                    except AncestryError as error:
                        if error.code == "JOB_EVENT_WAIT_TIMEOUT":
                            yield ": keep-alive\n\n"
                            continue
                        if error.code == "JOB_EVENT_REPLAY_EXPIRED":
                            yield _sse_resync_required()
                            return
                        raise
                    yield _sse_record(event)
                    if event.snapshot.state.value in _TERMINAL_JOB_STATES:
                        return
            finally:
                subscription.close()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(
        f"{API_NAMESPACE}/settings",
        response_model=SettingsResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="getInternalSettings",
        tags=["settings"],
    )
    def get_settings() -> SettingsResponse:
        return _settings_response(settings_service.snapshot())

    @app.patch(
        f"{API_NAMESPACE}/settings",
        response_model=SettingsResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="patchInternalSettings",
        tags=["settings"],
    )
    def patch_settings(request: SettingsPatchRequest) -> SettingsResponse:
        assert_mutations_allowed()
        return _settings_response(
            settings_service.patch(
                schema_version=request.schema_version,
                expected_revision=request.expected_revision,
                changes=request.changes,
            )
        )

    @app.get(
        f"{API_NAMESPACE}/secrets/{{reference}}/status",
        response_model=SecretStatusResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="getInternalSecretStatus",
        tags=["secrets"],
    )
    def get_secret_status(reference: str) -> SecretStatusResponse:
        return _secret_status_response(secret_service.status(reference))

    @app.post(
        f"{API_NAMESPACE}/secrets/{{reference}}/set",
        response_model=SecretStatusResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="setInternalSecret",
        tags=["secrets"],
    )
    def set_secret(reference: str, request: SecretSetRequest) -> SecretStatusResponse:
        assert_mutations_allowed()
        return _secret_status_response(secret_service.set(reference, request.value))

    @app.post(
        f"{API_NAMESPACE}/secrets/{{reference}}/delete",
        response_model=SecretStatusResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="deleteInternalSecret",
        tags=["secrets"],
    )
    def delete_secret(reference: str) -> SecretStatusResponse:
        assert_mutations_allowed()
        return _secret_status_response(secret_service.delete(reference))

    @app.get(
        f"{API_NAMESPACE}/provider-configuration",
        response_model=ProviderConfigurationResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="getInternalProviderConfiguration",
        tags=["provider-configuration"],
    )
    def get_provider_configuration() -> ProviderConfigurationResponse:
        return _provider_configuration_response(provider_configuration().snapshot())

    @app.post(
        f"{API_NAMESPACE}/provider-profiles",
        response_model=ProviderConfigurationResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="createInternalProviderProfile",
        tags=["provider-configuration"],
    )
    def create_provider_profile(
        request: ProviderProfileCreateRequest,
    ) -> ProviderConfigurationResponse:
        assert_mutations_allowed()
        snapshot = provider_configuration().create_profile(
            expected_revision=request.expected_revision,
            name=request.name,
            provider_id=request.provider_id,
            model=request.model,
            endpoint=request.endpoint,
            endpoint_identity_sha256=request.endpoint_identity_sha256,
        )
        return _provider_configuration_response(snapshot)

    @app.post(
        f"{API_NAMESPACE}/provider-endpoints/validate",
        response_model=EndpointValidationResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="validateInternalProviderEndpoint",
        tags=["provider-configuration"],
    )
    def validate_provider_endpoint(
        request: EndpointValidationRequest,
    ) -> EndpointValidationResponse:
        assert_mutations_allowed()
        result = endpoint_validation().validate(request.provider_id, request.endpoint)
        return _endpoint_validation_response(result)

    @app.post(
        f"{API_NAMESPACE}/consents/preview",
        response_model=ConsentPreviewResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="previewInternalConsent",
        tags=["provider-configuration"],
    )
    def preview_consent(request: ConsentPreviewRequest) -> ConsentPreviewResponse:
        preview = provider_configuration().preview_consent(
            provider_profile_name=request.provider_profile_name,
            modules=request.modules,
            purposes=request.purposes,
            data_classes=(DataClass(item) for item in request.data_classes),
            models=request.models,
            max_cost_usd=request.max_cost_usd,
            retain_payloads=request.retain_payloads,
        )
        return _consent_preview_response(preview)

    @app.post(
        f"{API_NAMESPACE}/consents",
        response_model=ProviderConfigurationResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="createInternalConsent",
        tags=["provider-configuration"],
    )
    def create_consent(request: ConsentCreateRequest) -> ProviderConfigurationResponse:
        assert_mutations_allowed()
        supplied = request.preview
        preview = ConsentPreview(
            schema_version=1,
            provider_profile_name=supplied.provider_profile_name,
            provider_id=supplied.provider_id,
            modules=tuple(supplied.modules),
            purposes=tuple(supplied.purposes),
            data_classes=tuple(supplied.data_classes),
            models=tuple(supplied.models),
            max_cost_usd=supplied.max_cost_usd,
            retain_payloads=supplied.retain_payloads,
            warning_codes=tuple(supplied.warning_codes),
        )
        snapshot = provider_configuration().create_consent(
            expected_revision=request.expected_revision,
            name=request.name,
            preview=preview,
        )
        return _provider_configuration_response(snapshot)

    @app.post(
        f"{API_NAMESPACE}/consents/{{name}}/revoke",
        response_model=ProviderConfigurationResponse,
        responses=_ERROR_RESPONSES,
        openapi_extra={"parameters": _HANDSHAKE_PARAMETERS, "security": [{"PrivateBearer": []}]},
        operation_id="revokeInternalConsent",
        tags=["provider-configuration"],
    )
    def revoke_consent(
        name: str,
        request: ProviderConfigurationMutationRequest,
    ) -> ProviderConfigurationResponse:
        assert_mutations_allowed()
        snapshot = provider_configuration().revoke_consent(
            expected_revision=request.expected_revision,
            name=name,
        )
        return _provider_configuration_response(snapshot)

    return app


__all__ = ["ApiLifecycle", "InternalApiApplication", "create_app"]
