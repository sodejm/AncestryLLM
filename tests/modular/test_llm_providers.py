from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import Iterator
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from ancestryllm.core.errors import ProviderError, SecurityPolicyError, normalize_provider_error
from ancestryllm.llm.contracts import (
    DataClass,
    GenerationRequest,
    GenerationResult,
    Message,
    ProviderCapabilities,
)
from ancestryllm.llm.policy import ConsentGrant
from ancestryllm.llm.providers.anthropic import AnthropicProvider
from ancestryllm.llm.providers.gemini import GeminiProvider
from ancestryllm.llm.providers.none import NoneProvider
from ancestryllm.llm.providers.ollama import OllamaProvider
from ancestryllm.llm.providers.openai import OpenAIProvider
from ancestryllm.llm.service import LLMService


def request(provider_id: str, *, timeout_seconds: float = 12.5) -> GenerationRequest:
    return GenerationRequest(
        provider_id=provider_id,
        model="test-model",
        module_id="test-module",
        purpose="test-purpose",
        messages=(
            Message(role="system", content="Return a bounded answer."),
            Message(role="user", content="Fictional genealogy data."),
        ),
        data_classes=frozenset({DataClass.DECEASED_PERSON}),
        max_output_tokens=23,
        temperature=0.25,
        timeout_seconds=timeout_seconds,
    )


def assert_all_phase_timeout(timeout: httpx.Timeout, expected: float) -> None:
    assert timeout.connect == expected
    assert timeout.read == expected
    assert timeout.write == expected
    assert timeout.pool == expected


class ContextClient:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> ContextClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True


class RaisingStream:
    def __init__(self, chunks: list[Any], error: BaseException) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = False

    def __enter__(self) -> RaisingStream:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def __iter__(self) -> Iterator[Any]:
        yield from self.chunks
        raise self.error

    @property
    def text_stream(self) -> RaisingStream:
        return self


def test_openai_client_configures_all_timeout_phases_and_disables_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    module = ModuleType("openai")

    def client(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    module.OpenAI = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)

    OpenAIProvider("key")._client(12.5)

    assert captured["max_retries"] == 0
    assert_all_phase_timeout(captured["timeout"], 12.5)


def test_anthropic_client_configures_all_timeout_phases_and_disables_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    module = ModuleType("anthropic")

    def client(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    module.Anthropic = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)

    AnthropicProvider("key")._client(12.5)

    assert captured["max_retries"] == 0
    assert_all_phase_timeout(captured["timeout"], 12.5)


def test_gemini_client_uses_millisecond_timeout_and_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    google = ModuleType("google")
    genai = ModuleType("google.genai")
    types = ModuleType("google.genai.types")

    def http_options(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(**kwargs)

    def retry_options(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(**kwargs)

    def client(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    types.HttpOptions = http_options  # type: ignore[attr-defined]
    types.HttpRetryOptions = retry_options  # type: ignore[attr-defined]
    genai.Client = client  # type: ignore[attr-defined]
    genai.types = types  # type: ignore[attr-defined]
    google.genai = genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types)

    GeminiProvider("key")._client_and_types(12.5001)

    options = captured["http_options"]
    assert options.timeout == 12_501
    assert options.retry_options.attempts == 1
    assert_all_phase_timeout(options.client_args["timeout"], 12.5001)


def test_ollama_client_configures_all_timeout_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    module = ModuleType("ollama")

    def client(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    module.Client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ollama", module)

    OllamaProvider()._client(12.5)

    assert_all_phase_timeout(captured["timeout"], 12.5)


def test_openai_generate_passes_request_timeout_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ContextClient()
    captured: dict[str, Any] = {}

    def create(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            id="response-id",
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
        )

    client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))  # type: ignore[attr-defined]
    provider = OpenAIProvider("key")
    monkeypatch.setattr(provider, "_client", lambda timeout: client)

    assert provider.generate(request("openai")).text == "answer"

    assert client.closed
    assert_all_phase_timeout(captured["timeout"], 12.5)
    assert captured["max_completion_tokens"] == 23


def test_anthropic_generate_passes_request_timeout_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ContextClient()
    captured: dict[str, Any] = {}

    def create(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            id="response-id",
            content=[SimpleNamespace(type="text", text="answer")],
            usage=SimpleNamespace(input_tokens=3, output_tokens=1),
        )

    client.messages = SimpleNamespace(create=create)  # type: ignore[attr-defined]
    provider = AnthropicProvider("key")
    monkeypatch.setattr(provider, "_client", lambda timeout: client)

    assert provider.generate(request("anthropic")).text == "answer"

    assert client.closed
    assert_all_phase_timeout(captured["timeout"], 12.5)
    assert captured["max_tokens"] == 23


def test_gemini_generate_passes_request_timeout_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ContextClient()
    captured: dict[str, Any] = {}

    def generate_content(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(text="answer", usage_metadata=None)

    client.models = SimpleNamespace(generate_content=generate_content)  # type: ignore[attr-defined]
    provider = GeminiProvider("key")
    monkeypatch.setattr(provider, "_client_and_types", lambda timeout: (client, GeminiTypes))

    assert provider.generate(request("gemini")).text == "answer"

    assert client.closed
    assert captured["config"].http_options.timeout == 12_500
    assert captured["config"].http_options.retry_options.attempts == 1
    assert captured["config"].max_output_tokens == 23


def test_ollama_generate_uses_timed_client_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ContextClient()
    captured: dict[str, Any] = {}

    def chat(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "message": {"content": "answer"},
            "prompt_eval_count": 3,
            "eval_count": 1,
        }

    client.chat = chat  # type: ignore[attr-defined]
    provider = OllamaProvider()
    received_timeout: list[float] = []

    def timed_client(timeout: float) -> ContextClient:
        received_timeout.append(timeout)
        return client

    monkeypatch.setattr(provider, "_client", timed_client)

    assert provider.generate(request("ollama")).text == "answer"

    assert received_timeout == [12.5]
    assert client.closed
    assert captured["options"]["num_predict"] == 23


@pytest.mark.parametrize("timeout_seconds", [1.0, 600.0])
def test_timeout_validation_accepts_contract_boundaries(timeout_seconds: float) -> None:
    assert request("none", timeout_seconds=timeout_seconds).timeout_seconds == timeout_seconds


@pytest.mark.parametrize("timeout_seconds", [0.999, 600.001])
def test_timeout_validation_rejects_values_outside_contract(timeout_seconds: float) -> None:
    with pytest.raises(ValidationError):
        request("none", timeout_seconds=timeout_seconds)


@pytest.mark.parametrize("max_safe_retries", [-1, 3])
def test_safe_retry_count_is_bounded(max_safe_retries: int) -> None:
    payload = request("none").model_dump()
    payload["max_safe_retries"] = max_safe_retries
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(payload)


def _openai_stream(
    monkeypatch: pytest.MonkeyPatch, *, partial: bool
) -> tuple[OpenAIProvider, RaisingStream, ContextClient, dict[str, Any]]:
    chunks = []
    if partial:
        chunks.append(
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="partial"))])
        )
    stream = RaisingStream(chunks, TimeoutError("provider detail"))
    client = ContextClient()
    captured: dict[str, Any] = {}

    def create(**kwargs: Any) -> RaisingStream:
        captured.update(kwargs)
        return stream

    client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))  # type: ignore[attr-defined]
    provider = OpenAIProvider("key")
    monkeypatch.setattr(provider, "_client", lambda timeout: client)
    return provider, stream, client, captured


def _anthropic_stream(
    monkeypatch: pytest.MonkeyPatch, *, partial: bool
) -> tuple[AnthropicProvider, RaisingStream, ContextClient, dict[str, Any]]:
    stream = RaisingStream(["partial"] if partial else [], TimeoutError("provider detail"))
    client = ContextClient()
    captured: dict[str, Any] = {}

    def create_stream(**kwargs: Any) -> RaisingStream:
        captured.update(kwargs)
        return stream

    client.messages = SimpleNamespace(stream=create_stream)  # type: ignore[attr-defined]
    provider = AnthropicProvider("key")
    monkeypatch.setattr(provider, "_client", lambda timeout: client)
    return provider, stream, client, captured


class GeminiTypes:
    @staticmethod
    def HttpOptions(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(**kwargs)

    @staticmethod
    def HttpRetryOptions(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(**kwargs)

    @staticmethod
    def GenerateContentConfig(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(**kwargs)


def _gemini_stream(
    monkeypatch: pytest.MonkeyPatch, *, partial: bool
) -> tuple[GeminiProvider, RaisingStream, ContextClient, dict[str, Any]]:
    chunks = [SimpleNamespace(text="partial")] if partial else []
    stream = RaisingStream(chunks, TimeoutError("provider detail"))
    client = ContextClient()
    captured: dict[str, Any] = {}

    def generate_content_stream(**kwargs: Any) -> RaisingStream:
        captured.update(kwargs)
        return stream

    client.models = SimpleNamespace(generate_content_stream=generate_content_stream)  # type: ignore[attr-defined]
    provider = GeminiProvider("key")
    monkeypatch.setattr(provider, "_client_and_types", lambda timeout: (client, GeminiTypes))
    return provider, stream, client, captured


def _ollama_stream(
    monkeypatch: pytest.MonkeyPatch, *, partial: bool
) -> tuple[OllamaProvider, RaisingStream, ContextClient, dict[str, Any]]:
    chunks = [{"message": {"content": "partial"}}] if partial else []
    stream = RaisingStream(chunks, TimeoutError("provider detail"))
    client = ContextClient()
    captured: dict[str, Any] = {}

    def chat(**kwargs: Any) -> RaisingStream:
        captured.update(kwargs)
        return stream

    client.chat = chat  # type: ignore[attr-defined]
    provider = OllamaProvider()
    monkeypatch.setattr(provider, "_client", lambda timeout: client)
    return provider, stream, client, captured


@pytest.mark.parametrize(
    ("provider_id", "factory"),
    [
        ("openai", _openai_stream),
        ("anthropic", _anthropic_stream),
        ("gemini", _gemini_stream),
        ("ollama", _ollama_stream),
    ],
)
@pytest.mark.parametrize(
    ("partial", "expected_code"),
    [(False, "PROVIDER_TIMEOUT"), (True, "PROVIDER_STREAM_TIMEOUT")],
)
def test_provider_streams_normalize_timeouts_and_close_resources(
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    factory: Any,
    partial: bool,
    expected_code: str,
) -> None:
    provider, stream, client, captured = factory(monkeypatch, partial=partial)

    with pytest.raises(ProviderError) as raised:
        list(provider.stream(request(provider_id)))

    assert raised.value.code == expected_code
    assert stream.closed is (provider_id in {"openai", "anthropic"})
    assert client.closed
    if provider_id in {"openai", "anthropic"}:
        assert_all_phase_timeout(captured["timeout"], 12.5)
    if provider_id == "gemini":
        assert captured["config"].http_options.timeout == 12_500
        assert captured["config"].http_options.retry_options.attempts == 1
    if provider_id == "openai":
        assert captured["max_completion_tokens"] == 23
    if provider_id == "anthropic":
        assert captured["max_tokens"] == 23
    if provider_id == "ollama":
        assert captured["options"]["num_predict"] == 23


def test_none_provider_remains_offline_for_generate_and_stream() -> None:
    provider = NoneProvider()
    disabled = request("none", timeout_seconds=1.0)

    with pytest.raises(ProviderError) as generated:
        provider.generate(disabled)
    with pytest.raises(ProviderError) as streamed:
        list(provider.stream(disabled))

    assert generated.value.code == "PROVIDER_DISABLED"
    assert streamed.value.code == "PROVIDER_DISABLED"


class RateLimitError(Exception):
    status_code = 429
    response = SimpleNamespace(status_code=429, headers={"retry-after": "120"})


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (TimeoutError(), "PROVIDER_TIMEOUT"),
        (RateLimitError(), "PROVIDER_RATE_LIMITED"),
        (ConnectionError(), "PROVIDER_TRANSIENT"),
    ],
)
def test_provider_errors_have_stable_codes(exc: Exception, expected_code: str) -> None:
    error = normalize_provider_error(exc, "test")

    assert error.code == expected_code
    assert "provider detail" not in error.message
    if expected_code == "PROVIDER_RATE_LIMITED":
        assert error.details["retry_after_seconds"] == 60.0


class AuditSession:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def __enter__(self) -> AuditSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def add(self, row: Any) -> None:
        self.rows.append(row)

    def commit(self) -> None:
        return None


class AuditDatabase:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    def session(self) -> AuditSession:
        return AuditSession(self.rows)


class StaticRegistry:
    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def create(self, provider_id: str) -> Any:
        assert provider_id == self.provider.capabilities.provider_id
        return self.provider


class LifecycleProvider:
    def __init__(self, *, remote: bool = False, fail_after_chunk: bool = False) -> None:
        self.capabilities = ProviderCapabilities(
            provider_id="test",
            remote=remote,
            structured_output=False,
            streaming=True,
        )
        self.fail_after_chunk = fail_after_chunk
        self.stream_called = False
        self.stream_calls = 0
        self.closed = False

    def generate(self, request: GenerationRequest) -> GenerationResult:
        return GenerationResult(provider_id="test", model=request.model, text="complete")

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        self.stream_called = True
        self.stream_calls += 1
        try:
            yield "partial"
            if self.fail_after_chunk:
                raise TimeoutError("sensitive partial output")
            yield " complete"
        finally:
            self.closed = True


class CancelledProvider(LifecycleProvider):
    def stream(self, request: GenerationRequest) -> Iterator[str]:
        self.stream_called = True
        self.stream_calls += 1
        self.closed = True
        raise asyncio.CancelledError
        yield ""  # pragma: no cover


class RetryingProvider(LifecycleProvider):
    def __init__(self, failures: list[ProviderError]) -> None:
        super().__init__()
        self.failures = failures
        self.generate_calls = 0

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.generate_calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return super().generate(request)


def service(provider: LifecycleProvider) -> tuple[LLMService, AuditDatabase]:
    database = AuditDatabase()
    return LLMService(StaticRegistry(provider), database), database  # type: ignore[arg-type]


def retention_consent() -> ConsentGrant:
    return ConsentGrant(
        consent_id="retain",
        provider_id="test",
        allowed_modules=frozenset({"test-module"}),
        allowed_purposes=frozenset({"test-purpose"}),
        allowed_data_classes=frozenset({DataClass.DECEASED_PERSON}),
        model_allowlist=("test-*",),
        retain_payloads=True,
    )


def test_service_stream_authorizes_before_calling_remote_provider() -> None:
    provider = LifecycleProvider(remote=True)
    llm, database = service(provider)

    with pytest.raises(SecurityPolicyError, match="consent"):
        llm.stream(request("test"))

    assert not provider.stream_called
    assert database.rows == []


def test_service_stream_audits_success_without_retaining_payload() -> None:
    provider = LifecycleProvider()
    llm, database = service(provider)

    assert list(llm.stream(request("test"))) == ["partial", " complete"]

    assert provider.closed
    assert len(database.rows) == 1
    row = database.rows[0]
    assert row.status == "succeeded"
    assert row.input_payload is None
    assert row.output_payload is None
    assert row.response_hash == hashlib.sha256(b"partial complete").hexdigest()


def test_service_stream_does_not_retain_partial_payload_by_default() -> None:
    provider = LifecycleProvider(fail_after_chunk=True)
    llm, database = service(provider)

    with pytest.raises(ProviderError) as raised:
        list(llm.stream(request("test")))

    assert raised.value.code == "PROVIDER_STREAM_TIMEOUT"
    assert provider.closed
    row = database.rows[0]
    assert row.status == "aborted"
    assert row.error_code == "PROVIDER_STREAM_TIMEOUT"
    assert row.response_hash is None
    assert row.input_payload is None
    assert row.output_payload is None


def test_service_never_retries_a_partially_consumed_stream() -> None:
    provider = LifecycleProvider(fail_after_chunk=True)
    llm, _database = service(provider)
    retriable = request("test").model_copy(update={"max_safe_retries": 2})

    with pytest.raises(ProviderError):
        list(llm.stream(retriable))

    assert provider.stream_calls == 1


def test_service_stream_retains_partial_payload_only_with_explicit_consent() -> None:
    provider = LifecycleProvider(fail_after_chunk=True)
    llm, database = service(provider)

    with pytest.raises(ProviderError):
        list(llm.stream(request("test"), retention_consent()))

    row = database.rows[0]
    assert row.status == "aborted"
    assert row.input_payload is not None
    assert row.output_payload == "partial"


def test_service_stream_close_records_cancellation_and_releases_provider() -> None:
    provider = LifecycleProvider()
    llm, database = service(provider)
    stream = llm.stream(request("test"))

    assert next(stream) == "partial"
    stream.close()

    assert provider.closed
    assert len(database.rows) == 1
    row = database.rows[0]
    assert row.status == "aborted"
    assert row.error_code == "PROVIDER_CANCELLED"
    assert row.output_payload is None


def test_service_stream_normalizes_provider_cancellation_before_output() -> None:
    provider = CancelledProvider()
    llm, database = service(provider)

    with pytest.raises(ProviderError) as raised:
        list(llm.stream(request("test")))

    assert raised.value.code == "PROVIDER_CANCELLED"
    assert provider.closed
    row = database.rows[0]
    assert row.status == "aborted"
    assert row.error_code == "PROVIDER_CANCELLED"
    assert row.output_payload is None


def test_service_generate_does_not_retry_without_explicit_opt_in() -> None:
    provider = RetryingProvider([ProviderError("PROVIDER_TRANSIENT", "temporary", details={})])
    llm, database = service(provider)

    with pytest.raises(ProviderError) as raised:
        llm.generate(request("test"))

    assert raised.value.code == "PROVIDER_TRANSIENT"
    assert provider.generate_calls == 1
    assert database.rows[0].status == "failed"


def test_service_generate_uses_bounded_backoff_for_opted_in_safe_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RetryingProvider(
        [
            ProviderError(
                "PROVIDER_RATE_LIMITED",
                "limited",
                details={"retry_after_seconds": 0.25},
            ),
            ProviderError("PROVIDER_TRANSIENT", "temporary", details={}),
        ]
    )
    llm, database = service(provider)
    delays: list[float] = []
    monkeypatch.setattr("ancestryllm.llm.service.time.sleep", delays.append)
    retriable = request("test").model_copy(update={"max_safe_retries": 2})

    result = llm.generate(retriable)

    assert result.text == "complete"
    assert provider.generate_calls == 3
    assert delays == [0.25, 1.0]
    assert len(database.rows) == 1
    assert database.rows[0].status == "succeeded"
