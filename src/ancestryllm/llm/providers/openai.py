"""OpenAI-compatible provider adapter used by OpenAI and OpenRouter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from ancestryllm.core.errors import ProviderError, normalize_provider_error
from ancestryllm.llm.contracts import GenerationRequest, GenerationResult, ProviderCapabilities
from ancestryllm.llm.policy import validate_endpoint
from ancestryllm.llm.validation import validate_structured_output


class OpenAIProvider:
    def __init__(
        self,
        api_key: str,
        *,
        provider_id: str = "openai",
        base_url: str = "https://api.openai.com/v1",
        zero_data_retention: bool = False,
    ) -> None:
        if not api_key:
            raise ProviderError("PROVIDER_KEY_MISSING", f"No key is configured for {provider_id}.")
        validate_endpoint(provider_id, base_url)
        self.api_key = api_key
        self.provider_id = provider_id
        self.base_url = base_url
        self.zero_data_retention = zero_data_retention

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            remote=True,
            structured_output=True,
            streaming=True,
            retention_known=self.provider_id == "openrouter" and self.zero_data_retention,
            zero_data_retention=self.zero_data_retention,
        )

    def _client(self, timeout_seconds: float) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("PROVIDER_NOT_INSTALLED", "Install ancestryllm[openai].") from exc
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds),
            max_retries=0,
        )

    def _response_format(self, request: GenerationRequest) -> dict[str, object] | None:
        if request.response_schema is None:
            return None
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "ancestryllm_response",
                "strict": True,
                "schema": request.response_schema,
            },
        }

    def generate(self, request: GenerationRequest) -> GenerationResult:
        kwargs: dict[str, object] = {
            "model": request.model,
            "messages": [message.model_dump() for message in request.messages],
            "max_completion_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "timeout": httpx.Timeout(request.timeout_seconds),
        }
        response_format = self._response_format(request)
        if response_format:
            kwargs["response_format"] = response_format
        try:
            with self._client(request.timeout_seconds) as client:
                response = client.chat.completions.create(**kwargs)
                text = response.choices[0].message.content or ""
        except ProviderError:
            raise
        except Exception as exc:
            raise normalize_provider_error(exc, self.provider_id) from exc
        usage = response.usage
        return GenerationResult(
            provider_id=self.provider_id,
            model=request.model,
            text=text,
            parsed=validate_structured_output(text, request.response_schema),
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            request_id=getattr(response, "id", None),
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        stream_started = False
        try:
            with self._client(request.timeout_seconds) as client:
                stream = client.chat.completions.create(
                    model=request.model,
                    messages=[message.model_dump() for message in request.messages],
                    max_completion_tokens=request.max_output_tokens,
                    temperature=request.temperature,
                    response_format=self._response_format(request),
                    stream=True,
                    timeout=httpx.Timeout(request.timeout_seconds),
                )
                with stream:
                    for chunk in stream:
                        content = chunk.choices[0].delta.content
                        if content:
                            stream_started = True
                            yield content
        except ProviderError:
            raise
        except Exception as exc:
            raise normalize_provider_error(
                exc,
                self.provider_id,
                streaming=True,
                stream_started=stream_started,
            ) from exc
