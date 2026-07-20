"""Local Ollama generation adapter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from ancestryllm.core.errors import ProviderError, normalize_provider_error
from ancestryllm.llm.contracts import GenerationRequest, GenerationResult, ProviderCapabilities
from ancestryllm.llm.policy import validate_endpoint
from ancestryllm.llm.validation import validate_structured_output


class OllamaProvider:
    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        validate_endpoint("ollama", base_url)
        self.base_url = base_url

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id="ollama", remote=False, structured_output=True, streaming=True
        )

    def _client(self, timeout_seconds: float) -> Any:
        try:
            from ollama import Client
        except ImportError as exc:
            raise ProviderError("PROVIDER_NOT_INSTALLED", "Install ancestryllm[ollama].") from exc
        return Client(host=self.base_url, timeout=httpx.Timeout(timeout_seconds))

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            with self._client(request.timeout_seconds) as client:
                response = client.chat(
                    model=request.model,
                    messages=[message.model_dump() for message in request.messages],
                    format=request.response_schema or "",
                    options={
                        "temperature": request.temperature,
                        "num_predict": request.max_output_tokens,
                    },
                    stream=False,
                )
                text = str(response["message"]["content"])
        except ProviderError:
            raise
        except Exception as exc:
            raise normalize_provider_error(exc, "ollama") from exc
        return GenerationResult(
            provider_id="ollama",
            model=request.model,
            text=text,
            parsed=validate_structured_output(text, request.response_schema),
            input_tokens=response.get("prompt_eval_count"),
            output_tokens=response.get("eval_count"),
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        stream_started = False
        try:
            with self._client(request.timeout_seconds) as client:
                for chunk in client.chat(
                    model=request.model,
                    messages=[message.model_dump() for message in request.messages],
                    format=request.response_schema or "",
                    options={
                        "temperature": request.temperature,
                        "num_predict": request.max_output_tokens,
                    },
                    stream=True,
                ):
                    text = str(chunk["message"]["content"])
                    if text:
                        stream_started = True
                        yield text
        except ProviderError:
            raise
        except Exception as exc:
            raise normalize_provider_error(
                exc,
                "ollama",
                streaming=True,
                stream_started=stream_started,
            ) from exc
