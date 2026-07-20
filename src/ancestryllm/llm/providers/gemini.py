"""Google Gemini generation adapter."""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

import httpx

from ancestryllm.core.errors import ProviderError, normalize_provider_error
from ancestryllm.llm.contracts import GenerationRequest, GenerationResult, ProviderCapabilities
from ancestryllm.llm.validation import validate_structured_output


class GeminiProvider:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ProviderError("PROVIDER_KEY_MISSING", "No Gemini key is configured.")
        self.api_key = api_key

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id="gemini", remote=True, structured_output=True, streaming=True
        )

    def _client_and_types(self, timeout_seconds: float) -> tuple[Any, Any]:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ProviderError("PROVIDER_NOT_INSTALLED", "Install ancestryllm[gemini].") from exc
        http_options = types.HttpOptions(
            timeout=math.ceil(timeout_seconds * 1_000),
            client_args={"timeout": httpx.Timeout(timeout_seconds)},
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        return genai.Client(api_key=self.api_key, http_options=http_options), types

    def _config(self, request: GenerationRequest, types: Any) -> Any:
        return types.GenerateContentConfig(
            system_instruction="\n".join(
                message.content for message in request.messages if message.role == "system"
            )
            or None,
            response_mime_type="application/json" if request.response_schema else "text/plain",
            response_json_schema=request.response_schema,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
            http_options=types.HttpOptions(
                timeout=math.ceil(request.timeout_seconds * 1_000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        contents = "\n".join(
            message.content for message in request.messages if message.role != "system"
        )
        try:
            client, types = self._client_and_types(request.timeout_seconds)
            with client:
                response = client.models.generate_content(
                    model=request.model,
                    contents=contents,
                    config=self._config(request, types),
                )
                text = response.text or ""
        except ProviderError:
            raise
        except Exception as exc:
            raise normalize_provider_error(exc, "gemini") from exc
        usage = getattr(response, "usage_metadata", None)
        return GenerationResult(
            provider_id="gemini",
            model=request.model,
            text=text,
            parsed=validate_structured_output(text, request.response_schema),
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        stream_started = False
        try:
            client, types = self._client_and_types(request.timeout_seconds)
            with client:
                for chunk in client.models.generate_content_stream(
                    model=request.model,
                    contents="\n".join(
                        message.content for message in request.messages if message.role != "system"
                    ),
                    config=self._config(request, types),
                ):
                    if chunk.text:
                        stream_started = True
                        yield chunk.text
        except ProviderError:
            raise
        except Exception as exc:
            raise normalize_provider_error(
                exc,
                "gemini",
                streaming=True,
                stream_started=stream_started,
            ) from exc
