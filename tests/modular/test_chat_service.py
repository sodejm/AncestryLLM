"""Characterize the bounded, transient chat application service."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from ancestryllm.application.chat import (
    CHAT_MAX_ACTIVE_SESSIONS,
    CHAT_MAX_CONTEXT_CHARACTERS,
    CHAT_MAX_MESSAGE_CHARACTERS,
    CHAT_MAX_MESSAGES,
    ChatDataClass,
    ChatMessage,
    ChatPurpose,
    ChatRole,
    ChatRunRequest,
    ChatSessionCreateRequest,
)
from ancestryllm.core.errors import AncestryError, ProviderError, SecurityPolicyError
from ancestryllm.llm.chat import ChatService
from ancestryllm.llm.contracts import (
    DataClass,
    GenerationRequest,
    GenerationResult,
    ProviderCapabilities,
)
from ancestryllm.llm.service import LLMService
from ancestryllm.storage.models import LlmRunModel

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ancestryllm.core.context import AppContext


class _FixtureProvider:
    def __init__(self, provider_id: str, *, remote: bool) -> None:
        self.capabilities = ProviderCapabilities(
            provider_id=provider_id,
            remote=remote,
            structured_output=False,
            streaming=False,
            retention_known=True,
            zero_data_retention=True,
        )
        self.calls: list[GenerationRequest] = []
        self.failure: ProviderError | None = None

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        return GenerationResult(
            provider_id=self.capabilities.provider_id,
            model=request.model,
            text="Fictional response with no evidentiary status.",
            input_tokens=12,
            output_tokens=7,
            cost_usd=0.001 if self.capabilities.remote else None,
        )

    def stream(self, _request: GenerationRequest) -> Iterator[str]:
        raise AssertionError("transient chat must not invoke provider streaming")


class _FixtureRegistry:
    def __init__(self, provider: _FixtureProvider) -> None:
        self.provider = provider
        self.creations: list[str] = []
        self.closed = False

    def create(self, provider_id: str, **_settings: object) -> _FixtureProvider:
        self.creations.append(provider_id)
        assert provider_id == self.provider.capabilities.provider_id
        return self.provider

    def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class _ChatEnvironment:
    app_context: AppContext
    provider: _FixtureProvider
    registry: _FixtureRegistry
    llm: LLMService
    chat: ChatService

    def close(self) -> None:
        self.chat.close()
        self.llm.close()
        self.app_context.close()


def _build_environment(
    app_context: AppContext,
    *,
    provider_id: str = "ollama",
    remote: bool = False,
    profile_name: str = "fictional-local",
) -> _ChatEnvironment:
    settings: dict[str, object] = {
        "max_output_tokens": 8_192,
        "max_safe_retries": 2,
        "temperature": 2.0,
        "timeout_seconds": 300,
    }
    if provider_id == "ollama":
        settings["base_url"] = "http://127.0.0.1:11434"
    app_context.provider_profiles.create_profile(
        profile_name,
        provider_id,
        "fictional-model",
        settings,
    )
    provider = _FixtureProvider(provider_id, remote=remote)
    registry = _FixtureRegistry(provider)
    llm = LLMService(  # type: ignore[arg-type]
        registry,
        app_context.database,
        profiles=app_context.provider_profiles,
    )
    return _ChatEnvironment(
        app_context=app_context,
        provider=provider,
        registry=registry,
        llm=llm,
        chat=ChatService(llm, app_context.provider_profiles),
    )


def _start_request(
    *,
    profile_name: str = "fictional-local",
    model: str = "fictional-model",
    consent_name: str | None = None,
    data_classes: tuple[ChatDataClass, ...] | None = None,
) -> ChatSessionCreateRequest:
    return ChatSessionCreateRequest(
        provider_profile_name=profile_name,
        model=model,
        purpose=ChatPurpose.GENEALOGY_ANALYSIS,
        data_classes=data_classes or (ChatDataClass.DECEASED_PERSON,),
        consent_name=consent_name,
    )


@pytest.fixture
def chat_environment(app_context: AppContext) -> Iterator[_ChatEnvironment]:
    environment = _build_environment(app_context)
    yield environment
    environment.close()


def test_local_chat_is_transient_bounded_and_privacy_minimal(
    chat_environment: _ChatEnvironment,
) -> None:
    session = chat_environment.chat.start(_start_request())

    first = chat_environment.chat.run(
        session.session_id,
        ChatRunRequest(message="Analyze this fictional deceased-person record."),
    )
    second = chat_environment.chat.run(
        session.session_id,
        ChatRunRequest(
            message="Summarize the fictional uncertainty.",
            max_output_tokens=4_096,
            temperature=1.0,
            timeout_seconds=120,
            max_safe_retries=1,
        ),
    )

    assert first.output_is_evidence is False
    assert first.retained_payload is False
    assert first.assistant_message == ChatMessage(
        role=ChatRole.ASSISTANT,
        content="Fictional response with no evidentiary status.",
    )
    assert second.message_count == 4
    assert chat_environment.chat.get(session.session_id).message_count == 4
    assert len(chat_environment.provider.calls) == 2
    assert [message.role for message in chat_environment.provider.calls[1].messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    planned = chat_environment.provider.calls[1]
    assert planned.messages[0].role == "system"
    assert "non-autonomous" in planned.messages[0].content
    assert all(
        execution_surface in planned.messages[0].content
        for execution_surface in ("tools", "files", "databases", "shells", "plugins")
    )
    assert not hasattr(planned, "tools")
    assert planned.max_output_tokens == 4_096
    assert planned.temperature == 1.0
    assert planned.timeout_seconds == 120
    assert planned.max_safe_retries == 1

    with chat_environment.app_context.database.session() as database_session:
        rows = list(database_session.scalars(select(LlmRunModel)))
    assert [row.status for row in rows] == ["succeeded", "succeeded"]
    assert all(row.request_hash for row in rows)
    assert all(row.response_hash for row in rows)
    assert all(row.input_payload is None for row in rows)
    assert all(row.output_payload is None for row in rows)

    chat_environment.chat.teardown(session.session_id)
    with pytest.raises(AncestryError) as missing:
        chat_environment.chat.get(session.session_id)
    assert missing.value.code == "CHAT_SESSION_NOT_FOUND"


def test_cloud_chat_requires_fresh_exact_consent_and_never_retains_payloads(
    app_context: AppContext,
) -> None:
    environment = _build_environment(
        app_context,
        provider_id="openai",
        remote=True,
        profile_name="fictional-cloud",
    )
    try:
        with pytest.raises(SecurityPolicyError) as missing:
            environment.chat.start(_start_request(profile_name="fictional-cloud"))
        assert missing.value.code == "CLOUD_CONSENT_REQUIRED"
        assert environment.provider.calls == []

        app_context.provider_profiles.create_consent(
            "fictional-chat-consent",
            "fictional-cloud",
            modules=["chat"],
            purposes=[ChatPurpose.GENEALOGY_ANALYSIS.value],
            data_classes=[DataClass.DECEASED_PERSON],
            models=["fictional-model"],
            retain_payloads=True,
        )
        session = environment.chat.start(
            _start_request(
                profile_name="fictional-cloud",
                consent_name="fictional-chat-consent",
            )
        )
        environment.chat.run(
            session.session_id,
            ChatRunRequest(message="Analyze a fictional cloud-safe record."),
        )

        with app_context.database.session() as database_session:
            row = database_session.scalars(select(LlmRunModel)).one()
        assert row.status == "succeeded"
        assert row.input_payload is None
        assert row.output_payload is None

        app_context.provider_profiles.revoke_consent("fictional-chat-consent")
        with pytest.raises(SecurityPolicyError) as revoked:
            environment.chat.run(
                session.session_id,
                ChatRunRequest(message="This must fail before another provider call."),
            )
        assert revoked.value.code == "CONSENT_INACTIVE"
        assert len(environment.provider.calls) == 1
        environment.chat.teardown(session.session_id)
    finally:
        environment.close()


def test_living_person_denial_precedes_provider_execution(app_context: AppContext) -> None:
    environment = _build_environment(
        app_context,
        provider_id="openai",
        remote=True,
        profile_name="fictional-cloud",
    )
    try:
        app_context.provider_profiles.create_consent(
            "deceased-only",
            "fictional-cloud",
            modules=["chat"],
            purposes=[ChatPurpose.GENEALOGY_ANALYSIS.value],
            data_classes=[DataClass.DECEASED_PERSON],
            models=["fictional-model"],
        )
        with pytest.raises(SecurityPolicyError) as denied:
            environment.chat.start(
                _start_request(
                    profile_name="fictional-cloud",
                    consent_name="deceased-only",
                    data_classes=(ChatDataClass.LIVING_PERSON,),
                )
            )
        assert denied.value.code == "CONSENT_DATA_DENIED"
        assert environment.provider.calls == []
    finally:
        environment.close()


def test_provider_failure_is_sanitized_and_does_not_commit_history(
    chat_environment: _ChatEnvironment,
) -> None:
    session = chat_environment.chat.start(_start_request())
    chat_environment.provider.failure = ProviderError(
        "PROVIDER_TRANSIENT",
        "The fictional provider is temporarily unavailable.",
    )

    with pytest.raises(ProviderError) as raised:
        chat_environment.chat.run(
            session.session_id,
            ChatRunRequest(message="Do not retain this failed message."),
        )

    assert raised.value.code == "PROVIDER_TRANSIENT"
    assert chat_environment.chat.get(session.session_id).message_count == 0
    with chat_environment.app_context.database.session() as database_session:
        row = database_session.scalars(select(LlmRunModel)).one()
    assert row.status == "failed"
    assert row.error_code == "PROVIDER_TRANSIENT"
    assert row.input_payload is None
    assert row.output_payload is None


@pytest.mark.parametrize("selection", ["none", "ollama", "openai"])
def test_chat_rejects_none_and_direct_provider_selection(
    chat_environment: _ChatEnvironment,
    selection: str,
) -> None:
    with pytest.raises(AncestryError) as raised:
        chat_environment.chat.start(_start_request(profile_name=selection))

    assert raised.value.code in {"CHAT_PROVIDER_NONE", "CHAT_PROFILE_REQUIRED"}
    assert chat_environment.registry.creations == []


def test_provider_none_is_network_free_under_socket_denial(
    chat_environment: _ChatEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted_network_calls: list[str] = []

    def deny_network(*_args: object, **_kwargs: object) -> None:
        attempted_network_calls.append("socket")
        raise AssertionError("provider=none attempted network access")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)

    with pytest.raises(AncestryError) as raised:
        chat_environment.chat.start(_start_request(profile_name="none"))

    assert raised.value.code == "CHAT_PROVIDER_NONE"
    assert attempted_network_calls == []
    assert chat_environment.registry.creations == []


@pytest.mark.parametrize(
    ("profile_name", "model", "expected_code"),
    [
        ("missing-profile", "fictional-model", "PROVIDER_PROFILE_NOT_FOUND"),
        ("fictional-local", "different-model", "PROVIDER_PROFILE_MODEL_CONFLICT"),
    ],
)
def test_missing_or_incompatible_profile_fails_before_provider_creation(
    chat_environment: _ChatEnvironment,
    profile_name: str,
    model: str,
    expected_code: str,
) -> None:
    with pytest.raises(AncestryError) as raised:
        chat_environment.chat.start(_start_request(profile_name=profile_name, model=model))

    assert raised.value.code == expected_code
    assert chat_environment.registry.creations == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("message", "x" * (CHAT_MAX_MESSAGE_CHARACTERS + 1)),
        ("max_output_tokens", 4_097),
        ("temperature", 1.01),
        ("timeout_seconds", 121),
        ("max_safe_retries", 2),
    ],
)
def test_run_request_rejects_scalar_bounds(field: str, value: object) -> None:
    values: dict[str, object] = {"message": "Fictional bounded request."}
    values[field] = value

    with pytest.raises(ValueError):
        ChatRunRequest(**values)  # type: ignore[arg-type]


def test_context_bound_fails_before_an_additional_provider_call(
    chat_environment: _ChatEnvironment,
) -> None:
    session = chat_environment.chat.start(_start_request())
    message = "x" * CHAT_MAX_MESSAGE_CHARACTERS
    successful_calls = 0
    while True:
        try:
            chat_environment.chat.run(session.session_id, ChatRunRequest(message=message))
        except AncestryError as exc:
            assert exc.code == "CHAT_CONTEXT_LIMIT"
            break
        successful_calls += 1
        assert successful_calls <= CHAT_MAX_CONTEXT_CHARACTERS // len(message)

    assert len(chat_environment.provider.calls) == successful_calls


def test_message_count_bound_fails_before_an_additional_provider_call(
    chat_environment: _ChatEnvironment,
) -> None:
    session = chat_environment.chat.start(_start_request())
    for _ in range(CHAT_MAX_MESSAGES // 2):
        chat_environment.chat.run(
            session.session_id,
            ChatRunRequest(message="Fictional bounded request."),
        )

    with pytest.raises(AncestryError) as limited:
        chat_environment.chat.run(
            session.session_id,
            ChatRunRequest(message="This request exceeds the message budget."),
        )

    assert limited.value.code == "CHAT_MESSAGE_LIMIT"
    assert len(chat_environment.provider.calls) == CHAT_MAX_MESSAGES // 2


def test_session_limit_fails_before_additional_provider_resolution(
    chat_environment: _ChatEnvironment,
) -> None:
    for _ in range(CHAT_MAX_ACTIVE_SESSIONS):
        chat_environment.chat.start(_start_request())

    with pytest.raises(AncestryError) as limited:
        chat_environment.chat.start(_start_request())

    assert limited.value.code == "CHAT_SESSION_LIMIT"
    assert len(chat_environment.registry.creations) == CHAT_MAX_ACTIVE_SESSIONS


def test_capability_forbids_tools_retention_and_evidentiary_use(
    chat_environment: _ChatEnvironment,
) -> None:
    capability = chat_environment.chat.capability()

    assert capability.transient is True
    assert capability.tools_enabled is False
    assert capability.payload_retention is False
    assert capability.output_is_evidence is False
    assert capability.streaming is False
