"""FastAPI composition for the authenticated private control plane."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

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
    ErrorEnvelope,
    HealthResponse,
    PageMetadata,
    PaginationRequest,
)
from ancestryllm.api.errors import error_response, request_error
from ancestryllm.api.middleware import InternalApiMiddleware
from ancestryllm.api.settings import ApiSettings
from ancestryllm.application.executor import CommandExecutor
from ancestryllm.core.errors import AncestryError


class ApiLifecycle(Protocol):
    """Adapter hook that a later sidecar supervisor can use for orderly startup and shutdown."""

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "The request failed closed."},
    401: {"model": ErrorEnvelope, "description": "The private bearer is invalid."},
    404: {"model": ErrorEnvelope, "description": "The route is not exposed."},
    405: {"model": ErrorEnvelope, "description": "The method is not accepted."},
    409: {"model": ErrorEnvelope, "description": "The paired build identity differs."},
    413: {"model": ErrorEnvelope, "description": "The request is too large."},
    415: {"model": ErrorEnvelope, "description": "The content type is not accepted."},
    500: {"model": ErrorEnvelope, "description": "The request failed safely."},
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
    return cast(str, request.state.correlation_ref)


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
            cast(dict[str, object], components)["securitySchemes"] = {
                "PrivateBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Fresh per-launch 256-bit bearer supplied privately by Electron main.",
                }
            }
            schemas = cast(dict[str, object], components.setdefault("schemas", {}))
            for model in (PaginationRequest, PageMetadata):
                model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
                definitions = cast(dict[str, object], model_schema.pop("$defs", {}))
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
    lifecycle: ApiLifecycle | None = None,
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
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    app.add_middleware(InternalApiMiddleware, settings=settings)

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

    return app


__all__ = ["ApiLifecycle", "InternalApiApplication", "create_app"]
