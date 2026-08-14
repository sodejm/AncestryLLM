"""Measure cold CLI import time and report eagerly loaded provider SDK modules."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys

SCHEMA_VERSION = 1
BENCHMARK_NAME = "ancestryllm.cli.import"
PROVIDER_SDK_MODULES = ("anthropic", "google.genai", "ollama", "openai")
_CHILD_PROGRAM = f"""
import json
import sys
import time

started = time.perf_counter_ns()
import ancestryllm.cli  # noqa: F401
elapsed_ns = time.perf_counter_ns() - started
provider_roots = {PROVIDER_SDK_MODULES!r}
loaded = sorted(
    module_name
    for module_name in sys.modules
    if any(
        module_name == root or module_name.startswith(root + ".")
        for root in provider_roots
    )
)
print(json.dumps({{"elapsed_ns": elapsed_ns, "provider_modules": loaded}}, sort_keys=True))
"""


def _positive_iterations(value: str) -> int:
    iterations = int(value)
    if not 3 <= iterations <= 50:
        raise argparse.ArgumentTypeError("iterations must be between 3 and 50")
    return iterations


def _measure_once() -> tuple[float, tuple[str, ...]]:
    completed = subprocess.run(  # noqa: S603 - the reviewed interpreter runs fixed source
        [sys.executable, "-c", _CHILD_PROGRAM],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    elapsed_ms = int(result["elapsed_ns"]) / 1_000_000
    provider_modules = tuple(str(name) for name in result["provider_modules"])
    return elapsed_ms, provider_modules


def benchmark(iterations: int) -> dict[str, object]:
    """Run isolated imports and return a sanitized schema-v1 measurement."""

    samples: list[float] = []
    loaded_provider_modules: set[str] = set()
    for _ in range(iterations):
        elapsed_ms, provider_modules = _measure_once()
        samples.append(elapsed_ms)
        loaded_provider_modules.update(provider_modules)

    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": BENCHMARK_NAME,
        "python_version": platform.python_version(),
        "iterations": iterations,
        "minimum_ms": round(min(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "maximum_ms": round(max(samples), 3),
        "provider_modules_loaded": sorted(loaded_provider_modules),
    }


def main() -> int:
    """Run the benchmark CLI import command and return its exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=_positive_iterations, default=7)
    arguments = parser.parse_args()
    print(json.dumps(benchmark(arguments.iterations), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
