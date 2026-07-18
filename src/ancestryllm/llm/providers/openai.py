"""OpenAI-compatible provider adapter used by OpenAI and OpenRouter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ancestryllm.core.errors import ProviderError
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

    def _client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("PROVIDER_NOT_INSTALLED", "Install ancestryllm[openai].") from exc
        return OpenAI(api_key=self.api_key, base_url=self.base_url)

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
        }
        response_format = self._response_format(request)
        if response_format:
            kwargs["response_format"] = response_format
        try:
            response = self._client().chat.completions.create(**kwargs)
            text = response.choices[0].message.content or ""
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_REQUEST_FAILED",
                f"The {self.provider_id} request failed.",
                details={"error_type": type(exc).__name__},
            ) from exc
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
        try:
            stream = self._client().chat.completions.create(
                model=request.model,
                messages=[message.model_dump() for message in request.messages],
                max_completion_tokens=request.max_output_tokens,
                stream=True,
            )
            for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_REQUEST_FAILED", f"The {self.provider_id} stream failed."
            ) from exc
