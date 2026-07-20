"""Opt-in provider connectivity smoke tests using only fictional public data."""

from __future__ import annotations

import os

import pytest

from ancestryllm.core.secrets import MemorySecretStore
from ancestryllm.llm.contracts import DataClass, GenerationRequest, Message
from ancestryllm.llm.policy import ConsentGrant, ConsentPolicy
from ancestryllm.llm.registry import ProviderRegistry

ENABLE_FLAG = "ANCESTRYLLM_LIVE_PROVIDER_TESTS"
CONSENT_FLAG = "ANCESTRYLLM_LIVE_PROVIDER_CONSENT"
CONSENT_VALUE = "I_CONSENT_TO_PROVIDER_NETWORK_CALLS"
BUDGET_FLAG = "ANCESTRYLLM_LIVE_MAX_OUTPUT_TOKENS"

PROVIDERS = {
    "openai": ("OPENAI_API_KEY", "ANCESTRYLLM_LIVE_OPENAI_MODEL"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANCESTRYLLM_LIVE_ANTHROPIC_MODEL"),
    "gemini": ("GEMINI_API_KEY", "ANCESTRYLLM_LIVE_GEMINI_MODEL"),
    "openrouter": ("OPENROUTER_API_KEY", "ANCESTRYLLM_LIVE_OPENROUTER_MODEL"),
    "ollama": (None, "ANCESTRYLLM_LIVE_OLLAMA_MODEL"),
}


def _explicit_live_budget() -> int:
    if os.getenv(ENABLE_FLAG) != "1" or os.getenv(CONSENT_FLAG) != CONSENT_VALUE:
        pytest.skip(f"requires {ENABLE_FLAG}=1 and explicit {CONSENT_FLAG}")
    raw_budget = os.getenv(BUDGET_FLAG)
    if raw_budget is None:
        pytest.skip(f"requires an explicit {BUDGET_FLAG} between 1 and 256")
    try:
        budget = int(raw_budget)
    except ValueError:
        pytest.fail(f"{BUDGET_FLAG} must be an integer")
    if not 1 <= budget <= 256:
        pytest.fail(f"{BUDGET_FLAG} must be between 1 and 256")
    return budget


@pytest.mark.parametrize("provider_id", PROVIDERS)
def test_live_provider_smoke(provider_id: str) -> None:
    max_output_tokens = _explicit_live_budget()
    key_environment, model_environment = PROVIDERS[provider_id]
    model = os.getenv(model_environment)
    if not model:
        pytest.skip(f"requires {model_environment}")
    api_key = os.getenv(key_environment) if key_environment else None
    if key_environment and not api_key:
        pytest.skip(f"requires {key_environment}")

    secrets = MemorySecretStore({f"{provider_id}.api_key": api_key} if api_key else {})
    base_url = os.getenv("ANCESTRYLLM_LIVE_OLLAMA_ENDPOINT") if provider_id == "ollama" else None
    provider = ProviderRegistry(secrets).create(provider_id, base_url=base_url)
    request = GenerationRequest(
        provider_id=provider_id,
        model=model,
        module_id="provider-contract",
        purpose="live-smoke",
        messages=(
            Message(
                role="user",
                content="Reply briefly that the fictional public genealogy provider smoke test works.",
            ),
        ),
        data_classes=frozenset({DataClass.PUBLIC_GENEALOGY}),
        max_output_tokens=max_output_tokens,
        timeout_seconds=30.0,
    )
    consent = ConsentGrant(
        consent_id="live-provider-smoke",
        provider_id=provider_id,
        allowed_modules=frozenset({"provider-contract"}),
        allowed_purposes=frozenset({"live-smoke"}),
        allowed_data_classes=frozenset({DataClass.PUBLIC_GENEALOGY}),
        model_allowlist=(model,),
        retain_payloads=False,
    )
    ConsentPolicy().authorize(request, provider.capabilities, consent)

    result = provider.generate(request)

    assert result.provider_id == provider_id
    assert result.model == model
    assert result.text.strip()
