from __future__ import annotations

import os
import socket
import sys
from types import ModuleType
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from ancestryllm.core.errors import ProviderError, SecurityPolicyError, normalize_provider_error
from ancestryllm.core.secrets import MemorySecretStore
from ancestryllm.llm.contracts import (
    DataClass,
    GenerationRequest,
    Message,
    ProviderCapabilities,
)
from ancestryllm.llm.policy import ConsentGrant, ConsentPolicy, validate_endpoint
from ancestryllm.llm.providers.openai import OpenAIProvider
from ancestryllm.llm.registry import ProviderRegistry
from ancestryllm.llm.validation import validate_structured_output

REMOTE_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "openrouter": "https://openrouter.ai/api/v1",
}
PROVIDER_IDS = ("none", "ollama", "openai", "anthropic", "gemini", "openrouter")


def request(provider_id: str = "openai", *, max_output_tokens: int = 64) -> GenerationRequest:
    return GenerationRequest(
        provider_id=provider_id,
        model="contract-model",
        module_id="provider-contract",
        purpose="contract-smoke",
        messages=(Message(role="user", content="Fictional public genealogy data."),),
        response_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        data_classes=frozenset({DataClass.PUBLIC_GENEALOGY}),
        max_output_tokens=max_output_tokens,
        timeout_seconds=5.0,
    )


@pytest.mark.parametrize("max_output_tokens", [1, 32_768])
def test_output_token_budget_accepts_contract_boundaries(max_output_tokens: int) -> None:
    assert request(max_output_tokens=max_output_tokens).max_output_tokens == max_output_tokens


@pytest.mark.parametrize("max_output_tokens", [0, 32_769])
def test_output_token_budget_rejects_out_of_range_values(max_output_tokens: int) -> None:
    with pytest.raises(ValidationError):
        request(max_output_tokens=max_output_tokens)


def test_registry_characterizes_every_builtin_provider() -> None:
    registry = ProviderRegistry(
        MemorySecretStore(
            {
                "openai.api_key": "test-key",
                "anthropic.api_key": "test-key",
                "gemini.api_key": "test-key",
                "openrouter.api_key": "test-key",
            }
        )
    )

    capabilities = {
        provider_id: registry.create(provider_id).capabilities for provider_id in PROVIDER_IDS
    }

    assert set(capabilities) == {"none", "ollama", "openai", "anthropic", "gemini", "openrouter"}
    assert not capabilities["none"].remote
    assert not capabilities["ollama"].remote
    assert all(
        capabilities[provider_id].remote
        for provider_id in ("openai", "anthropic", "gemini", "openrouter")
    )
    assert not capabilities["anthropic"].structured_output
    assert all(
        capabilities[provider_id].streaming for provider_id in capabilities if provider_id != "none"
    )


def test_openrouter_uses_shared_timeout_and_retry_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    module = ModuleType("openai")

    def client(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    module.OpenAI = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)

    OpenAIProvider(
        "key", provider_id="openrouter", base_url=REMOTE_ENDPOINTS["openrouter"]
    )._client(7.5)

    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == timeout.read == timeout.write == timeout.pool == 7.5
    assert captured["max_retries"] == 0
    assert captured["base_url"] == REMOTE_ENDPOINTS["openrouter"]


class _Context:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> _Context:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True


class _InterruptedStream(_Context):
    def __iter__(self) -> Any:
        yield SimpleChunk("partial")
        raise TimeoutError("sensitive upstream interruption")


class SimpleChunk:
    def __init__(self, text: str) -> None:
        self.choices = [type("Choice", (), {"delta": type("Delta", (), {"content": text})()})()]


def test_openrouter_stream_interruption_is_sanitized_and_resources_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _InterruptedStream()
    client = _Context()
    client.chat = type(  # type: ignore[attr-defined]
        "Chat",
        (),
        {
            "completions": type(
                "Completions", (), {"create": staticmethod(lambda **_kwargs: stream)}
            )()
        },
    )()
    provider = OpenAIProvider(
        "key", provider_id="openrouter", base_url=REMOTE_ENDPOINTS["openrouter"]
    )
    monkeypatch.setattr(provider, "_client", lambda _timeout: client)

    with pytest.raises(ProviderError) as raised:
        list(provider.stream(request("openrouter")))

    assert raised.value.code == "PROVIDER_STREAM_TIMEOUT"
    assert "sensitive upstream interruption" not in raised.value.render()
    assert stream.closed
    assert client.closed


@pytest.mark.parametrize("provider_id", ["openai", "anthropic", "gemini", "openrouter", "ollama"])
@pytest.mark.parametrize(
    "text", ["not json", '{"ok": "not-a-boolean"}', '{"ok": true, "extra": 1}']
)
def test_malformed_structured_output_has_a_stable_sanitized_error(
    provider_id: str, text: str
) -> None:
    with pytest.raises(ProviderError) as raised:
        validate_structured_output(text, request(provider_id).response_schema)

    assert raised.value.code == "PROVIDER_OUTPUT_INVALID"
    assert text not in raised.value.render()
    assert text not in repr(raised.value.details)


@pytest.mark.parametrize("provider_id", ["openai", "anthropic", "gemini", "openrouter"])
def test_remote_provider_consent_denial_precedes_any_adapter_call(provider_id: str) -> None:
    capabilities = ProviderCapabilities(
        provider_id=provider_id,
        remote=True,
        structured_output=provider_id != "anthropic",
        streaming=True,
    )

    with pytest.raises(SecurityPolicyError) as raised:
        ConsentPolicy().authorize(request(provider_id), capabilities, None)

    assert raised.value.code == "CLOUD_CONSENT_REQUIRED"


def test_consent_budget_and_scope_are_preserved_for_preflight() -> None:
    grant = ConsentGrant(
        consent_id="contract-consent",
        provider_id="openai",
        allowed_modules=frozenset({"provider-contract"}),
        allowed_purposes=frozenset({"contract-smoke"}),
        allowed_data_classes=frozenset({DataClass.PUBLIC_GENEALOGY}),
        model_allowlist=("contract-*",),
        max_cost_usd=0.01,
    )

    ConsentPolicy().authorize(
        request(),
        ProviderCapabilities(
            provider_id="openai", remote=True, structured_output=True, streaming=True
        ),
        grant,
    )

    assert grant.max_cost_usd == 0.01


@pytest.mark.parametrize(("provider_id", "endpoint"), REMOTE_ENDPOINTS.items())
def test_builtin_remote_https_endpoints_are_allowed(provider_id: str, endpoint: str) -> None:
    validate_endpoint(provider_id, endpoint)


@pytest.mark.parametrize("provider_id", REMOTE_ENDPOINTS)
def test_remote_http_and_unlisted_https_endpoints_are_rejected(provider_id: str) -> None:
    with pytest.raises(SecurityPolicyError, match="HTTPS"):
        validate_endpoint(provider_id, "http://example.test/v1")
    with pytest.raises(SecurityPolicyError, match="allowlist"):
        validate_endpoint(provider_id, "https://example.test/v1")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
        "https://ollama.example.test",
    ],
)
def test_ollama_allows_loopback_http_or_non_loopback_https(endpoint: str) -> None:
    validate_endpoint("ollama", endpoint)


def test_ollama_rejects_non_loopback_http() -> None:
    with pytest.raises(SecurityPolicyError, match="non-loopback"):
        validate_endpoint("ollama", "http://192.0.2.10:11434")


def test_provider_failure_redacts_exception_text_and_secret() -> None:
    secret = "sk-contract-secret-never-render"
    error = normalize_provider_error(RuntimeError(f"upstream rejected {secret}"), "openai")

    rendered = error.render()
    assert secret not in rendered
    assert "upstream rejected" not in rendered
    assert error.details == {"error_type": "RuntimeError"}


def test_none_is_strictly_offline_with_keys_and_sdk_modules_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for environment_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.setenv(environment_name, "installed-but-must-not-be-read")
    for module_name in ("openai", "anthropic", "ollama", "google.genai"):
        monkeypatch.setitem(sys.modules, module_name, ModuleType(module_name))

    network_attempts: list[tuple[Any, ...]] = []

    def deny_network(*args: Any, **kwargs: Any) -> None:
        network_attempts.append((*args, kwargs))
        raise AssertionError("provider=none attempted network access")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    provider = ProviderRegistry(
        MemorySecretStore(
            {
                "openai.api_key": os.environ["OPENAI_API_KEY"],
                "anthropic.api_key": os.environ["ANTHROPIC_API_KEY"],
                "gemini.api_key": os.environ["GEMINI_API_KEY"],
                "openrouter.api_key": os.environ["OPENROUTER_API_KEY"],
            }
        )
    ).create("none")

    with pytest.raises(ProviderError) as generated:
        provider.generate(request("none"))
    with pytest.raises(ProviderError) as streamed:
        list(provider.stream(request("none")))

    assert generated.value.code == "PROVIDER_DISABLED"
    assert streamed.value.code == "PROVIDER_DISABLED"
    assert network_attempts == []
    assert provider.capabilities.provider_id == "none"
    assert not provider.capabilities.remote
    assert not provider.capabilities.structured_output
    assert not provider.capabilities.streaming
