"""Renderer-safe provider profile and consent configuration facade."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import threading
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from ancestryllm.core.errors import AncestryError, SecurityPolicyError
from ancestryllm.llm.contracts import DataClass
from ancestryllm.llm.policy import (
    DEFAULT_PROVIDER_ENDPOINTS,
    REMOTE_ENDPOINTS,
    endpoint_is_loopback,
    validate_endpoint,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ancestryllm.llm.endpoint_validation import EndpointValidationService
    from ancestryllm.llm.profiles import ProviderProfileService
    from ancestryllm.storage.models import ProviderProfileModel


_LIVING_DATA_CLASSES = frozenset(
    {
        DataClass.LIVING_PERSON,
        DataClass.POSSIBLY_LIVING_PERSON,
        DataClass.GOVERNMENT_IDENTIFIER,
    }
)
_CONFIGURATION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$")


@dataclass(frozen=True, slots=True)
class ProviderProfileSummary:
    name: str
    provider_id: str
    model: str
    endpoint: str
    endpoint_kind: str
    secret_reference: str | None
    enabled: bool


@dataclass(frozen=True, slots=True)
class ConsentGrantSummary:
    name: str
    provider_profile_name: str
    provider_id: str
    modules: tuple[str, ...]
    purposes: tuple[str, ...]
    data_classes: tuple[str, ...]
    models: tuple[str, ...]
    max_cost_usd: float | None
    retain_payloads: bool
    active: bool


@dataclass(frozen=True, slots=True)
class ProviderConfigurationSnapshot:
    schema_version: int
    revision: str
    profiles: tuple[ProviderProfileSummary, ...]
    consents: tuple[ConsentGrantSummary, ...]


@dataclass(frozen=True, slots=True)
class ConsentPreview:
    schema_version: int
    provider_profile_name: str
    provider_id: str
    modules: tuple[str, ...]
    purposes: tuple[str, ...]
    data_classes: tuple[str, ...]
    models: tuple[str, ...]
    max_cost_usd: float | None
    retain_payloads: bool
    warning_codes: tuple[str, ...]


def _normalized_values(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(sorted(set(values)))
    if not normalized or any(
        not value
        or value != value.strip()
        or len(value) > 200
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        for value in normalized
    ):
        raise AncestryError(
            "CONSENT_INVALID",
            f"Consent {field_name} must contain bounded non-empty values.",
        )
    return normalized


def _validated_name(value: str, *, field_name: str) -> str:
    if _CONFIGURATION_NAME.fullmatch(value) is None:
        raise AncestryError(
            "PROVIDER_PROFILE_INVALID" if field_name == "profile" else "CONSENT_INVALID",
            f"The {field_name} name is invalid.",
        )
    return value


def _validated_cost(value: float | None) -> float | None:
    if value is not None and (not math.isfinite(value) or value < 0):
        raise AncestryError(
            "CONSENT_INVALID", "Consent cost limits must be finite and non-negative."
        )
    return value


def _profile_endpoint(profile: ProviderProfileModel) -> str:
    try:
        settings = json.loads(profile.settings_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AncestryError(
            "PROVIDER_PROFILE_INVALID",
            "Provider profile settings are malformed.",
        ) from exc
    if not isinstance(settings, dict):
        raise AncestryError(
            "PROVIDER_PROFILE_INVALID",
            "Provider profile settings are malformed.",
        )
    endpoint = settings.get("base_url", DEFAULT_PROVIDER_ENDPOINTS.get(profile.provider_id))
    if not isinstance(endpoint, str):
        raise AncestryError(
            "PROVIDER_PROFILE_INVALID",
            "Provider profile endpoint is malformed.",
        )
    return endpoint


class ProviderConfigurationService:
    """Serialize desktop mutations over the existing profile and consent services."""

    def __init__(
        self,
        profiles: ProviderProfileService,
        endpoint_validator: EndpointValidationService,
    ) -> None:
        self._profiles = profiles
        self._endpoint_validator = endpoint_validator
        self._mutation_lock = threading.Lock()

    def snapshot(self) -> ProviderConfigurationSnapshot:
        with self._mutation_lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> ProviderConfigurationSnapshot:
        profile_summaries = tuple(
            ProviderProfileSummary(
                name=profile.name,
                provider_id=profile.provider_id,
                model=profile.model,
                endpoint=(endpoint := _profile_endpoint(profile)),
                endpoint_kind="loopback" if endpoint_is_loopback(endpoint) else "remote",
                secret_reference=profile.secret_reference,
                enabled=profile.enabled,
            )
            for profile in self._profiles.list_profiles()
        )
        consent_summaries = []
        for consent in self._profiles.list_consents():
            grant = self._profiles.consent_grant(consent.name)
            consent_summaries.append(
                ConsentGrantSummary(
                    name=consent.name,
                    provider_profile_name=grant.provider_profile_name or "",
                    provider_id=grant.provider_id,
                    modules=tuple(sorted(grant.allowed_modules)),
                    purposes=tuple(sorted(grant.allowed_purposes)),
                    data_classes=tuple(sorted(item.value for item in grant.allowed_data_classes)),
                    models=tuple(sorted(grant.model_allowlist)),
                    max_cost_usd=grant.max_cost_usd,
                    retain_payloads=grant.retain_payloads,
                    active=grant.active,
                )
            )
        canonical = json.dumps(
            {
                "schema_version": 1,
                "profiles": [asdict(item) for item in profile_summaries],
                "consents": [asdict(item) for item in consent_summaries],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return ProviderConfigurationSnapshot(
            schema_version=1,
            revision=hashlib.sha256(canonical).hexdigest(),
            profiles=profile_summaries,
            consents=tuple(consent_summaries),
        )

    @staticmethod
    def _check_revision(current: ProviderConfigurationSnapshot, expected: str) -> None:
        if expected != current.revision:
            raise AncestryError(
                "PROVIDER_CONFIGURATION_CONFLICT",
                "Provider configuration changed before this update could be saved.",
                "Reload the current settings, review them, and retry the update.",
                details={"current_revision": current.revision},
            )

    def create_profile(
        self,
        *,
        expected_revision: str,
        name: str,
        provider_id: str,
        model: str,
        endpoint: str,
        endpoint_identity_sha256: str,
    ) -> ProviderConfigurationSnapshot:
        with self._mutation_lock:
            name = _validated_name(name, field_name="profile")
            if (
                not model
                or model != model.strip()
                or len(model) > 200
                or any(ord(character) < 32 or ord(character) == 127 for character in model)
                or len(endpoint) > 2_048
            ):
                raise AncestryError(
                    "PROVIDER_PROFILE_INVALID",
                    "The provider profile model or endpoint is invalid.",
                )
            current = self._snapshot_unlocked()
            self._check_revision(current, expected_revision)
            validate_endpoint(provider_id, endpoint)
            if provider_id == "ollama" and not endpoint_is_loopback(endpoint):
                raise SecurityPolicyError(
                    "ENDPOINT_REJECTED",
                    "Desktop local-runtime endpoints must use an explicit loopback address.",
                )
            if provider_id in REMOTE_ENDPOINTS:
                default_endpoint = DEFAULT_PROVIDER_ENDPOINTS[provider_id]
                if endpoint != default_endpoint:
                    raise SecurityPolicyError(
                        "ENDPOINT_REJECTED",
                        "Desktop cloud providers use their reviewed built-in endpoint.",
                    )
            observed = self._endpoint_validator.validate(provider_id, endpoint)
            if not hmac.compare_digest(
                endpoint_identity_sha256,
                observed.destination_digest,
            ):
                raise SecurityPolicyError(
                    "ENDPOINT_DESTINATION_CHANGED",
                    "The endpoint destination changed after it was tested.",
                    "Test the endpoint again before saving the profile.",
                )
            settings: dict[str, object] = {}
            if provider_id in {"ollama", "openrouter"}:
                settings["base_url"] = endpoint
            settings["endpoint_identity_sha256"] = observed.destination_digest
            self._profiles.create_profile(name, provider_id, model, settings)
            return self._snapshot_unlocked()

    def preview_consent(
        self,
        *,
        provider_profile_name: str,
        modules: Iterable[str],
        purposes: Iterable[str],
        data_classes: Iterable[DataClass],
        models: Iterable[str],
        max_cost_usd: float | None,
        retain_payloads: bool,
    ) -> ConsentPreview:
        profiles = {item.name: item for item in self.snapshot().profiles}
        profile = profiles.get(provider_profile_name)
        if profile is None:
            raise AncestryError(
                "PROVIDER_PROFILE_NOT_FOUND",
                "The selected provider profile does not exist.",
            )
        max_cost_usd = _validated_cost(max_cost_usd)
        normalized_modules = _normalized_values(modules, field_name="modules")
        normalized_purposes = _normalized_values(purposes, field_name="purposes")
        normalized_models = _normalized_values(models, field_name="models")
        normalized_data_classes = tuple(sorted(set(data_classes), key=lambda item: item.value))
        if not normalized_data_classes:
            raise AncestryError(
                "CONSENT_INVALID",
                "Consent data classes must contain at least one value.",
            )
        warnings: list[str] = []
        if set(normalized_data_classes).intersection(_LIVING_DATA_CLASSES):
            warnings.append("LIVING_PERSON_DATA_INCLUDED")
        if profile.endpoint_kind == "remote":
            warnings.append("REMOTE_PROVIDER_SELECTED")
        if profile.endpoint_kind == "remote" and retain_payloads:
            warnings.append("REMOTE_RETENTION_ENABLED")
        return ConsentPreview(
            schema_version=1,
            provider_profile_name=provider_profile_name,
            provider_id=profile.provider_id,
            modules=normalized_modules,
            purposes=normalized_purposes,
            data_classes=tuple(item.value for item in normalized_data_classes),
            models=normalized_models,
            max_cost_usd=max_cost_usd,
            retain_payloads=retain_payloads,
            warning_codes=tuple(warnings),
        )

    def create_consent(
        self,
        *,
        expected_revision: str,
        name: str,
        preview: ConsentPreview,
    ) -> ProviderConfigurationSnapshot:
        with self._mutation_lock:
            name = _validated_name(name, field_name="consent")
            current = self._snapshot_unlocked()
            self._check_revision(current, expected_revision)
            authoritative_preview = self._preview_consent_unlocked(preview)
            if preview != authoritative_preview:
                raise AncestryError(
                    "CONSENT_PREVIEW_STALE",
                    "The consent preview changed before it could be saved.",
                    "Preview the consent again before saving it.",
                )
            self._profiles.verify_endpoint_identity(preview.provider_profile_name)
            self._profiles.create_consent(
                name,
                preview.provider_profile_name,
                modules=list(preview.modules),
                purposes=list(preview.purposes),
                data_classes=[DataClass(value) for value in preview.data_classes],
                models=list(preview.models),
                max_cost_usd=preview.max_cost_usd,
                retain_payloads=preview.retain_payloads,
            )
            return self._snapshot_unlocked()

    def _preview_consent_unlocked(self, preview: ConsentPreview) -> ConsentPreview:
        profiles = {item.name: item for item in self._snapshot_unlocked().profiles}
        profile = profiles.get(preview.provider_profile_name)
        if profile is None:
            raise AncestryError(
                "PROVIDER_PROFILE_NOT_FOUND",
                "The selected provider profile does not exist.",
            )
        data_classes = tuple(DataClass(value) for value in preview.data_classes)
        warnings: list[str] = []
        if set(data_classes).intersection(_LIVING_DATA_CLASSES):
            warnings.append("LIVING_PERSON_DATA_INCLUDED")
        if profile.endpoint_kind == "remote":
            warnings.append("REMOTE_PROVIDER_SELECTED")
        if profile.endpoint_kind == "remote" and preview.retain_payloads:
            warnings.append("REMOTE_RETENTION_ENABLED")
        return ConsentPreview(
            schema_version=1,
            provider_profile_name=profile.name,
            provider_id=profile.provider_id,
            modules=_normalized_values(preview.modules, field_name="modules"),
            purposes=_normalized_values(preview.purposes, field_name="purposes"),
            data_classes=tuple(sorted(item.value for item in data_classes)),
            models=_normalized_values(preview.models, field_name="models"),
            max_cost_usd=_validated_cost(preview.max_cost_usd),
            retain_payloads=preview.retain_payloads,
            warning_codes=tuple(warnings),
        )

    def revoke_consent(
        self,
        *,
        expected_revision: str,
        name: str,
    ) -> ProviderConfigurationSnapshot:
        with self._mutation_lock:
            name = _validated_name(name, field_name="consent")
            current = self._snapshot_unlocked()
            self._check_revision(current, expected_revision)
            self._profiles.revoke_consent(name)
            return self._snapshot_unlocked()
