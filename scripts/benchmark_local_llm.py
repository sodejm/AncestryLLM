#!/usr/bin/env python3
"""Run an explicit, fictional-data benchmark against an already installed Ollama model.

The script never downloads, starts, reconfigures, or unloads models.  It only
contacts an endpoint when ``--execute`` is supplied and writes aggregate JSON
without retaining the prompt or response text.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

_PROMPT = (
    "Fictional genealogy task: return exactly one JSON object with keys "
    "duplicate (boolean) and reason (string) for Ada Example and Ada Exempler."
)


@dataclass(frozen=True)
class BenchmarkResult:
    model: str
    endpoint: str
    profile: str
    elapsed_seconds: float
    response_bytes: int
    peak_rss_kib: int


def _endpoint(value: str) -> str:
    return value.rstrip("/")


def _installed_models(client: httpx.Client, endpoint: str) -> set[str]:
    response = client.get(f"{endpoint}/api/tags")
    response.raise_for_status()
    payload = response.json()
    return {str(item["name"]) for item in payload.get("models", []) if "name" in item}


def run(model: str, endpoint: str, profile: str, timeout_seconds: float) -> BenchmarkResult:
    """Benchmark one already-installed model and return payload-free metrics."""
    with httpx.Client(timeout=timeout_seconds) as client:
        try:
            installed = _installed_models(client, endpoint)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Ollama is unavailable at {endpoint}: {type(exc).__name__}"
            ) from exc
        if model not in installed:
            raise LookupError(f"model is not installed: {model}")
        started = time.monotonic()
        response = client.post(
            f"{endpoint}/api/generate",
            json={"model": model, "prompt": _PROMPT, "stream": False},
        )
        response.raise_for_status()
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB. Normalize the portable report.
    peak_rss_kib = int(peak_rss / 1024) if sys.platform == "darwin" else peak_rss
    return BenchmarkResult(
        model=model,
        endpoint=endpoint,
        profile=profile,
        elapsed_seconds=round(time.monotonic() - started, 6),
        response_bytes=len(response.content),
        peak_rss_kib=peak_rss_kib,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="already-installed Ollama model name")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--profile", choices=("low-memory", "balanced"), default="low-memory")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--execute", action="store_true", help="allow the one benchmark request")
    parser.add_argument("--output", type=Path, help="write aggregate JSON metrics")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    endpoint = _endpoint(args.endpoint)
    if not args.execute:
        print("dry-run: pass --execute to contact an already-running Ollama endpoint")
        return 0
    try:
        result = run(args.model, endpoint, args.profile, args.timeout_seconds)
    except (LookupError, RuntimeError, httpx.HTTPStatusError) as exc:
        print(f"skip: {exc}", file=sys.stderr)
        return 2
    payload = asdict(result)
    if args.output:
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
