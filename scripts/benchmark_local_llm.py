#!/usr/bin/env python3
"""Benchmark an already-installed local Ollama model with fictional workloads.

Nothing contacts Ollama unless ``--execute`` is supplied.  The benchmark never
downloads a model; unloading, cancellation, and queued requests are also
explicit opt-in actions.  JSON contains aggregate metadata and timing only--it
never retains prompt text, generated text, or an endpoint URL.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

_WORKLOADS = {
    "duplicate-adjudication": (
        "Fictional genealogy task: return exactly one JSON object with keys "
        "duplicate (boolean) and reason (string) for Ada Example and Ada Exempler."
    ),
}
_PROFILE_OPTIONS: dict[str, dict[str, int]] = {
    "low-memory": {"num_ctx": 2048},
    "balanced": {"num_ctx": 4096},
}


@dataclass(frozen=True)
class ModelMetadata:
    name: str
    digest: str | None
    size_bytes: int | None
    family: str | None
    parameter_size: str | None
    quantization_level: str | None
    memory_bytes: int | None


@dataclass(frozen=True)
class RequestMetrics:
    phase: str
    status: str
    wall_seconds: float
    ttft_seconds: float | None
    completion_tokens: int | None
    completion_tokens_per_second: float | None
    prompt_tokens: int | None
    prompt_tokens_per_second: float | None
    ollama_total_seconds: float | None
    ollama_load_seconds: float | None
    queue_delay_seconds: float | None
    cancelled: bool


@dataclass(frozen=True)
class BenchmarkResult:
    model: ModelMetadata
    profile: str
    profile_options: dict[str, int]
    workload: str
    cold_start_mode: str
    queue_depth: int
    timeout_seconds: float
    unload_after_requested: bool
    unload_after_succeeded: bool | None
    benchmark_process_peak_rss_kib: int | None
    requests: tuple[RequestMetrics, ...]


def _endpoint(value: str) -> str:
    return value.rstrip("/")


def _seconds(nanoseconds: object) -> float | None:
    if not isinstance(nanoseconds, int | float):
        return None
    return round(float(nanoseconds) / 1_000_000_000, 6)


def _rate(tokens: object, duration_nanoseconds: object) -> float | None:
    if not isinstance(tokens, int | float) or not isinstance(duration_nanoseconds, int | float):
        return None
    if not duration_nanoseconds:
        return None
    return round(float(tokens) / (float(duration_nanoseconds) / 1_000_000_000), 3)


def _model_metadata(item: Mapping[str, Any]) -> ModelMetadata:
    details = item.get("details")
    details = details if isinstance(details, Mapping) else {}
    return ModelMetadata(
        name=str(item["name"]),
        digest=_string_or_none(item.get("digest")),
        size_bytes=_int_or_none(item.get("size")),
        family=_string_or_none(details.get("family")),
        parameter_size=_string_or_none(details.get("parameter_size")),
        quantization_level=_string_or_none(details.get("quantization_level")),
        memory_bytes=None,
    )


def _string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _installed_models(client: httpx.Client, endpoint: str) -> dict[str, ModelMetadata]:
    response = client.get(f"{endpoint}/api/tags")
    response.raise_for_status()
    payload = response.json()
    models = payload.get("models", [])
    return {
        metadata.name: metadata
        for item in models
        if isinstance(item, Mapping) and "name" in item
        for metadata in (_model_metadata(item),)
    }


def _resident_memory_bytes(client: httpx.Client, endpoint: str, model: str) -> int | None:
    """Return Ollama's model memory estimate when the endpoint exposes it."""
    try:
        response = client.get(f"{endpoint}/api/ps")
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    for item in response.json().get("models", []):
        if isinstance(item, Mapping) and item.get("name") == model:
            return _int_or_none(item.get("size"))
    return None


def _unload(client: httpx.Client, endpoint: str, model: str) -> bool:
    response = client.post(f"{endpoint}/api/generate", json={"model": model, "keep_alive": 0})
    response.raise_for_status()
    return True


def _measure_request(
    *,
    model: str,
    endpoint: str,
    prompt: str,
    profile_options: Mapping[str, int],
    timeout_seconds: float,
    phase: str,
    queued_at: float | None = None,
    cancel_after_first_token: bool = False,
) -> RequestMetrics:
    started = time.monotonic()
    first_token_at: float | None = None
    final_payload: Mapping[str, Any] | None = None
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            with client.stream(
                "POST",
                f"{endpoint}/api/generate",
                json={"model": model, "prompt": prompt, "stream": True, "options": profile_options},
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    payload = json.loads(line)
                    if not isinstance(payload, Mapping):
                        continue
                    if payload.get("response") and first_token_at is None:
                        first_token_at = time.monotonic()
                        if cancel_after_first_token:
                            return RequestMetrics(
                                phase=phase,
                                status="cancelled",
                                wall_seconds=round(first_token_at - started, 6),
                                ttft_seconds=round(first_token_at - started, 6),
                                completion_tokens=None,
                                completion_tokens_per_second=None,
                                prompt_tokens=None,
                                prompt_tokens_per_second=None,
                                ollama_total_seconds=None,
                                ollama_load_seconds=None,
                                queue_delay_seconds=_queue_delay(queued_at, started),
                                cancelled=True,
                            )
                    if payload.get("done") is True:
                        final_payload = payload
                        break
    except httpx.TimeoutException:
        return RequestMetrics(
            phase=phase,
            status="timeout",
            wall_seconds=round(time.monotonic() - started, 6),
            ttft_seconds=None,
            completion_tokens=None,
            completion_tokens_per_second=None,
            prompt_tokens=None,
            prompt_tokens_per_second=None,
            ollama_total_seconds=None,
            ollama_load_seconds=None,
            queue_delay_seconds=_queue_delay(queued_at, started),
            cancelled=False,
        )
    if final_payload is None:
        raise RuntimeError("Ollama ended the stream without completion metadata")
    ended = time.monotonic()
    return RequestMetrics(
        phase=phase,
        status="completed",
        wall_seconds=round(ended - started, 6),
        ttft_seconds=round(first_token_at - started, 6) if first_token_at else None,
        completion_tokens=_int_or_none(final_payload.get("eval_count")),
        completion_tokens_per_second=_rate(
            final_payload.get("eval_count"), final_payload.get("eval_duration")
        ),
        prompt_tokens=_int_or_none(final_payload.get("prompt_eval_count")),
        prompt_tokens_per_second=_rate(
            final_payload.get("prompt_eval_count"), final_payload.get("prompt_eval_duration")
        ),
        ollama_total_seconds=_seconds(final_payload.get("total_duration")),
        ollama_load_seconds=_seconds(final_payload.get("load_duration")),
        queue_delay_seconds=_queue_delay(queued_at, started),
        cancelled=False,
    )


def _queue_delay(queued_at: float | None, started: float) -> float | None:
    return round(started - queued_at, 6) if queued_at is not None else None


def _peak_rss_kib() -> int | None:
    try:
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (AttributeError, OSError):
        return None
    return int(peak_rss / 1024) if sys.platform == "darwin" else int(peak_rss)


def run(
    model: str,
    endpoint: str,
    profile: str,
    timeout_seconds: float,
    *,
    workload: str = "duplicate-adjudication",
    warm_runs: int = 1,
    queue_depth: int = 1,
    cancel_after_first_token: bool = False,
    unload_before: bool = False,
    unload_after: bool = False,
) -> BenchmarkResult:
    """Benchmark one installed model without retaining fictional payload text."""
    prompt = _WORKLOADS[workload]
    profile_options = _PROFILE_OPTIONS[profile]
    with httpx.Client(timeout=timeout_seconds) as client:
        try:
            metadata = _installed_models(client, endpoint).get(model)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Ollama is unavailable at {endpoint}: {type(exc).__name__}"
            ) from exc
        if metadata is None:
            raise LookupError(f"model is not installed: {model}")
        if unload_before:
            _unload(client, endpoint, model)
        metadata = ModelMetadata(
            **{**asdict(metadata), "memory_bytes": _resident_memory_bytes(client, endpoint, model)}
        )

    requests = [
        _measure_request(
            model=model,
            endpoint=endpoint,
            prompt=prompt,
            profile_options=profile_options,
            timeout_seconds=timeout_seconds,
            phase="cold",
            cancel_after_first_token=cancel_after_first_token,
        )
    ]
    for index in range(warm_runs):
        requests.append(
            _measure_request(
                model=model,
                endpoint=endpoint,
                prompt=prompt,
                profile_options=profile_options,
                timeout_seconds=timeout_seconds,
                phase=f"warm-{index + 1}",
                cancel_after_first_token=cancel_after_first_token,
            )
        )
    if queue_depth > 1:
        queued_at = time.monotonic()
        with ThreadPoolExecutor(max_workers=queue_depth) as executor:
            queued = executor.map(
                lambda index: _measure_request(
                    model=model,
                    endpoint=endpoint,
                    prompt=prompt,
                    profile_options=profile_options,
                    timeout_seconds=timeout_seconds,
                    phase=f"queue-{index + 1}",
                    queued_at=queued_at,
                    cancel_after_first_token=cancel_after_first_token,
                ),
                range(queue_depth),
            )
            requests.extend(queued)

    unload_succeeded: bool | None = None
    if unload_after:
        with httpx.Client(timeout=timeout_seconds) as client:
            unload_succeeded = _unload(client, endpoint, model)
    return BenchmarkResult(
        model=metadata,
        profile=profile,
        profile_options=dict(profile_options),
        workload=workload,
        cold_start_mode="unload-requested" if unload_before else "observed-first-request",
        queue_depth=queue_depth,
        timeout_seconds=timeout_seconds,
        unload_after_requested=unload_after,
        unload_after_succeeded=unload_succeeded,
        benchmark_process_peak_rss_kib=_peak_rss_kib(),
        requests=tuple(requests),
    )


def _safe_output_path(output: Path) -> Path:
    resolved = output.resolve()
    repository = Path(__file__).resolve().parents[1]
    try:
        resolved.relative_to(repository)
    except ValueError:
        return resolved
    raise ValueError(
        "--output must be outside the repository to avoid committing benchmark reports"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="already-installed Ollama model name")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--profile", choices=tuple(_PROFILE_OPTIONS), default="low-memory")
    parser.add_argument("--workload", choices=tuple(_WORKLOADS), default="duplicate-adjudication")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--warm-runs", type=int, default=1)
    parser.add_argument("--queue-depth", type=int, default=1)
    parser.add_argument("--cancel-after-first-token", action="store_true")
    parser.add_argument("--unload-before", action="store_true")
    parser.add_argument("--unload-after", action="store_true")
    parser.add_argument("--execute", action="store_true", help="allow benchmark requests to Ollama")
    parser.add_argument("--output", type=Path, help="write aggregate JSON outside the repository")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout_seconds <= 0 or args.warm_runs < 0 or args.queue_depth <= 0:
        raise SystemExit(
            "--timeout-seconds and --queue-depth must be positive; --warm-runs cannot be negative"
        )
    if args.output:
        try:
            output = _safe_output_path(args.output)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    else:
        output = None
    endpoint = _endpoint(args.endpoint)
    if not args.execute:
        print("dry-run: pass --execute to contact an already-running Ollama endpoint")
        return 0
    try:
        result = run(
            args.model,
            endpoint,
            args.profile,
            args.timeout_seconds,
            workload=args.workload,
            warm_runs=args.warm_runs,
            queue_depth=args.queue_depth,
            cancel_after_first_token=args.cancel_after_first_token,
            unload_before=args.unload_before,
            unload_after=args.unload_after,
        )
    except (LookupError, RuntimeError, httpx.HTTPError) as exc:
        print(f"skip: {exc}", file=sys.stderr)
        return 2
    payload = asdict(result)
    if output:
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
