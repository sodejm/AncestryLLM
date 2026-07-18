"""Anthropic generation adapter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ancestryllm.core.errors import ProviderError
from ancestryllm.llm.contracts import GenerationRequest, GenerationResult, ProviderCapabilities
from ancestryllm.llm.validation import validate_structured_output


class AnthropicProvider:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ProviderError("PROVIDER_KEY_MISSING", "No Anthropic key is configured.")
        self.api_key = api_key

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id="anthropic", remote=True, structured_output=False, streaming=True
        )

    def _client(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(
                "PROVIDER_NOT_INSTALLED", "Install ancestryllm[anthropic]."
            ) from exc
        return anthropic.Anthropic(api_key=self.api_key)

    def _messages(self, request: GenerationRequest) -> tuple[str, list[dict[str, str]]]:
        systems = [message.content for message in request.messages if message.role == "system"]
        messages = [
            message.model_dump() for message in request.messages if message.role != "system"
        ]
        if request.response_schema:
            messages.append(
                {
                    "role": "user",
                    "content": "Return only JSON matching this schema: "
                    + str(request.response_schema),
                }
            )
        return "\n".join(systems), messages

    def generate(self, request: GenerationRequest) -> GenerationResult:
        system, messages = self._messages(request)
        try:
            response = self._client().messages.create(
                model=request.model,
                system=system,
                messages=messages,
                max_tokens=request.max_output_tokens,
                temperature=request.temperature,
            )
            text = "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_REQUEST_FAILED",
                "The Anthropic request failed.",
                details={"error_type": type(exc).__name__},
            ) from exc
        return GenerationResult(
            provider_id="anthropic",
            model=request.model,
            text=text,
            parsed=validate_structured_output(text, request.response_schema),
            input_tokens=getattr(response.usage, "input_tokens", None),
            output_tokens=getattr(response.usage, "output_tokens", None),
            request_id=getattr(response, "id", None),
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        system, messages = self._messages(request)
        try:
            with self._client().messages.stream(
                model=request.model,
                system=system,
                messages=messages,
                max_tokens=request.max_output_tokens,
            ) as stream:
                yield from stream.text_stream
        except Exception as exc:
            raise ProviderError("PROVIDER_REQUEST_FAILED", "The Anthropic stream failed.") from exc
