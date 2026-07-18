"""Local Ollama generation adapter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ancestryllm.core.errors import ProviderError
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

    def _client(self) -> Any:
        try:
            from ollama import Client
        except ImportError as exc:
            raise ProviderError("PROVIDER_NOT_INSTALLED", "Install ancestryllm[ollama].") from exc
        return Client(host=self.base_url)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            response = self._client().chat(
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
            raise ProviderError(
                "PROVIDER_REQUEST_FAILED",
                "The local Ollama request failed.",
                details={"error_type": type(exc).__name__},
            ) from exc
        return GenerationResult(
            provider_id="ollama",
            model=request.model,
            text=text,
            parsed=validate_structured_output(text, request.response_schema),
            input_tokens=response.get("prompt_eval_count"),
            output_tokens=response.get("eval_count"),
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        try:
            for chunk in self._client().chat(
                model=request.model,
                messages=[message.model_dump() for message in request.messages],
                stream=True,
            ):
                yield str(chunk["message"]["content"])
        except Exception as exc:
            raise ProviderError(
                "PROVIDER_REQUEST_FAILED", "The local Ollama stream failed."
            ) from exc
