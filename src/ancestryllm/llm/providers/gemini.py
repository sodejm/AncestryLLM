"""Google Gemini generation adapter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ancestryllm.core.errors import ProviderError
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

    def _client_and_types(self) -> tuple[Any, Any]:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ProviderError("PROVIDER_NOT_INSTALLED", "Install ancestryllm[gemini].") from exc
        return genai.Client(api_key=self.api_key), types

    def generate(self, request: GenerationRequest) -> GenerationResult:
        client, types = self._client_and_types()
        system = "\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        contents = "\n".join(
            message.content for message in request.messages if message.role != "system"
        )
        try:
            response = client.models.generate_content(
                model=request.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system or None,
                    response_mime_type="application/json"
                    if request.response_schema
                    else "text/plain",
                    response_json_schema=request.response_schema,
                    max_output_tokens=request.max_output_tokens,
                    temperature=request.temperature,
                ),
            )
            text = response.text or ""
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_REQUEST_FAILED",
                "The Gemini request failed.",
                details={"error_type": type(exc).__name__},
            ) from exc
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
        client, _types = self._client_and_types()
        try:
            for chunk in client.models.generate_content_stream(
                model=request.model,
                contents="\n".join(message.content for message in request.messages),
            ):
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            raise ProviderError("PROVIDER_REQUEST_FAILED", "The Gemini stream failed.") from exc
