"""Dependency container shared by console and future API adapters."""

from __future__ import annotations

from dataclasses import dataclass

from ancestryllm.core.config import AppConfig
from ancestryllm.core.secrets import KeyringSecretStore, SecretStore
from ancestryllm.llm.profiles import ProviderProfileService
from ancestryllm.llm.registry import ProviderRegistry
from ancestryllm.llm.service import LLMService
from ancestryllm.prompts.service import PromptService
from ancestryllm.research.service import ResearchService
from ancestryllm.storage.database import Database


@dataclass(slots=True)
class AppContext:
    config: AppConfig
    secrets: SecretStore
    database: Database
    providers: ProviderRegistry
    provider_profiles: ProviderProfileService
    llm: LLMService
    prompts: PromptService
    research: ResearchService

    def close(self) -> None:
        """Release shared provider clients before closing encrypted storage."""

        first_failure: BaseException | None = None
        for action in (self.llm.close, self.database.close):
            try:
                action()
            except BaseException as exc:  # noqa: BLE001 - later resources must still close
                if first_failure is None:
                    first_failure = exc
        if first_failure is not None:
            raise first_failure

    @classmethod
    def build(
        cls, config: AppConfig | None = None, secrets_store: SecretStore | None = None
    ) -> AppContext:
        selected_config = config or AppConfig.load()
        selected_secrets = secrets_store or KeyringSecretStore()
        database = Database(selected_config.database_path, selected_secrets)
        providers = ProviderRegistry(selected_secrets)
        provider_profiles = ProviderProfileService(database)
        return cls(
            config=selected_config,
            secrets=selected_secrets,
            database=database,
            providers=providers,
            provider_profiles=provider_profiles,
            llm=LLMService(providers, database, profiles=provider_profiles),
            prompts=PromptService(database),
            research=ResearchService(database),
        )
