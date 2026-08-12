"""Consent, endpoint, and data-class policy enforced before remote calls."""

from __future__ import annotations

import fnmatch
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

from ancestryllm.core.errors import SecurityPolicyError
from ancestryllm.llm.contracts import DataClass, GenerationRequest, ProviderCapabilities

REMOTE_ENDPOINTS = {
    "openai": frozenset({"api.openai.com"}),
    "anthropic": frozenset({"api.anthropic.com"}),
    "gemini": frozenset({"generativelanguage.googleapis.com"}),
    "openrouter": frozenset({"openrouter.ai"}),
}

DEFAULT_PROVIDER_ENDPOINTS = {
    "ollama": "http://127.0.0.1:11434",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "openrouter": "https://openrouter.ai/api/v1",
}


@dataclass(frozen=True, slots=True)
class ConsentGrant:
    consent_id: str
    provider_id: str
    allowed_modules: frozenset[str]
    allowed_purposes: frozenset[str]
    allowed_data_classes: frozenset[DataClass]
    model_allowlist: tuple[str, ...]
    max_cost_usd: float | None = None
    retain_payloads: bool = False
    active: bool = True
    provider_profile_name: str | None = None


def endpoint_is_loopback(endpoint: str) -> bool:
    """Return whether an endpoint names an explicit loopback host."""

    hostname = urlparse(endpoint).hostname
    if not hostname:
        return False
    normalized = hostname.casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_endpoint(provider_id: str, endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if provider_id == "ollama":
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SecurityPolicyError("ENDPOINT_REJECTED", "The Ollama endpoint is invalid.")
        if endpoint_is_loopback(endpoint):
            return
        if parsed.scheme != "https":
            raise SecurityPolicyError(
                "ENDPOINT_REJECTED",
                "A non-loopback Ollama endpoint must use HTTPS.",
            )
        return
    if parsed.scheme != "https" or not parsed.hostname:
        raise SecurityPolicyError(
            "ENDPOINT_REJECTED",
            "Remote provider endpoints must use an explicit HTTPS URL.",
        )
    allowed = REMOTE_ENDPOINTS.get(provider_id)
    if allowed is None or parsed.hostname.casefold() not in allowed:
        raise SecurityPolicyError(
            "ENDPOINT_REJECTED",
            "The remote provider endpoint is not on the built-in allowlist.",
        )


class ConsentPolicy:
    """Authorize exactly the requested provider/model/purpose/data combination."""

    def authorize(
        self,
        request: GenerationRequest,
        capabilities: ProviderCapabilities,
        consent: ConsentGrant | None,
    ) -> None:
        if capabilities.provider_id != request.provider_id:
            raise SecurityPolicyError(
                "PROVIDER_MISMATCH", "The selected provider does not match the request."
            )
        if not capabilities.remote and consent is None:
            return
        if consent is None:
            raise SecurityPolicyError(
                "CLOUD_CONSENT_REQUIRED",
                "An active consent profile is required before genealogy data can reach a cloud provider.",
                "Create or select a provider-specific consent profile.",
            )
        if not consent.active:
            raise SecurityPolicyError(
                "CONSENT_INACTIVE",
                "The selected consent profile is revoked or inactive.",
                "Create or select an active consent profile.",
            )
        if consent.provider_id != request.provider_id:
            raise SecurityPolicyError(
                "CONSENT_PROVIDER_MISMATCH", "Consent is for a different provider."
            )
        if consent.provider_profile_name != request.execution.profile_name:
            raise SecurityPolicyError(
                "CONSENT_PROFILE_MISMATCH",
                "Consent is for a different provider profile or endpoint.",
            )
        if request.module_id not in consent.allowed_modules:
            raise SecurityPolicyError(
                "CONSENT_MODULE_DENIED", "Consent does not allow this module."
            )
        if request.purpose not in consent.allowed_purposes:
            raise SecurityPolicyError(
                "CONSENT_PURPOSE_DENIED", "Consent does not allow this purpose."
            )
        if not request.data_classes.issubset(consent.allowed_data_classes):
            denied = sorted(
                item.value for item in request.data_classes - consent.allowed_data_classes
            )
            raise SecurityPolicyError(
                "CONSENT_DATA_DENIED",
                "Consent does not allow every data class in this request.",
                details={"denied": denied},
            )
        if not any(
            fnmatch.fnmatchcase(request.model, pattern) for pattern in consent.model_allowlist
        ):
            raise SecurityPolicyError("CONSENT_MODEL_DENIED", "Consent does not allow this model.")


def default_local_data_classes() -> frozenset[DataClass]:
    return frozenset({DataClass.PUBLIC_GENEALOGY, DataClass.DECEASED_PERSON})
