"""Verify LLM provider lifecycle, streaming, retry, privacy, and cancellation behavior."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from ancestryllm.core.cancellation import CancellationError
from ancestryllm.core.errors import ProviderError, SecurityPolicyError, normalize_provider_error
from ancestryllm.core.jobs import JobManager, JobState
from ancestryllm.llm.contracts import (
    DataClass,
    GenerationRequest,
    GenerationResult,
    Message,
    ProviderCapabilities,
    ProviderExecution,
)
from ancestryllm.llm.policy import ConsentGrant
from ancestryllm.llm.providers.anthropic import AnthropicProvider
from ancestryllm.llm.providers.gemini import GeminiProvider
from ancestryllm.llm.providers.none import NoneProvider
from ancestryllm.llm.providers.ollama import OllamaProvider
from ancestryllm.llm.providers.openai import OpenAIProvider
from ancestryllm.llm.service import LLMService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    import httpx


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


class CompletionStream:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks
        self.closed = False

    def __enter__(self) -> CompletionStream:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def __iter__(self) -> Iterator[Any]:
        yield from self.chunks


def test_openai_client_configures_scalar_timeout_and_disables_retries(
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
    assert captured["timeout"] == 12.5


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
    assert captured["timeout"] == 12.5
    assert captured["max_completion_tokens"] == 23


@pytest.mark.parametrize(
    ("provider_id", "zero_data_retention", "expects_enforcement"),
    [
        ("openrouter", True, True),
        ("openrouter", False, False),
        ("openai", True, False),
    ],
)
def test_openrouter_generate_enforces_zdr_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    zero_data_retention: bool,
    expects_enforcement: bool,
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
    provider = OpenAIProvider(
        "key",
        provider_id=provider_id,
        base_url=(
            "https://openrouter.ai/api/v1"
            if provider_id == "openrouter"
            else "https://api.openai.com/v1"
        ),
        zero_data_retention=zero_data_retention,
    )
    monkeypatch.setattr(provider, "_client", lambda timeout: client)

    assert provider.generate(request(provider_id)).text == "answer"

    expected = {
        "provider": {
            "data_collection": "deny",
            "require_parameters": True,
            "zdr": True,
        }
    }
    if expects_enforcement:
        assert captured["extra_body"] == expected
    else:
        assert "extra_body" not in captured
    assert provider.capabilities.retention_known is expects_enforcement
    assert provider.capabilities.zero_data_retention is expects_enforcement


def test_openrouter_stream_enforces_zdr_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = CompletionStream(
        [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="answer"))])]
    )
    client = ContextClient()
    captured: dict[str, Any] = {}

    def create(**kwargs: Any) -> CompletionStream:
        captured.update(kwargs)
        return stream

    client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))  # type: ignore[attr-defined]
    provider = OpenAIProvider(
        "key",
        provider_id="openrouter",
        base_url="https://openrouter.ai/api/v1",
        zero_data_retention=True,
    )
    monkeypatch.setattr(provider, "_client", lambda timeout: client)

    assert list(provider.stream(request("openrouter"))) == ["answer"]

    assert captured["extra_body"] == {
        "provider": {
            "data_collection": "deny",
            "require_parameters": True,
            "zdr": True,
        }
    }
    assert captured["stream"] is True
    assert stream.closed
    assert client.closed


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


def test_ollama_generate_reuses_timed_client_until_provider_shutdown(
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
    assert not client.closed
    assert captured["options"]["num_predict"] == 23
    provider._clients[12.5] = client
    provider.close()
    assert client.closed


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
    if provider_id == "ollama":
        assert not client.closed
        provider._clients[12.5] = client
        provider.close()
        assert client.closed
    else:
        assert client.closed
    if provider_id == "openai":
        assert captured["timeout"] == 12.5
    if provider_id == "anthropic":
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


class InvalidRequestError(Exception):
    status_code = 400


@pytest.mark.parametrize(
    ("exc", "expected_code", "guidance"),
    [
        (TimeoutError(), "PROVIDER_TIMEOUT", "connectivity and provider status"),
        (RateLimitError(), "PROVIDER_RATE_LIMITED", "wait before retrying manually"),
        (ConnectionError(), "PROVIDER_TRANSIENT", "network connectivity and provider status"),
        (InvalidRequestError(), "PROVIDER_REQUEST_FAILED", "provider configuration"),
    ],
)
def test_provider_errors_have_stable_actionable_guidance(
    exc: Exception,
    expected_code: str,
    guidance: str,
) -> None:
    error = normalize_provider_error(exc, "test")

    assert error.code == expected_code
    assert "provider detail" not in error.message
    assert error.remediation is not None
    assert guidance in error.remediation
    assert "No further retry will be attempted automatically." in error.remediation
    if expected_code == "PROVIDER_RATE_LIMITED":
        assert error.details["retry_after_seconds"] == 60.0


def test_provider_stream_timeout_guidance_accounts_for_partial_output() -> None:
    error = normalize_provider_error(
        TimeoutError(),
        "test",
        streaming=True,
        stream_started=True,
    )

    assert error.code == "PROVIDER_STREAM_TIMEOUT"
    assert error.remediation is not None
    assert "partial output" in error.remediation
    assert "safe to duplicate" in error.remediation
    assert "No further retry will be attempted automatically." in error.remediation


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


class BlockingProvider(LifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.started.set()
        assert self.release.wait(2)
        return super().generate(request)


class BlockingStreamProvider(LifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed_event = threading.Event()

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        self.stream_called = True
        try:
            self.started.set()
            assert self.release.wait(2)
            yield "must not be consumed"
        finally:
            self.closed = True
            self.closed_event.set()


class PartialBlockingStreamProvider(LifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed_event = threading.Event()

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        self.stream_called = True
        try:
            yield "partial"
            self.started.set()
            assert self.release.wait(2)
            yield "must not be consumed"
        finally:
            self.closed = True
            self.closed_event.set()


class RapidStreamProvider(LifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.yield_count = 0
        self.closed_event = threading.Event()

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        del request
        self.stream_called = True
        self.stream_calls += 1
        try:
            while True:
                self.yield_count += 1
                yield f"chunk-{self.yield_count}"
        finally:
            self.closed = True
            self.closed_event.set()


class OversizedChunkProvider(LifecycleProvider):
    def stream(self, request: GenerationRequest) -> Iterator[str]:
        del request
        self.stream_called = True
        self.stream_calls += 1
        try:
            yield "sensitive oversized provider output"
        finally:
            self.closed = True


class CloseFailureIterator:
    def __init__(self) -> None:
        self.yielded = False
        self.closed = False

    def __iter__(self) -> CloseFailureIterator:
        return self

    def __next__(self) -> str:
        if not self.yielded:
            self.yielded = True
            return "partial"
        raise TimeoutError("fictional primary stream timeout")

    def close(self) -> None:
        self.closed = True
        raise CancellationError("fictional iterator close cancellation")


class CloseFailureProvider(LifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.iterator = CloseFailureIterator()

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        del request
        self.stream_called = True
        self.stream_calls += 1
        return self.iterator


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


async def collect_async_stream(stream: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in stream]


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


def test_service_stream_preserves_primary_failure_when_iterator_close_raises_baseexception() -> (
    None
):
    provider = CloseFailureProvider()
    llm, database = service(provider)

    with pytest.raises(ProviderError) as raised:
        list(llm.stream(request("test")))

    assert raised.value.code == "PROVIDER_STREAM_TIMEOUT"
    assert provider.iterator.closed is True
    assert database.rows[0].error_code == "PROVIDER_STREAM_TIMEOUT"


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


def test_service_async_stream_authorizes_before_starting_worker() -> None:
    provider = LifecycleProvider(remote=True)
    llm, database = service(provider)

    with pytest.raises(SecurityPolicyError, match="consent"):
        asyncio.run(collect_async_stream(llm.async_stream(request("test"))))

    assert not provider.stream_called
    assert database.rows == []


def test_service_async_stream_audits_success_without_retaining_payload() -> None:
    provider = LifecycleProvider()
    llm, database = service(provider)

    chunks = asyncio.run(collect_async_stream(llm.async_stream(request("test"))))

    assert chunks == ["partial", " complete"]
    assert provider.closed
    assert len(database.rows) == 1
    row = database.rows[0]
    assert row.status == "succeeded"
    assert row.input_payload is None
    assert row.output_payload is None
    assert row.response_hash == hashlib.sha256(b"partial complete").hexdigest()


def test_service_async_stream_retains_success_only_with_explicit_consent() -> None:
    provider = LifecycleProvider()
    llm, database = service(provider)
    generation_request = request("test")

    chunks = asyncio.run(
        collect_async_stream(llm.async_stream(generation_request, retention_consent()))
    )

    assert chunks == ["partial", " complete"]
    assert len(database.rows) == 1
    row = database.rows[0]
    assert row.status == "succeeded"
    assert row.input_payload == generation_request.model_dump_json()
    assert row.output_payload == "partial complete"


def test_service_async_stream_audits_partial_timeout_once_without_payload() -> None:
    provider = LifecycleProvider(fail_after_chunk=True)
    llm, database = service(provider)

    with pytest.raises(ProviderError) as raised:
        asyncio.run(collect_async_stream(llm.async_stream(request("test"))))

    assert raised.value.code == "PROVIDER_STREAM_TIMEOUT"
    assert provider.closed
    assert len(database.rows) == 1
    row = database.rows[0]
    assert row.status == "aborted"
    assert row.error_code == "PROVIDER_STREAM_TIMEOUT"
    assert row.response_hash is None
    assert row.input_payload is None
    assert row.output_payload is None


def test_service_async_stream_enforces_wall_clock_timeout_and_closes_provider() -> None:
    provider = BlockingStreamProvider()
    llm, database = service(provider)

    try:
        with pytest.raises(ProviderError) as raised:
            asyncio.run(
                collect_async_stream(llm.async_stream(request("test", timeout_seconds=1.0)))
            )
    finally:
        provider.release.set()

    assert raised.value.code == "PROVIDER_TIMEOUT"
    assert provider.closed_event.wait(2)
    assert len(database.rows) == 1
    row = database.rows[0]
    assert row.status == "failed"
    assert row.error_code == "PROVIDER_TIMEOUT"
    assert row.input_payload is None
    assert row.output_payload is None


def test_service_async_stream_timeout_never_cancels_consumer_between_chunks() -> None:
    provider = PartialBlockingStreamProvider()
    llm, database = service(provider)

    async def consume_after_deadline() -> None:
        stream = llm.async_stream(request("test", timeout_seconds=1.0))
        assert await anext(stream) == "partial"
        await asyncio.sleep(1.05)
        with pytest.raises(ProviderError) as raised:
            await anext(stream)
        assert raised.value.code == "PROVIDER_STREAM_TIMEOUT"

    try:
        asyncio.run(consume_after_deadline())
    finally:
        provider.release.set()

    assert provider.closed_event.wait(2)
    assert len(database.rows) == 1
    assert database.rows[0].status == "aborted"
    assert database.rows[0].error_code == "PROVIDER_STREAM_TIMEOUT"


def test_service_async_stream_cancellation_releases_worker_and_audits_once() -> None:
    provider = BlockingStreamProvider()
    llm, database = service(provider)

    async def cancel_request() -> None:
        stream = llm.async_stream(request("test"))
        pending = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(provider.started.wait, 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await stream.aclose()

    try:
        asyncio.run(cancel_request())
    finally:
        provider.release.set()

    assert provider.closed_event.wait(2)
    assert provider.closed
    assert len(database.rows) == 1
    row = database.rows[0]
    assert row.status == "aborted"
    assert row.error_code == "PROVIDER_CANCELLED"
    assert row.output_payload is None


def test_job_cancellation_propagates_through_async_stream_worker_context() -> None:
    provider = BlockingStreamProvider()
    llm, database = service(provider)
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "asynchronous provider stream",
            lambda: asyncio.run(collect_async_stream(llm.async_stream(request("test")))),
        )
        assert provider.started.wait(2)
        manager.cancel(job.job_id)
        provider.release.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        provider.release.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert provider.closed_event.wait(2)
    assert len(database.rows) == 1
    assert database.rows[0].status == "aborted"
    assert database.rows[0].error_code == "PROVIDER_CANCELLED"
    assert database.rows[0].output_payload is None


def test_job_cancellation_discards_retained_partial_async_stream_payload() -> None:
    provider = PartialBlockingStreamProvider()
    llm, database = service(provider)
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "retained asynchronous provider stream",
            lambda: asyncio.run(
                collect_async_stream(llm.async_stream(request("test"), retention_consent()))
            ),
        )
        assert provider.started.wait(2)
        manager.cancel(job.job_id)
        provider.release.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        provider.release.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert provider.closed is True
    assert len(database.rows) == 1
    row = database.rows[0]
    assert row.status == "aborted"
    assert row.error_code == "PROVIDER_CANCELLED"
    assert row.input_payload is not None
    assert row.output_payload is None


def test_service_async_stream_applies_bounded_backpressure() -> None:
    provider = RapidStreamProvider()
    database = AuditDatabase()
    llm = LLMService(
        StaticRegistry(provider),  # type: ignore[arg-type]
        database,  # type: ignore[arg-type]
        async_stream_queue_items=2,
    )

    async def consume_one_chunk() -> None:
        stream = llm.async_stream(request("test"))
        assert await anext(stream) == "chunk-1"
        await asyncio.sleep(0.1)
        assert provider.yield_count <= 4
        await stream.aclose()

    asyncio.run(consume_one_chunk())

    assert provider.closed_event.wait(2)
    assert len(database.rows) == 1
    assert database.rows[0].status == "aborted"
    assert database.rows[0].error_code == "PROVIDER_CANCELLED"


def test_service_async_stream_rejects_oversized_chunk_without_disclosure() -> None:
    provider = OversizedChunkProvider()
    database = AuditDatabase()
    llm = LLMService(
        StaticRegistry(provider),  # type: ignore[arg-type]
        database,  # type: ignore[arg-type]
        async_stream_max_chunk_bytes=8,
    )

    with pytest.raises(ProviderError) as raised:
        asyncio.run(collect_async_stream(llm.async_stream(request("test"))))

    assert raised.value.code == "PROVIDER_STREAM_CHUNK_TOO_LARGE"
    assert "sensitive" not in raised.value.render()
    assert provider.closed
    assert len(database.rows) == 1
    assert database.rows[0].status == "failed"
    assert database.rows[0].error_code == "PROVIDER_STREAM_CHUNK_TOO_LARGE"


@pytest.mark.parametrize(
    ("queue_items", "chunk_bytes"),
    [
        (True, 64 * 1024),
        (16, True),
        (17, 1024 * 1024),
    ],
)
def test_service_async_stream_rejects_unsafe_buffer_configuration(
    queue_items: int,
    chunk_bytes: int,
) -> None:
    provider = LifecycleProvider()

    with pytest.raises(ValueError, match="async stream"):
        LLMService(
            StaticRegistry(provider),  # type: ignore[arg-type]
            AuditDatabase(),  # type: ignore[arg-type]
            async_stream_queue_items=queue_items,
            async_stream_max_chunk_bytes=chunk_bytes,
        )


def test_service_async_stream_rejects_missing_stream_capability_before_call() -> None:
    provider = LifecycleProvider()
    provider.capabilities = provider.capabilities.model_copy(update={"streaming": False})
    llm, database = service(provider)

    with pytest.raises(ProviderError) as raised:
        asyncio.run(collect_async_stream(llm.async_stream(request("test"))))

    assert raised.value.code == "PROVIDER_STREAMING_UNSUPPORTED"
    assert provider.stream_calls == 0
    assert database.rows == []


def test_service_async_stream_rejects_structured_output_before_provider_call() -> None:
    provider = LifecycleProvider()
    llm, database = service(provider)
    structured_request = request("test").model_copy(
        update={
            "response_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            }
        }
    )

    with pytest.raises(ProviderError) as raised:
        asyncio.run(collect_async_stream(llm.async_stream(structured_request)))

    assert raised.value.code == "PROVIDER_STREAM_STRUCTURED_OUTPUT_UNSUPPORTED"
    assert provider.stream_calls == 0
    assert database.rows == []


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
    monkeypatch.setattr(llm, "_wait_for_retry", delays.append)
    retriable = request("test").model_copy(update={"max_safe_retries": 2})

    result = llm.generate(retriable)

    assert result.text == "complete"
    assert provider.generate_calls == 3
    assert delays == [0.25, 1.0]
    assert len(database.rows) == 1
    assert database.rows[0].status == "succeeded"


def test_job_cancellation_interrupts_provider_retry_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RetryingProvider(
        [
            ProviderError(
                "PROVIDER_RATE_LIMITED",
                "limited",
                details={"retry_after_seconds": 60.0},
            )
        ]
    )
    llm, database = service(provider)
    backoff_started = threading.Event()
    original_retry_delay = llm._retry_delay

    def observed_retry_delay(error: ProviderError, retry_attempt: int) -> float:
        backoff_started.set()
        return original_retry_delay(error, retry_attempt)

    monkeypatch.setattr(llm, "_retry_delay", observed_retry_delay)
    retriable = request("test").model_copy(update={"max_safe_retries": 1})
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit("provider retry backoff", lambda: llm.generate(retriable))
        assert backoff_started.wait(2)
        manager.cancel(job.job_id)
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert provider.generate_calls == 1
    assert len(database.rows) == 1
    assert database.rows[0].status == "aborted"
    assert database.rows[0].error_code == "PROVIDER_CANCELLED"


def test_job_cancellation_aborts_provider_request_after_bounded_call_returns() -> None:
    provider = BlockingProvider()
    llm, database = service(provider)
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit("provider request", lambda: llm.generate(request("test")))
        assert provider.started.wait(2)
        manager.cancel(job.job_id)
        provider.release.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        provider.release.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert len(database.rows) == 1
    assert database.rows[0].status == "aborted"
    assert database.rows[0].error_code == "PROVIDER_CANCELLED"


def test_job_cancellation_closes_provider_stream_and_discards_next_chunk() -> None:
    provider = BlockingStreamProvider()
    llm, database = service(provider)
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit("provider stream", lambda: list(llm.stream(request("test"))))
        assert provider.started.wait(2)
        manager.cancel(job.job_id)
        provider.release.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        provider.release.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert provider.closed is True
    assert len(database.rows) == 1
    assert database.rows[0].status == "aborted"
    assert database.rows[0].error_code == "PROVIDER_CANCELLED"
    assert database.rows[0].output_payload is None


def test_job_cancellation_discards_explicitly_retained_partial_stream_payload() -> None:
    provider = PartialBlockingStreamProvider()
    llm, database = service(provider)
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "retained provider stream",
            lambda: list(llm.stream(request("test"), retention_consent())),
        )
        assert provider.started.wait(2)
        manager.cancel(job.job_id)
        provider.release.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        provider.release.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert provider.closed is True
    assert len(database.rows) == 1
    assert database.rows[0].status == "aborted"
    assert database.rows[0].error_code == "PROVIDER_CANCELLED"
    assert database.rows[0].output_payload is None


def test_retry_backoff_is_cancellation_aware_and_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RetryingProvider(
        [
            ProviderError(
                "PROVIDER_RATE_LIMITED",
                "limited",
                details={"retry_after_seconds": 60.0},
            )
        ]
    )
    database = AuditDatabase()
    cancelled = False

    def cancellation_check() -> None:
        if cancelled:
            raise asyncio.CancelledError

    def cancel_during_wait(_delay: float) -> None:
        nonlocal cancelled
        cancelled = True

    llm = LLMService(
        StaticRegistry(provider),  # type: ignore[arg-type]
        database,  # type: ignore[arg-type]
        cancellation_check=cancellation_check,
    )
    monkeypatch.setattr("ancestryllm.llm.service.time.sleep", cancel_during_wait)
    retriable = request("test").model_copy(update={"max_safe_retries": 1})

    with pytest.raises(ProviderError) as raised:
        llm.generate(retriable)

    assert raised.value.code == "PROVIDER_CANCELLED"
    assert provider.generate_calls == 1
    assert len(database.rows) == 1
    assert database.rows[0].status == "aborted"
    assert database.rows[0].error_code == "PROVIDER_CANCELLED"


def test_cache_hit_does_not_duplicate_explicitly_retained_payloads() -> None:
    class CacheProvider(LifecycleProvider):
        def __init__(self) -> None:
            super().__init__()
            self.generate_calls = 0

        def generate(self, request: GenerationRequest) -> GenerationResult:
            self.generate_calls += 1
            return GenerationResult(
                provider_id="test",
                model=request.model,
                text='{"answer":"complete"}',
                parsed={"answer": "complete"},
                input_tokens=12,
                output_tokens=3,
                cost_usd=0.004,
            )

    provider = CacheProvider()
    llm, database = service(provider)
    cached_request = request("test").model_copy(
        update={
            "temperature": 0,
            "response_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
            "execution": ProviderExecution(cache_ttl_seconds=60),
        }
    )

    llm.generate(cached_request, retention_consent())
    llm.generate(cached_request, retention_consent())

    assert provider.generate_calls == 1
    assert [row.status for row in database.rows] == ["succeeded", "cache_hit"]
    assert database.rows[0].input_payload is not None
    assert database.rows[0].output_payload == '{"answer":"complete"}'
    assert database.rows[0].input_tokens == 12
    assert database.rows[0].output_tokens == 3
    assert database.rows[0].cost_usd == 0.004
    assert database.rows[1].input_payload is None
    assert database.rows[1].output_payload is None
    assert database.rows[1].input_tokens is None
    assert database.rows[1].output_tokens is None
    assert database.rows[1].cost_usd is None
