"""Local Ollama generation adapter."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

import httpx

from ancestryllm.core.errors import ProviderError, normalize_provider_error
from ancestryllm.llm.contracts import GenerationRequest, GenerationResult, ProviderCapabilities
from ancestryllm.llm.policy import endpoint_is_loopback, validate_endpoint
from ancestryllm.llm.validation import validate_structured_output

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


class OllamaProvider:
    """Adapt local Ollama generation and streaming behind the provider contract."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        validate_endpoint("ollama", base_url)
        self.base_url = base_url
        self._clients: dict[float, Any] = {}
        self._lock = threading.RLock()
        self._closed = False

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities exposed by the ollama provider."""
        return ProviderCapabilities(
            provider_id="ollama",
            remote=not endpoint_is_loopback(self.base_url),
            structured_output=True,
            streaming=True,
        )

    def _client(self, timeout_seconds: float) -> Any:
        with self._lock:
            if self._closed:
                raise ProviderError(
                    "PROVIDER_SERVICE_CLOSED",
                    "The Ollama provider is shutting down.",
                )
            client = self._clients.get(timeout_seconds)
            if client is not None:
                return client
            try:
                from ollama import Client
            except ImportError as exc:
                raise ProviderError(
                    "PROVIDER_NOT_INSTALLED", "Install ancestryllm[ollama]."
                ) from exc
            client = Client(host=self.base_url, timeout=httpx.Timeout(timeout_seconds))
            self._clients[timeout_seconds] = client
            return client

    @staticmethod
    def _options(request: GenerationRequest) -> dict[str, int | float]:
        options: dict[str, int | float] = {
            "temperature": request.temperature,
            "num_predict": request.max_output_tokens,
        }
        for name in ("num_ctx", "num_batch", "num_thread", "num_gpu", "seed"):
            value = getattr(request.execution, name)
            if value is not None:
                options[name] = value
        return options

    @staticmethod
    def _keep_alive(request: GenerationRequest) -> dict[str, int | str]:
        if request.execution.keep_alive is None:
            return {}
        return {"keep_alive": request.execution.keep_alive}

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate a response through the ollama provider."""
        try:
            client = self._client(request.timeout_seconds)
            response = client.chat(
                model=request.model,
                messages=[message.model_dump() for message in request.messages],
                format=request.response_schema or "",
                options=self._options(request),
                stream=False,
                **self._keep_alive(request),
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
        """Stream response chunks through the ollama provider."""
        stream_started = False
        iterator: Iterator[Any] | None = None
        try:
            client = self._client(request.timeout_seconds)
            iterator = iter(
                client.chat(
                    model=request.model,
                    messages=[message.model_dump() for message in request.messages],
                    format=request.response_schema or "",
                    options=self._options(request),
                    stream=True,
                    **self._keep_alive(request),
                )
            )
            for chunk in iterator:
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
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - SDK iterators own close behavior
                    logger.warning("Ollama stream close failed: %s", type(exc).__name__)

    def close(self) -> None:
        """Release resources owned by the ollama provider."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = tuple(self._clients.values())
            self._clients.clear()
        for client in clients:
            close = getattr(client, "close", None)
            if close is not None:
                close()
                continue
            exit_context = getattr(client, "__exit__", None)
            if exit_context is not None:
                exit_context(None, None, None)
