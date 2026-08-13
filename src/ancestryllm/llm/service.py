"""Policy-enforcing generation service with privacy-minimal audit metadata."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import importlib
import secrets
import time
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ancestryllm.core.errors import (
    ProviderError,
    is_provider_cancellation,
    normalize_provider_error,
)
from ancestryllm.llm.async_stream import (
    DEFAULT_ASYNC_STREAM_MAX_CHUNK_BYTES,
    DEFAULT_ASYNC_STREAM_QUEUE_ITEMS,
    BoundedAsyncStreamBridge,
    validate_async_stream_bounds,
)
from ancestryllm.llm.execution import (
    CancellationCheck,
    ExactResultCache,
    ProviderExecutionCoordinator,
)
from ancestryllm.llm.policy import ConsentGrant, ConsentPolicy
from ancestryllm.llm.validation import validate_structured_output
from ancestryllm.storage.models import LlmRunModel

if TYPE_CHECKING:
    import threading
    from collections.abc import AsyncIterator, Callable, Iterator

    from sqlalchemy.engine import CursorResult

    from ancestryllm.llm.contracts import (
        GenerationRequest,
        GenerationResult,
        LLMProvider,
        ProviderCapabilities,
    )
    from ancestryllm.llm.profiles import ProviderProfileService
    from ancestryllm.llm.registry import ProviderRegistry
    from ancestryllm.storage.database import Database

__all__ = ["LLMService"]

SAFE_RETRY_ERROR_CODES = frozenset({"PROVIDER_RATE_LIMITED", "PROVIDER_TRANSIENT"})
MAX_RETRY_DELAY_SECONDS = 60.0


class LLMService:
    def __init__(
        self,
        registry: ProviderRegistry,
        database: Database,
        policy: ConsentPolicy | None = None,
        profiles: ProviderProfileService | None = None,
        *,
        execution: ProviderExecutionCoordinator | None = None,
        cache: ExactResultCache | None = None,
        cancellation_check: CancellationCheck | None = None,
        async_stream_queue_items: int = DEFAULT_ASYNC_STREAM_QUEUE_ITEMS,
        async_stream_max_chunk_bytes: int = DEFAULT_ASYNC_STREAM_MAX_CHUNK_BYTES,
    ) -> None:
        validate_async_stream_bounds(
            max_items=async_stream_queue_items,
            max_chunk_bytes=async_stream_max_chunk_bytes,
        )
        self.registry = registry
        self.database = database
        self.policy = policy or ConsentPolicy()
        self.profiles = profiles
        self.execution = execution or ProviderExecutionCoordinator()
        self.cache = cache or ExactResultCache()
        self._cache_key = secrets.token_bytes(32)
        self._explicit_cancellation_check = cancellation_check
        self._async_stream_queue_items = async_stream_queue_items
        self._async_stream_max_chunk_bytes = async_stream_max_chunk_bytes

    @staticmethod
    def _request_metadata(request: GenerationRequest) -> tuple[str, str]:
        canonical = request.model_dump_json()
        request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return canonical, request_hash

    @staticmethod
    def _ambient_cancellation_check() -> None:
        """Use the job token when the cooperative-cancellation module is installed."""

        try:
            module = importlib.import_module("ancestryllm.core.cancellation")
        except ModuleNotFoundError as exc:
            if exc.name == "ancestryllm.core.cancellation":
                return
            raise
        checkpoint = getattr(module, "cancellation_checkpoint", None)
        if checkpoint is not None:
            checkpoint()

    def _check_cancellation(self) -> None:
        callback = self._explicit_cancellation_check or self._ambient_cancellation_check
        callback()

    def _resolve_request(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None,
        *,
        enforce_request_bounds: bool = False,
    ) -> GenerationRequest:
        if self.profiles is None:
            return request
        return self.profiles.resolve_request(
            request,
            consent,
            enforce_request_bounds=enforce_request_bounds,
        )

    def _provider(self, request: GenerationRequest) -> LLMProvider:
        execution = request.execution
        if (
            execution.base_url is None
            and execution.profile_name is None
            and execution.zero_data_retention
        ):
            return self.registry.create(request.provider_id)
        return self.registry.create(
            request.provider_id,
            base_url=execution.base_url,
            zero_data_retention=execution.zero_data_retention,
            profile_name=execution.profile_name,
        )

    def _prepare(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None,
        *,
        enforce_request_bounds: bool = False,
    ) -> tuple[GenerationRequest, LLMProvider]:
        planned_request = self._resolve_request(
            request,
            consent,
            enforce_request_bounds=enforce_request_bounds,
        )
        self._check_cancellation()
        provider = self._provider(planned_request)
        self.policy.authorize(planned_request, provider.capabilities, consent)
        return planned_request, provider

    def preflight(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None = None,
        *,
        enforce_request_bounds: bool = False,
    ) -> tuple[GenerationRequest, ProviderCapabilities]:
        """Resolve and authorize a request without executing or auditing generation."""

        planned_request, provider = self._prepare(
            request,
            consent,
            enforce_request_bounds=enforce_request_bounds,
        )
        return planned_request, provider.capabilities

    @staticmethod
    def _execution_key(request: GenerationRequest) -> tuple[str, ...]:
        execution = request.execution
        return (
            request.provider_id,
            execution.profile_name or "direct",
            execution.base_url or "default",
            request.model,
        )

    def _exact_cache_key(
        self,
        canonical: str,
        consent: ConsentGrant | None,
    ) -> str:
        scope = consent.consent_id if consent is not None else "local-workspace"
        payload = f"ancestryllm-exact-v1\0{scope}\0{canonical}".encode()
        return hmac.new(self._cache_key, payload, hashlib.sha256).hexdigest()

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
        audit_run_id: str | None = None,
    ) -> None:
        completed_at = dt.datetime.now(dt.UTC).isoformat()
        with self.database.session() as session:
            if audit_run_id is not None:
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(LlmRunModel)
                        .where(
                            LlmRunModel.id == audit_run_id,
                            LlmRunModel.status == "running",
                            LlmRunModel.completed_at.is_(None),
                        )
                        .values(
                            provider_id=provider_id or request.provider_id,
                            response_hash=response_hash,
                            input_payload=input_payload,
                            output_payload=output_payload,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cost_usd=cost_usd,
                            status=status,
                            error_code=error_code,
                            completed_at=completed_at,
                        )
                    ),
                )
                if result.rowcount == 1:
                    session.commit()
                    return
                existing = session.get(LlmRunModel, audit_run_id)
                if existing is not None and existing.status != "running":
                    session.rollback()
                    return
                session.rollback()
                raise ProviderError(
                    "CHAT_STREAM_AUDIT_INVALID",
                    "The streaming audit lifecycle could not be terminalized.",
                )
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
                    completed_at=completed_at,
                )
            )
            session.commit()

    @staticmethod
    def _validate_audit_run_id(run_id: str) -> None:
        if (
            not isinstance(run_id, str)
            or len(run_id) != 36
            or not run_id.startswith("run_")
            or any(character not in "0123456789abcdef" for character in run_id[4:])
        ):
            raise ProviderError(
                "CHAT_STREAM_ID_INVALID",
                "The streaming audit identifier is invalid.",
            )

    def _begin_stream_run(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None,
        *,
        audit_run_id: str,
        request_hash: str,
        started_at: str,
        input_payload: str | None,
    ) -> None:
        self._validate_audit_run_id(audit_run_id)
        try:
            with self.database.session() as session:
                session.add(
                    LlmRunModel(
                        id=audit_run_id,
                        consent_profile_id=consent.consent_id if consent else None,
                        provider_id=request.provider_id,
                        model=request.model,
                        purpose=request.purpose,
                        request_hash=request_hash,
                        input_payload=input_payload,
                        status="running",
                        started_at=started_at,
                        completed_at=None,
                    )
                )
                session.commit()
        except IntegrityError as exc:
            raise ProviderError(
                "CHAT_STREAM_ID_CONFLICT",
                "The streaming audit identifier already exists.",
            ) from exc

    def terminalize_stream_audit(self, run_id: str, *, error_code: str) -> bool:
        """Idempotently interrupt an unfinished chat stream without payload retention."""

        self._validate_audit_run_id(run_id)
        completed_at = dt.datetime.now(dt.UTC).isoformat()
        with self.database.session() as session:
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    update(LlmRunModel)
                    .where(
                        LlmRunModel.id == run_id,
                        LlmRunModel.status == "running",
                        LlmRunModel.completed_at.is_(None),
                    )
                    .values(
                        status="aborted",
                        error_code=error_code,
                        input_payload=None,
                        output_payload=None,
                        completed_at=completed_at,
                    )
                ),
            )
            changed = result.rowcount == 1
            session.commit()
            return changed

    def reconcile_interrupted_stream_runs(self) -> int:
        """Fail closed any chat stream audit left running across process restart."""

        completed_at = dt.datetime.now(dt.UTC).isoformat()
        with self.database.session() as session:
            run_ids = tuple(
                session.scalars(
                    select(LlmRunModel.id).where(
                        LlmRunModel.id.like("run\\_%", escape="\\"),
                        LlmRunModel.status == "running",
                        LlmRunModel.completed_at.is_(None),
                    )
                )
            )
            if not run_ids:
                return 0
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    update(LlmRunModel)
                    .where(
                        LlmRunModel.id.in_(run_ids),
                        LlmRunModel.status == "running",
                        LlmRunModel.completed_at.is_(None),
                    )
                    .values(
                        status="aborted",
                        error_code="CHAT_STREAM_RESTART_INTERRUPTED",
                        input_payload=None,
                        output_payload=None,
                        completed_at=completed_at,
                    )
                ),
            )
            session.commit()
            return int(result.rowcount or 0)

    def generate(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None = None,
        *,
        enforce_request_bounds: bool = False,
    ) -> GenerationResult:
        planned_request, provider = self._prepare(
            request,
            consent,
            enforce_request_bounds=enforce_request_bounds,
        )
        canonical, request_hash = self._request_metadata(planned_request)
        started = dt.datetime.now(dt.UTC).isoformat()
        retain = bool(consent and consent.retain_payloads)
        cache_hit = False
        try:
            with self.execution.admission(
                self._execution_key(planned_request),
                max_pending=planned_request.execution.max_pending,
            ):
                if self._cache_eligible(planned_request):
                    result, cache_hit = self.cache.get_or_execute(
                        self._exact_cache_key(canonical, consent),
                        lambda: self._generate_uncached(planned_request, provider),
                        ttl_seconds=planned_request.execution.cache_ttl_seconds,
                        max_entries=planned_request.execution.cache_max_entries,
                        timeout_seconds=planned_request.timeout_seconds,
                        cancellation_check=self._check_cancellation,
                        cache_when=lambda item: self._cache_result_valid(planned_request, item),
                    )
                else:
                    result = self._generate_uncached(planned_request, provider)
        except BaseException as exc:
            if not isinstance(exc, Exception) and not is_provider_cancellation(exc):
                raise
            error = normalize_provider_error(exc, planned_request.provider_id)
            self._record_run(
                planned_request,
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
            planned_request,
            consent,
            request_hash=request_hash,
            started_at=started,
            status="cache_hit" if cache_hit else "succeeded",
            provider_id=result.provider_id,
            response_hash=hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
            input_payload=canonical if retain and not cache_hit else None,
            output_payload=result.text if retain and not cache_hit else None,
            input_tokens=None if cache_hit else result.input_tokens,
            output_tokens=None if cache_hit else result.output_tokens,
            cost_usd=None if cache_hit else result.cost_usd,
        )
        return result

    @staticmethod
    def _cache_eligible(request: GenerationRequest) -> bool:
        return (
            request.execution.cache_ttl_seconds > 0
            and request.temperature == 0
            and request.response_schema is not None
        )

    @staticmethod
    def _cache_result_valid(
        request: GenerationRequest,
        result: GenerationResult,
    ) -> bool:
        if result.parsed is None or request.response_schema is None:
            return False
        try:
            parsed = validate_structured_output(result.text, request.response_schema)
        except ProviderError:
            return False
        return bool(parsed == result.parsed)

    def _generate_uncached(
        self,
        request: GenerationRequest,
        provider: LLMProvider,
    ) -> GenerationResult:
        retry_attempt = 0
        while True:
            try:
                self._check_cancellation()
                with self.execution.capacity(
                    self._execution_key(request),
                    max_concurrency=request.execution.max_concurrency,
                    timeout_seconds=request.timeout_seconds,
                    cancellation_check=self._check_cancellation,
                ):
                    result = provider.generate(request)
                self._check_cancellation()
                return result.model_copy(update={"remote": provider.capabilities.remote})
            except BaseException as exc:
                if not isinstance(exc, Exception) and not is_provider_cancellation(exc):
                    raise
                error = normalize_provider_error(exc, request.provider_id)
                if not self._should_retry(request, error, retry_attempt):
                    if error is exc:
                        raise
                    raise error from exc
                self._check_cancellation()
                self._wait_for_retry(self._retry_delay(error, retry_attempt))
                retry_attempt += 1

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

    def _wait_for_retry(self, delay_seconds: float) -> None:
        """Wait in short intervals so cancellation can interrupt provider backoff."""

        deadline = time.monotonic() + delay_seconds
        while True:
            self._check_cancellation()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    def stream(
        self, request: GenerationRequest, consent: ConsentGrant | None = None
    ) -> Iterator[str]:
        """Authorize and audit a provider stream without retaining partial output by default."""

        planned_request, provider = self._prepare(request, consent)
        canonical, request_hash = self._request_metadata(planned_request)
        started = dt.datetime.now(dt.UTC).isoformat()
        retain = bool(consent and consent.retain_payloads)
        return self._stream_lifecycle(
            planned_request,
            consent,
            provider,
            canonical=canonical,
            request_hash=request_hash,
            started_at=started,
            retain=retain,
        )

    def async_stream(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None = None,
        *,
        enforce_request_bounds: bool = False,
        audit_run_id: str | None = None,
        max_response_characters: int | None = None,
    ) -> AsyncIterator[str]:
        """Adapt one authorized synchronous provider stream without blocking asyncio."""

        planned_request, provider = self._prepare(
            request,
            consent,
            enforce_request_bounds=enforce_request_bounds,
        )
        if planned_request.response_schema is not None:
            raise ProviderError(
                "PROVIDER_STREAM_STRUCTURED_OUTPUT_UNSUPPORTED",
                "Structured output requires the validated non-streaming generation path.",
                "Use generate for requests with a response schema.",
            )
        if not provider.capabilities.streaming:
            raise ProviderError(
                "PROVIDER_STREAMING_UNSUPPORTED",
                f"The {planned_request.provider_id} provider does not support streaming.",
                "Select a streaming-capable provider or use non-streaming generation.",
            )

        canonical, request_hash = self._request_metadata(planned_request)
        started = dt.datetime.now(dt.UTC).isoformat()
        retain = bool(consent and consent.retain_payloads)
        if max_response_characters is not None and (
            isinstance(max_response_characters, bool) or max_response_characters < 1
        ):
            raise ValueError("maximum stream response characters must be positive")
        if audit_run_id is not None:
            self._begin_stream_run(
                planned_request,
                consent,
                audit_run_id=audit_run_id,
                request_hash=request_hash,
                started_at=started,
                input_payload=canonical if retain else None,
            )
        return self._async_stream_lifecycle(
            planned_request,
            consent,
            provider,
            canonical=canonical,
            request_hash=request_hash,
            started_at=started,
            retain=retain,
            audit_run_id=audit_run_id,
            max_response_characters=max_response_characters,
        )

    async def _async_stream_lifecycle(
        self,
        planned_request: GenerationRequest,
        consent: ConsentGrant | None,
        provider: LLMProvider,
        *,
        canonical: str,
        request_hash: str,
        started_at: str,
        retain: bool,
        audit_run_id: str | None,
        max_response_characters: int | None,
    ) -> AsyncIterator[str]:
        response_hasher = hashlib.sha256()
        retained_chunks: list[str] | None = [] if retain else None
        response_characters = 0
        stream_started = False
        failure: BaseException | None = None
        caller_cancelled = False
        bridge = BoundedAsyncStreamBridge(
            lambda publish, stop: self._produce_async_stream(
                planned_request,
                provider,
                publish,
                stop,
            ),
            max_items=self._async_stream_queue_items,
            max_chunk_bytes=self._async_stream_max_chunk_bytes,
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + planned_request.timeout_seconds
        deadline_handle: asyncio.TimerHandle | None = None
        try:
            bridge.start()
            deadline_handle = loop.call_at(deadline, bridge.cancel)
            while True:
                if loop.time() >= deadline:
                    raise TimeoutError
                async with asyncio.timeout_at(deadline):
                    item = await bridge.receive()
                if item.failure is not None:
                    failure = item.failure
                    break
                if item.complete:
                    break
                chunk = item.chunk
                if chunk is None:
                    raise ProviderError(
                        "PROVIDER_STREAM_BRIDGE_INVALID",
                        "The provider stream bridge returned an invalid item.",
                    )
                response_characters += len(chunk)
                if (
                    max_response_characters is not None
                    and response_characters > max_response_characters
                ):
                    raise ProviderError(
                        "CHAT_PROVIDER_OUTPUT_INVALID",
                        "The provider returned an oversized chat response.",
                    )
                stream_started = True
                response_hasher.update(chunk.encode("utf-8"))
                if retained_chunks is not None:
                    retained_chunks.append(chunk)
                yield chunk
        except BaseException as exc:  # noqa: BLE001 - cancellation is outside Exception
            failure = exc
            caller_cancelled = isinstance(exc, asyncio.CancelledError)
        finally:
            if deadline_handle is not None:
                deadline_handle.cancel()
            bridge.cancel()

        if failure is None and max_response_characters is not None and response_characters == 0:
            failure = ProviderError(
                "CHAT_PROVIDER_OUTPUT_INVALID",
                "The provider returned an empty chat response.",
            )

        if failure is not None:
            error = self._record_stream_failure(
                planned_request,
                consent,
                failure,
                canonical=canonical,
                request_hash=request_hash,
                started_at=started_at,
                retain=retain,
                retained_chunks=retained_chunks,
                stream_started=stream_started,
                audit_run_id=audit_run_id,
            )
            if isinstance(failure, GeneratorExit):
                return
            if caller_cancelled:
                raise failure
            if error is failure:
                raise error
            raise error from failure

        self._record_run(
            planned_request,
            consent,
            request_hash=request_hash,
            started_at=started_at,
            status="succeeded",
            response_hash=response_hasher.hexdigest(),
            input_payload=canonical if retain else None,
            output_payload=("".join(retained_chunks) if retained_chunks is not None else None),
            audit_run_id=audit_run_id,
        )

    def _produce_async_stream(
        self,
        request: GenerationRequest,
        provider: LLMProvider,
        publish: Callable[[str], None],
        stop: threading.Event,
    ) -> None:
        failure: BaseException | None = None
        iterator: Iterator[str] | None = None

        def check_cancellation() -> None:
            if stop.is_set():
                raise asyncio.CancelledError
            self._check_cancellation()

        try:
            with self.execution.lease(
                self._execution_key(request),
                max_concurrency=request.execution.max_concurrency,
                max_pending=request.execution.max_pending,
                timeout_seconds=request.timeout_seconds,
                cancellation_check=check_cancellation,
            ):
                check_cancellation()
                iterator = iter(provider.stream(request))
                for chunk in iterator:
                    check_cancellation()
                    publish(chunk)
                check_cancellation()
        except BaseException as exc:  # noqa: BLE001 - cancellation is outside Exception
            failure = exc
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                try:
                    close()
                except BaseException as exc:  # noqa: BLE001 - cancellation is outside Exception
                    if failure is None:
                        failure = exc
        if failure is not None:
            raise failure

    def _record_stream_failure(
        self,
        request: GenerationRequest,
        consent: ConsentGrant | None,
        failure: BaseException,
        *,
        canonical: str,
        request_hash: str,
        started_at: str,
        retain: bool,
        retained_chunks: list[str] | None,
        stream_started: bool,
        audit_run_id: str | None = None,
    ) -> ProviderError:
        if not isinstance(failure, Exception) and not is_provider_cancellation(failure):
            raise failure
        error = normalize_provider_error(
            failure,
            request.provider_id,
            streaming=True,
            stream_started=stream_started,
        )
        partial_output = (
            "".join(retained_chunks)
            if error.code != "PROVIDER_CANCELLED" and retained_chunks is not None and stream_started
            else None
        )
        self._record_run(
            request,
            consent,
            request_hash=request_hash,
            started_at=started_at,
            status=(
                "aborted" if stream_started or error.code == "PROVIDER_CANCELLED" else "failed"
            ),
            input_payload=canonical if retain else None,
            output_payload=partial_output,
            error_code=error.code,
            audit_run_id=audit_run_id,
        )
        return error

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
            with self.execution.lease(
                self._execution_key(request),
                max_concurrency=request.execution.max_concurrency,
                max_pending=request.execution.max_pending,
                timeout_seconds=request.timeout_seconds,
                cancellation_check=self._check_cancellation,
            ):
                self._check_cancellation()
                iterator = iter(provider.stream(request))
                for chunk in iterator:
                    self._check_cancellation()
                    stream_started = True
                    encoded = chunk.encode("utf-8")
                    response_hasher.update(encoded)
                    if retained_chunks is not None:
                        retained_chunks.append(chunk)
                    yield chunk
                self._check_cancellation()
        except BaseException as exc:  # noqa: BLE001 - cancellation is outside Exception
            failure = exc
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                try:
                    close()
                except BaseException as exc:  # noqa: BLE001 - cancellation is outside Exception
                    if failure is None:
                        failure = exc

        if failure is not None:
            error = self._record_stream_failure(
                request,
                consent,
                failure,
                canonical=canonical,
                request_hash=request_hash,
                started_at=started_at,
                retain=retain,
                retained_chunks=retained_chunks,
                stream_started=stream_started,
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

    def close(self) -> None:
        """Stop queued work, discard in-memory results, and close shared clients."""

        first_failure: BaseException | None = None
        actions = [self.execution.close, self.cache.close]
        close = getattr(self.registry, "close", None)
        if close is not None:
            actions.append(close)
        for action in actions:
            try:
                action()
            except BaseException as exc:  # noqa: BLE001 - later resources must still close
                if first_failure is None:
                    first_failure = exc
        if first_failure is not None:
            raise first_failure
