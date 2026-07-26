"""Encrypted provider configuration and revocable profile-level consent."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from ancestryllm.core.errors import AncestryError, SecurityPolicyError
from ancestryllm.llm.contracts import (
    DataClass,
    GenerationRequest,
    ProviderExecution,
)
from ancestryllm.llm.policy import ConsentGrant, validate_endpoint
from ancestryllm.llm.registry import PROVIDER_IDS
from ancestryllm.storage.database import Database
from ancestryllm.storage.models import ConsentProfileModel, ProviderProfileModel
from ancestryllm.storage.repositories import ProviderRepository

SECRET_REFERENCES = {
    "openai": "openai.api_key",
    "anthropic": "anthropic.api_key",
    "gemini": "gemini.api_key",
    "openrouter": "openrouter.api_key",
}

REQUEST_SETTING_NAMES = frozenset(
    {
        "max_output_tokens",
        "max_safe_retries",
        "temperature",
        "timeout_seconds",
    }
)
EXECUTION_SETTING_NAMES = frozenset(
    {
        "base_url",
        "cache_max_entries",
        "cache_ttl_seconds",
        "keep_alive",
        "max_concurrency",
        "max_pending",
        "num_batch",
        "num_ctx",
        "num_gpu",
        "num_thread",
        "seed",
        "zero_data_retention",
    }
)
COMMON_PROFILE_SETTINGS = frozenset(
    {
        "cache_max_entries",
        "cache_ttl_seconds",
        "max_concurrency",
        "max_output_tokens",
        "max_pending",
        "max_safe_retries",
        "temperature",
        "timeout_seconds",
    }
)
PROVIDER_PROFILE_SETTINGS = {
    "ollama": COMMON_PROFILE_SETTINGS
    | frozenset(
        {
            "base_url",
            "keep_alive",
            "num_batch",
            "num_ctx",
            "num_gpu",
            "num_thread",
            "seed",
        }
    ),
    "openai": COMMON_PROFILE_SETTINGS,
    "anthropic": COMMON_PROFILE_SETTINGS,
    "gemini": COMMON_PROFILE_SETTINGS,
    "openrouter": COMMON_PROFILE_SETTINGS | frozenset({"base_url", "zero_data_retention"}),
}


def _coerce_setting(value: object) -> object:
    """Parse CLI-provided JSON scalars while preserving ordinary strings."""

    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _validated_settings(
    provider_id: str,
    settings: dict[str, object],
    *,
    profile_name: str,
) -> tuple[ProviderExecution, dict[str, Any]]:
    allowed = PROVIDER_PROFILE_SETTINGS.get(provider_id, frozenset())
    unknown = sorted(set(settings) - allowed)
    if unknown:
        raise AncestryError(
            "PROVIDER_PROFILE_SETTING_UNKNOWN",
            "The provider profile contains unsupported settings.",
            "Remove unsupported settings and recreate the profile.",
            details={"settings": unknown},
        )
    normalized = {name: _coerce_setting(value) for name, value in settings.items()}
    execution_payload = {
        name: value for name, value in normalized.items() if name in EXECUTION_SETTING_NAMES
    }
    execution_payload["profile_name"] = profile_name
    try:
        execution = ProviderExecution.model_validate(execution_payload)
        request_settings = {
            name: value for name, value in normalized.items() if name in REQUEST_SETTING_NAMES
        }
        # Validate request-level constraints without inventing a second range contract.
        probe = GenerationRequest(
            provider_id=provider_id,
            model="profile-validation",
            module_id="profile-validation",
            purpose="profile-validation",
            messages=(),
            **request_settings,
        )
    except (TypeError, ValidationError, ValueError) as exc:
        raise AncestryError(
            "PROVIDER_PROFILE_INVALID",
            "The provider profile settings are invalid.",
            "Correct the named settings and recreate the profile.",
            details={"settings": sorted(settings), "error_type": type(exc).__name__},
        ) from exc
    if execution.base_url is not None:
        validate_endpoint(provider_id, execution.base_url)
    return execution, {name: getattr(probe, name) for name in request_settings}


class ProviderProfileService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_profile(
        self,
        name: str,
        provider_id: str,
        model: str,
        settings: Mapping[str, object] | None = None,
    ) -> ProviderProfileModel:
        if provider_id not in PROVIDER_IDS or provider_id == "none":
            raise AncestryError(
                "PROVIDER_UNKNOWN", f"Unsupported configured provider: {provider_id}"
            )
        if not name.strip() or not model.strip():
            raise AncestryError("PROVIDER_PROFILE_INVALID", "Profile name and model are required.")
        if name in PROVIDER_IDS:
            raise AncestryError(
                "PROVIDER_PROFILE_RESERVED",
                "A provider profile name cannot replace a built-in provider identifier.",
            )
        normalized_settings = dict(settings or {})
        _validated_settings(provider_id, normalized_settings, profile_name=name)
        with self.database.session() as session:
            repository = ProviderRepository(session)
            if repository.get_profile(name):
                raise AncestryError(
                    "PROVIDER_PROFILE_EXISTS", f"Provider profile already exists: {name}"
                )
            profile = ProviderProfileModel(
                name=name,
                provider_id=provider_id,
                model=model,
                secret_reference=SECRET_REFERENCES.get(provider_id),
                settings_json=json.dumps(
                    {key: _coerce_setting(value) for key, value in normalized_settings.items()},
                    sort_keys=True,
                ),
            )
            session.add(profile)
            session.commit()
            return profile

    def resolve_request(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None = None,
    ) -> GenerationRequest:
        """Resolve a built-in provider ID or named profile into one immutable request plan."""

        selection = request.provider_id
        consent_profile = consent.provider_profile_name if consent is not None else None
        if consent is not None and consent_profile is None:
            raise SecurityPolicyError(
                "CONSENT_PROFILE_MISMATCH",
                "Consent is not linked to a provider profile or endpoint.",
            )
        if selection == "none":
            return request.model_copy(update={"execution": ProviderExecution()})
        if selection in PROVIDER_IDS:
            if not request.model.strip():
                raise AncestryError(
                    "PROVIDER_MODEL_REQUIRED",
                    "A model is required when a built-in provider is selected directly.",
                    "Supply --model or select a named provider profile.",
                )
            if consent is not None and consent_profile is not None:
                if consent.provider_id != selection:
                    raise SecurityPolicyError(
                        "CONSENT_PROVIDER_MISMATCH",
                        "Consent is for a different provider.",
                    )
                selection = consent_profile
                request = request.model_copy(update={"provider_id": selection})
            else:
                if request.execution.base_url is not None:
                    validate_endpoint(selection, request.execution.base_url)
                return request

        with self.database.session() as session:
            profile = ProviderRepository(session).get_profile(selection)
            if profile is None:
                raise AncestryError(
                    "PROVIDER_PROFILE_NOT_FOUND",
                    f"Provider profile not found: {selection}",
                    "Create the profile or select a built-in provider and model.",
                )
            if consent is not None:
                if consent_profile != profile.name:
                    raise SecurityPolicyError(
                        "CONSENT_PROFILE_MISMATCH",
                        "Consent is for a different provider profile or endpoint.",
                    )
                if consent.provider_id != profile.provider_id:
                    raise SecurityPolicyError(
                        "CONSENT_PROVIDER_MISMATCH",
                        "Consent is for a different provider.",
                    )
            if not profile.enabled:
                raise AncestryError(
                    "PROVIDER_PROFILE_DISABLED",
                    f"Provider profile is disabled: {selection}",
                )
            try:
                raw_settings = json.loads(profile.settings_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise AncestryError(
                    "PROVIDER_PROFILE_INVALID",
                    f"Provider profile settings are malformed: {selection}",
                ) from exc
            if not isinstance(raw_settings, dict) or not all(
                isinstance(name, str) for name in raw_settings
            ):
                raise AncestryError(
                    "PROVIDER_PROFILE_INVALID",
                    f"Provider profile settings are malformed: {selection}",
                )
            execution, request_settings = _validated_settings(
                profile.provider_id,
                raw_settings,
                profile_name=profile.name,
            )
            if request.model.strip() and request.model != profile.model:
                raise AncestryError(
                    "PROVIDER_PROFILE_MODEL_CONFLICT",
                    "The command model does not match the selected provider profile.",
                    "Omit --model or use the model configured by the profile.",
                )
            for bounded_name in (
                "max_output_tokens",
                "timeout_seconds",
            ):
                if bounded_name in request_settings:
                    request_settings[bounded_name] = min(
                        getattr(request, bounded_name),
                        request_settings[bounded_name],
                    )
            return request.model_copy(
                update={
                    "provider_id": profile.provider_id,
                    "model": profile.model,
                    "execution": execution,
                    **request_settings,
                }
            )

    def create_consent(
        self,
        name: str,
        provider_profile: str,
        *,
        modules: list[str],
        purposes: list[str],
        data_classes: list[DataClass],
        models: list[str],
        max_cost_usd: float | None = None,
        retain_payloads: bool = False,
    ) -> ConsentProfileModel:
        with self.database.session() as session:
            repository = ProviderRepository(session)
            profile = repository.get_profile(provider_profile)
            if profile is None:
                raise AncestryError(
                    "PROVIDER_PROFILE_NOT_FOUND", f"Profile not found: {provider_profile}"
                )
            if repository.get_consent(name):
                raise AncestryError("CONSENT_EXISTS", f"Consent profile already exists: {name}")
            consent = ConsentProfileModel(
                name=name,
                provider_profile_id=profile.id,
                allowed_modules_json=json.dumps(sorted(set(modules))),
                allowed_purposes_json=json.dumps(sorted(set(purposes))),
                allowed_data_classes_json=json.dumps(sorted({item.value for item in data_classes})),
                model_allowlist_json=json.dumps(sorted(set(models))),
                max_cost_usd=max_cost_usd,
                retain_payloads=retain_payloads,
            )
            session.add(consent)
            session.commit()
            return consent

    def consent_grant(self, name: str) -> ConsentGrant:
        with self.database.session() as session:
            consent = ProviderRepository(session).get_consent(name)
            if consent is None:
                raise AncestryError("CONSENT_NOT_FOUND", f"Consent profile not found: {name}")
            profile = session.get(ProviderProfileModel, consent.provider_profile_id)
            if profile is None:
                raise AncestryError(
                    "PROVIDER_PROFILE_NOT_FOUND", "Consent provider profile is missing."
                )
            return ConsentGrant(
                consent_id=consent.id,
                provider_id=profile.provider_id,
                allowed_modules=frozenset(json.loads(consent.allowed_modules_json)),
                allowed_purposes=frozenset(json.loads(consent.allowed_purposes_json)),
                allowed_data_classes=frozenset(
                    DataClass(value) for value in json.loads(consent.allowed_data_classes_json)
                ),
                model_allowlist=tuple(json.loads(consent.model_allowlist_json)),
                max_cost_usd=consent.max_cost_usd,
                retain_payloads=consent.retain_payloads,
                active=consent.revoked_at is None,
                provider_profile_name=profile.name,
            )

    def revoke_consent(self, name: str) -> None:
        with self.database.session() as session:
            consent = ProviderRepository(session).get_consent(name)
            if consent is None:
                raise AncestryError("CONSENT_NOT_FOUND", f"Consent profile not found: {name}")
            consent.revoked_at = dt.datetime.now(dt.timezone.utc).isoformat()
            session.commit()

    def list_profiles(self) -> list[ProviderProfileModel]:
        with self.database.session() as session:
            return ProviderRepository(session).list_profiles()

    def list_consents(self) -> list[ConsentProfileModel]:
        with self.database.session() as session:
            return ProviderRepository(session).list_consents()
