"""Policy-enforcing generation service with privacy-minimal audit metadata."""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from collections.abc import Iterator

from ancestryllm.core.errors import (
    ProviderError,
    is_provider_cancellation,
    normalize_provider_error,
)
from ancestryllm.llm.contracts import GenerationRequest, GenerationResult, LLMProvider
from ancestryllm.llm.policy import ConsentGrant, ConsentPolicy
from ancestryllm.llm.registry import ProviderRegistry
from ancestryllm.storage.database import Database
from ancestryllm.storage.models import LlmRunModel

SAFE_RETRY_ERROR_CODES = frozenset({"PROVIDER_RATE_LIMITED", "PROVIDER_TRANSIENT"})
MAX_RETRY_DELAY_SECONDS = 60.0


class LLMService:
    def __init__(
        self, registry: ProviderRegistry, database: Database, policy: ConsentPolicy | None = None
    ) -> None:
        self.registry = registry
        self.database = database
        self.policy = policy or ConsentPolicy()

    @staticmethod
    def _request_metadata(request: GenerationRequest) -> tuple[str, str]:
        canonical = request.model_dump_json()
        request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return canonical, request_hash

    def _record_run(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None,
        *,
        request_hash: str,
        started_at: str,
        status: str,
        provider_id: str | None = None,
        response_hash: str | None = None,
        input_payload: str | None = None,
        output_payload: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        error_code: str | None = None,
    ) -> None:
        with self.database.session() as session:
            session.add(
                LlmRunModel(
                    consent_profile_id=consent.consent_id if consent else None,
                    provider_id=provider_id or request.provider_id,
                    model=request.model,
                    purpose=request.purpose,
                    request_hash=request_hash,
                    response_hash=response_hash,
                    input_payload=input_payload,
                    output_payload=output_payload,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    status=status,
                    error_code=error_code,
                    started_at=started_at,
                    completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                )
            )
            session.commit()

    def generate(
        self, request: GenerationRequest, consent: ConsentGrant | None = None
    ) -> GenerationResult:
        provider = self.registry.create(request.provider_id)
        self.policy.authorize(request, provider.capabilities, consent)
        canonical, request_hash = self._request_metadata(request)
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        retain = bool(consent and consent.retain_payloads)
        retry_attempt = 0
        while True:
            try:
                result = provider.generate(request)
                break
            except BaseException as exc:
                if not isinstance(exc, Exception) and not is_provider_cancellation(exc):
                    raise
                error = normalize_provider_error(exc, request.provider_id)
                if self._should_retry(request, error, retry_attempt):
                    time.sleep(self._retry_delay(error, retry_attempt))
                    retry_attempt += 1
                    continue
                self._record_run(
                    request,
                    consent,
                    request_hash=request_hash,
                    started_at=started,
                    status="aborted" if error.code == "PROVIDER_CANCELLED" else "failed",
                    input_payload=canonical if retain else None,
                    error_code=error.code,
                )
                if error is exc:
                    raise
                raise error from exc
        self._record_run(
            request,
            consent,
            request_hash=request_hash,
            started_at=started,
            status="succeeded",
            provider_id=result.provider_id,
            response_hash=hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
            input_payload=canonical if retain else None,
            output_payload=result.text if retain else None,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
        )
        return result

    @staticmethod
    def _should_retry(request: GenerationRequest, error: ProviderError, retry_attempt: int) -> bool:
        return retry_attempt < request.max_safe_retries and error.code in SAFE_RETRY_ERROR_CODES

    @staticmethod
    def _retry_delay(error: ProviderError, retry_attempt: int) -> float:
        retry_after = error.details.get("retry_after_seconds")
        if isinstance(retry_after, (int, float)):
            retry_after_seconds: float = float(retry_after)
            return min(max(retry_after_seconds, 0.0), MAX_RETRY_DELAY_SECONDS)
        backoff_seconds: float = 0.5 * (2.0**retry_attempt)
        return min(backoff_seconds, MAX_RETRY_DELAY_SECONDS)

    def stream(
        self, request: GenerationRequest, consent: ConsentGrant | None = None
    ) -> Iterator[str]:
        """Authorize and audit a provider stream without retaining partial output by default."""

        provider = self.registry.create(request.provider_id)
        self.policy.authorize(request, provider.capabilities, consent)
        canonical, request_hash = self._request_metadata(request)
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        retain = bool(consent and consent.retain_payloads)
        return self._stream_lifecycle(
            request,
            consent,
            provider,
            canonical=canonical,
            request_hash=request_hash,
            started_at=started,
            retain=retain,
        )

    def _stream_lifecycle(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None,
        provider: LLMProvider,
        *,
        canonical: str,
        request_hash: str,
        started_at: str,
        retain: bool,
    ) -> Iterator[str]:
        response_hasher = hashlib.sha256()
        retained_chunks: list[str] | None = [] if retain else None
        stream_started = False
        failure: BaseException | None = None
        iterator: Iterator[str] | None = None
        try:
            iterator = iter(provider.stream(request))
            for chunk in iterator:
                stream_started = True
                encoded = chunk.encode("utf-8")
                response_hasher.update(encoded)
                if retained_chunks is not None:
                    retained_chunks.append(chunk)
                yield chunk
        except BaseException as exc:  # noqa: BLE001 - cancellation is outside Exception
            failure = exc
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - adapters may expose arbitrary close errors
                    if failure is None:
                        failure = exc

        if failure is not None:
            if not isinstance(failure, Exception) and not is_provider_cancellation(failure):
                raise failure
            error = normalize_provider_error(
                failure,
                request.provider_id,
                streaming=True,
                stream_started=stream_started,
            )
            partial_output = (
                "".join(retained_chunks) if retained_chunks is not None and stream_started else None
            )
            self._record_run(
                request,
                consent,
                request_hash=request_hash,
                started_at=started_at,
                status="aborted"
                if stream_started or error.code == "PROVIDER_CANCELLED"
                else "failed",
                input_payload=canonical if retain else None,
                output_payload=partial_output,
                error_code=error.code,
            )
            if isinstance(failure, GeneratorExit):
                return
            if error is failure:
                raise error
            raise error from failure

        self._record_run(
            request,
            consent,
            request_hash=request_hash,
            started_at=started_at,
            status="succeeded",
            response_hash=response_hasher.hexdigest(),
            input_payload=canonical if retain else None,
            output_payload="".join(retained_chunks) if retained_chunks is not None else None,
        )
