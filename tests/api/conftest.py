"""Fictional fixtures for the internal control-plane API."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from ancestryllm.api import (
    API_BUILD_HEADER,
    API_CONTRACT,
    API_VERSION_HEADER,
    ApiSettings,
    create_app,
)
from ancestryllm.application.chat import (
    ChatCapability,
    ChatDataClass,
    ChatEvent,
    ChatEventPayload,
    ChatEventType,
    ChatMessage,
    ChatPurpose,
    ChatRole,
    ChatRunSummary,
    ChatSession,
    ChatStreamRun,
)
from ancestryllm.application.executor import CommandExecutor, CommandInvocation, CommandOutcome
from ancestryllm.application.gedcom_jobs import GedcomJobFacade
from ancestryllm.application.jobs import (
    JobLifecycleService,
    JobLifecycleState,
    MemoryJobEventRepository,
    PublicJobSnapshot,
)
from ancestryllm.application.results import StructuredResult
from ancestryllm.application.secret_management import SecretManagementService
from ancestryllm.application.settings import SettingsService
from ancestryllm.core.commands import BUILTIN_MODULES, DispatchKey, ModuleDescriptor
from ancestryllm.core.config import AppConfig
from ancestryllm.core.jobs import JobManager
from ancestryllm.core.secrets import MemorySecretStore
from ancestryllm.llm.chat import ChatService
from ancestryllm.llm.chat_streaming import ChatStreamingService
from ancestryllm.llm.endpoint_validation import (
    EndpointProbeRequest,
    EndpointProbeResponse,
    EndpointValidationService,
)
from ancestryllm.llm.profiles import ProviderProfileService
from ancestryllm.llm.provider_configuration import ProviderConfigurationService
from ancestryllm.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


class FixtureRegistry:
    def __init__(self, descriptors: Sequence[ModuleDescriptor]) -> None:
        self._descriptors = tuple(descriptors)

    def descriptors(self) -> list[ModuleDescriptor]:
        return list(self._descriptors)


def _complete(invocation: CommandInvocation) -> CommandOutcome:
    return CommandOutcome(StructuredResult({"dispatch_key": invocation.key.value}))


@pytest.fixture
def api_settings() -> ApiSettings:
    return ApiSettings(
        bearer_token="A" * 43,
        app_build="0.5.0-test",
        sidecar_build="0.5.0-test",
        provider_id="none",
    )


@pytest.fixture
def registered_keys() -> tuple[DispatchKey, ...]:
    return tuple(
        BUILTIN_MODULES[module_id].command.routes[0].key
        for module_id in ("gedcom", "providers", "ocr")
    )


@pytest.fixture
def secret_store() -> MemorySecretStore:
    return MemorySecretStore({})


@pytest.fixture
def job_service() -> Iterator[JobLifecycleService]:
    service = JobLifecycleService(JobManager(), MemoryJobEventRepository())
    service.startup()
    try:
        yield service
    finally:
        service.close()


def _queued_job(name: str) -> PublicJobSnapshot:
    return PublicJobSnapshot(
        schema_version=1,
        sequence=1,
        job_id="j000001",
        name=name,
        state=JobLifecycleState.QUEUED,
        submitted_at="2026-08-27T12:00:00+00:00",
        started_at=None,
        finished_at=None,
        resource_refs=("resource_" + ("a" * 64),),
        artifact=None,
        outcome_summary=None,
        next_action=None,
        error_code=None,
        error_message=None,
        error_remediation=None,
        progress=None,
        cancellation_requested_at=None,
        cancellation_deferred_by=None,
    )


@pytest.fixture
def gedcom_job_facade() -> Mock:
    service = Mock(spec=GedcomJobFacade)
    service.submit_inspect.return_value = _queued_job("gedcom.inspect")
    service.submit_merge.return_value = _queued_job("gedcom.merge")
    service.submit_subtree.return_value = _queued_job("gedcom.subtree")
    service.submit_quality.return_value = _queued_job("gedcom.quality")
    service.submit_sync.return_value = _queued_job("gedcom.sync")
    return service


@pytest.fixture
def chat_service() -> Mock:
    service = Mock(spec=ChatService)
    session = ChatSession(
        session_id="chat_" + ("a" * 32),
        provider_profile_name="fictional-local",
        provider_id="ollama",
        model="fictional-model",
        purpose=ChatPurpose.GENEALOGY_ANALYSIS,
        data_classes=(ChatDataClass.DECEASED_PERSON,),
        remote=False,
        consent_name=None,
        message_count=0,
    )
    service.capability.return_value = ChatCapability()
    service.start.return_value = session
    service.get.return_value = session
    service.run.return_value = ChatRunSummary(
        assistant_message=ChatMessage(
            role=ChatRole.ASSISTANT,
            content="Fictional response with no evidentiary status.",
        ),
        provider_id="ollama",
        model="fictional-model",
        remote=False,
        input_tokens=12,
        output_tokens=7,
        cost_usd=None,
        message_count=2,
    )
    return service


@pytest.fixture
def chat_streaming_service() -> Mock:
    service = Mock(spec=ChatStreamingService)
    session_id = "chat_" + ("a" * 32)
    run_id = "run_" + ("b" * 32)
    timestamp = "2026-08-13T12:00:00+00:00"
    events = (
        ChatEvent(
            run_id=run_id,
            sequence=1,
            type=ChatEventType.ACTIVE,
            timestamp=timestamp,
            payload=ChatEventPayload(
                provider_id="ollama",
                model="fictional-model",
                remote=False,
            ),
        ),
        ChatEvent(
            run_id=run_id,
            sequence=2,
            type=ChatEventType.FIRST_TOKEN,
            timestamp=timestamp,
            payload=ChatEventPayload(text="Fictional "),
        ),
        ChatEvent(
            run_id=run_id,
            sequence=3,
            type=ChatEventType.DELTA,
            timestamp=timestamp,
            payload=ChatEventPayload(text=" "),
        ),
        ChatEvent(
            run_id=run_id,
            sequence=4,
            type=ChatEventType.COMPLETED,
            timestamp=timestamp,
            payload=ChatEventPayload(message_count=2),
        ),
    )

    async def subscription() -> object:
        for event in events:
            yield event

    service.start = AsyncMock(
        return_value=ChatStreamRun(
            session_id=session_id,
            run_id=run_id,
            state=ChatEventType.ACTIVE,
            latest_sequence=1,
            terminal=False,
        )
    )
    service.subscribe = AsyncMock(side_effect=lambda *_args, **_kwargs: subscription())
    service.cancel = AsyncMock(
        return_value=ChatStreamRun(
            session_id=session_id,
            run_id=run_id,
            state=ChatEventType.INTERRUPTED,
            latest_sequence=4,
            terminal=True,
        )
    )
    return service


@pytest.fixture
def api_client(
    api_settings: ApiSettings,
    registered_keys: tuple[DispatchKey, ...],
    secret_store: MemorySecretStore,
    job_service: JobLifecycleService,
    gedcom_job_facade: Mock,
    chat_service: Mock,
    chat_streaming_service: Mock,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[TestClient]:
    registry = FixtureRegistry(
        tuple(BUILTIN_MODULES[module_id] for module_id in ("gedcom", "providers", "secrets"))
    )
    executor = CommandExecutor((key, _complete) for key in registered_keys)
    config_root = tmp_path_factory.mktemp("api-config")
    database = Database(config_root / "workspace.db", secret_store)
    endpoint_validator = EndpointValidationService(
        resolver=lambda hostname, _port: (
            ("127.0.0.1",) if hostname in {"127.0.0.1", "localhost"} else ("8.8.8.8",)
        ),
        probe=lambda request: _successful_probe(request),
    )
    provider_profiles = ProviderProfileService(
        database,
        endpoint_validator=endpoint_validator,
    )
    app = create_app(
        settings=api_settings,
        registry=registry,
        executor=executor,
        settings_service=SettingsService(
            AppConfig(
                config_path=config_root / "config.toml",
                data_dir=config_root / "data",
            )
        ),
        secret_service=SecretManagementService(secret_store),
        provider_configuration_service=ProviderConfigurationService(
            provider_profiles,
            endpoint_validator,
        ),
        endpoint_validation_service=endpoint_validator,
        job_service=lambda: job_service,
        gedcom_job_service=lambda: gedcom_job_facade,
        chat_service=chat_service,
        chat_streaming_service=chat_streaming_service,
    )
    with TestClient(app, base_url="http://127.0.0.1:8421", raise_server_exceptions=False) as client:
        yield client
    database.close()


def _successful_probe(request: EndpointProbeRequest) -> EndpointProbeResponse:
    return EndpointProbeResponse(status_code=200, peer_address=request.pinned_address)


@pytest.fixture
def api_headers(api_settings: ApiSettings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_settings.bearer_token}",
        API_VERSION_HEADER: API_CONTRACT,
        API_BUILD_HEADER: api_settings.app_build,
    }
