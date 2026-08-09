"""Verify provider consent, locality, endpoint, and offline execution policy."""

from __future__ import annotations

import pytest

from ancestryllm.core.errors import ProviderError, SecurityPolicyError
from ancestryllm.core.secrets import MemorySecretStore
from ancestryllm.llm.contracts import (
    DataClass,
    GenerationRequest,
    Message,
    ProviderCapabilities,
    ProviderExecution,
)
from ancestryllm.llm.policy import ConsentGrant, ConsentPolicy, validate_endpoint
from ancestryllm.llm.registry import ProviderRegistry


def request(provider: str = "openai") -> GenerationRequest:
    return GenerationRequest(
        provider_id=provider,
        model="test-model",
        module_id="gedcom",
        purpose="identity_adjudication",
        messages=(Message(role="user", content="fictional bounded summary"),),
        data_classes=frozenset({DataClass.DECEASED_PERSON}),
    )


def test_remote_provider_requires_matching_consent() -> None:
    capabilities = ProviderCapabilities(
        provider_id="openai", remote=True, structured_output=True, streaming=True
    )
    with pytest.raises(SecurityPolicyError, match="consent"):
        ConsentPolicy().authorize(request(), capabilities, None)


def test_local_retention_consent_must_match_the_exact_profile_and_scope() -> None:
    capabilities = ProviderCapabilities(
        provider_id="ollama", remote=False, structured_output=True, streaming=True
    )
    local_request = request("ollama").model_copy(
        update={"execution": ProviderExecution(profile_name="local-one")}
    )
    wrong_grant = ConsentGrant(
        "wrong-consent",
        "ollama",
        frozenset({"gedcom"}),
        frozenset({"identity_adjudication"}),
        frozenset({DataClass.DECEASED_PERSON}),
        ("test-*",),
        retain_payloads=True,
        provider_profile_name="local-two",
    )

    with pytest.raises(SecurityPolicyError) as raised:
        ConsentPolicy().authorize(local_request, capabilities, wrong_grant)

    assert raised.value.code == "CONSENT_PROFILE_MISMATCH"


def test_consent_denies_living_data_by_default() -> None:
    grant = ConsentGrant(
        "consent",
        "openai",
        frozenset({"gedcom"}),
        frozenset({"identity_adjudication"}),
        frozenset({DataClass.DECEASED_PERSON}),
        ("test-*",),
    )
    living = request().model_copy(update={"data_classes": frozenset({DataClass.LIVING_PERSON})})
    capabilities = ProviderCapabilities(
        provider_id="openai", remote=True, structured_output=True, streaming=True
    )
    with pytest.raises(SecurityPolicyError, match="data class"):
        ConsentPolicy().authorize(living, capabilities, grant)


def test_none_provider_never_calls_a_network_case() -> None:
    provider = ProviderRegistry(MemorySecretStore({})).create("none")
    with pytest.raises(ProviderError, match="strictly offline"):
        provider.generate(request("none"))


@pytest.mark.parametrize(
    "provider,endpoint",
    [("openai", "http://api.openai.com/v1"), ("ollama", "http://192.168.1.2:11434")],
)
def test_unsafe_provider_endpoints_are_rejected(provider: str, endpoint: str) -> None:
    with pytest.raises(SecurityPolicyError):
        validate_endpoint(provider, endpoint)


def test_arbitrary_https_cloud_endpoint_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="allowlist"):
        validate_endpoint("openai", "https://attacker.example/v1")
