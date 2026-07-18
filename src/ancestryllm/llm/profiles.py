"""Encrypted provider configuration and revocable profile-level consent."""

from __future__ import annotations

import datetime as dt
import json

from ancestryllm.core.errors import AncestryError
from ancestryllm.llm.contracts import DataClass
from ancestryllm.llm.policy import ConsentGrant
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


class ProviderProfileService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_profile(
        self,
        name: str,
        provider_id: str,
        model: str,
        settings: dict[str, object] | None = None,
    ) -> ProviderProfileModel:
        if provider_id not in PROVIDER_IDS or provider_id == "none":
            raise AncestryError(
                "PROVIDER_UNKNOWN", f"Unsupported configured provider: {provider_id}"
            )
        if not name.strip() or not model.strip():
            raise AncestryError("PROVIDER_PROFILE_INVALID", "Profile name and model are required.")
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
                settings_json=json.dumps(settings or {}, sort_keys=True),
            )
            session.add(profile)
            session.commit()
            return profile

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
