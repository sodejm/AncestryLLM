"""Policy-enforcing generation service with privacy-minimal audit metadata."""

from __future__ import annotations

import datetime as dt
import hashlib

from ancestryllm.llm.contracts import GenerationRequest, GenerationResult
from ancestryllm.llm.policy import ConsentGrant, ConsentPolicy
from ancestryllm.llm.registry import ProviderRegistry
from ancestryllm.storage.database import Database
from ancestryllm.storage.models import LlmRunModel


class LLMService:
    def __init__(
        self, registry: ProviderRegistry, database: Database, policy: ConsentPolicy | None = None
    ) -> None:
        self.registry = registry
        self.database = database
        self.policy = policy or ConsentPolicy()

    def generate(
        self, request: GenerationRequest, consent: ConsentGrant | None = None
    ) -> GenerationResult:
        provider = self.registry.create(request.provider_id)
        self.policy.authorize(request, provider.capabilities, consent)
        canonical = request.model_dump_json()
        request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        retain = bool(consent and consent.retain_payloads)
        try:
            result = provider.generate(request)
        except Exception as exc:
            with self.database.session() as session:
                session.add(
                    LlmRunModel(
                        consent_profile_id=consent.consent_id if consent else None,
                        provider_id=request.provider_id,
                        model=request.model,
                        purpose=request.purpose,
                        request_hash=request_hash,
                        input_payload=canonical if retain else None,
                        status="failed",
                        error_code=getattr(exc, "code", type(exc).__name__),
                        started_at=started,
                        completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    )
                )
                session.commit()
            raise
        with self.database.session() as session:
            session.add(
                LlmRunModel(
                    consent_profile_id=consent.consent_id if consent else None,
                    provider_id=result.provider_id,
                    model=result.model,
                    purpose=request.purpose,
                    request_hash=request_hash,
                    response_hash=hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
                    input_payload=canonical if retain else None,
                    output_payload=result.text if retain else None,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_usd=result.cost_usd,
                    status="succeeded",
                    started_at=started,
                    completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                )
            )
            session.commit()
        return result
