"""Read-only capability discovery derived from shared command contracts."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Protocol

from ancestryllm.api.contracts import (
    API_CONTRACT,
    ApiVersion,
    CapabilityAction,
    CapabilityManifest,
    CapabilityModule,
    HealthResponse,
    PaginationPolicy,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ancestryllm.api.settings import ApiSettings
    from ancestryllm.application.executor import CommandExecutor
    from ancestryllm.core.commands import ModuleDescriptor


class ModuleDescriptorRegistry(Protocol):
    """Define read-only lookup for registered capability descriptors."""

    def descriptors(self) -> Sequence[ModuleDescriptor]:
        """Return the descriptors exposed by the module descriptor registry."""
        ...


def capability_manifest(
    registry: ModuleDescriptorRegistry, executor: CommandExecutor, settings: ApiSettings
) -> CapabilityManifest:
    """Build the capability manifest from registered command descriptors."""
    registered = frozenset(executor.dispatch_keys)
    modules: list[CapabilityModule] = []
    for descriptor in sorted(registry.descriptors(), key=lambda item: item.module_id):
        actions = tuple(
            CapabilityAction(
                dispatch_key=route.key.value, name=route.action.name, summary=route.action.help
            )
            for route in descriptor.command.routes
            if route.key in registered
        )
        if actions:
            modules.append(
                CapabilityModule(
                    module_id=descriptor.module_id,
                    name=descriptor.name,
                    summary=descriptor.summary,
                    actions=actions,
                )
            )
    return CapabilityManifest(
        api=ApiVersion(),
        modules=tuple(modules),
        request_policy=settings.request_policy,
        pagination=PaginationPolicy(),
    )


def health_response(settings: ApiSettings) -> HealthResponse:
    """Build a sanitized health response for the internal API."""
    proof_payload = f"{API_CONTRACT}\n{settings.app_build}\n{settings.sidecar_build}".encode()
    proof = hmac.new(settings.bearer_token.encode(), proof_payload, hashlib.sha256).hexdigest()
    return HealthResponse(
        api=ApiVersion(),
        app_build=settings.app_build,
        sidecar_build=settings.sidecar_build,
        readiness_proof=proof,
    )


__all__ = ["ModuleDescriptorRegistry", "capability_manifest", "health_response"]
