"""FastAPI composition for the authenticated private control plane."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi

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
    ConsentCreateRequest,
    ConsentGrantResponse,
    ConsentPreviewRequest,
    ConsentPreviewResponse,
    EndpointValidationRequest,
    EndpointValidationResponse,
    ErrorEnvelope,
    HealthResponse,
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
    from collections.abc import AsyncIterator, Callable

    from fastapi.responses import JSONResponse

    from ancestryllm.api.settings import ApiSettings
    from ancestryllm.application.executor import CommandExecutor
    from ancestryllm.application.secret_management import SecretManagementService, SecretStatus
    from ancestryllm.application.settings import SettingField, SettingsService, SettingsSnapshot
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
    404: {"model": ErrorEnvelope, "description": "The route is not exposed."},
    405: {"model": ErrorEnvelope, "description": "The method is not accepted."},
    409: {
        "model": ErrorEnvelope,
        "description": "The request conflicts with the current protected state.",
    },
    413: {"model": ErrorEnvelope, "description": "The request is too large."},
    415: {"model": ErrorEnvelope, "description": "The content type is not accepted."},
    500: {"model": ErrorEnvelope, "description": "The request failed safely."},
    503: {"model": ErrorEnvelope, "description": "Secure credential storage is unavailable."},
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
    lifecycle: ApiLifecycle | None = None,
    startup_diagnostics: Callable[[], StartupDiagnosticReport] | None = None,
    mutations_allowed: Callable[[], bool] | None = None,
) -> FastAPI:
    """Create the internal control surface over existing application contracts."""

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
    app.add_middleware(InternalApiMiddleware, settings=settings)
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
