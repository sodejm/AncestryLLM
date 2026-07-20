from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_local_llm.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_local_llm", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = benchmark
_SPEC.loader.exec_module(benchmark)


def test_benchmark_is_dry_run_without_explicit_execution(capsys) -> None:
    assert benchmark.main(["--model", "fictional-model"]) == 0
    assert "pass --execute" in capsys.readouterr().out


def test_benchmark_skips_when_ollama_is_unavailable(monkeypatch, capsys) -> None:
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("Ollama is unavailable at http://127.0.0.1:11434: ConnectError")

    monkeypatch.setattr(benchmark, "run", unavailable)

    assert benchmark.main(["--model", "fictional-model", "--execute"]) == 2
    assert "skip: Ollama is unavailable" in capsys.readouterr().err


def test_benchmark_skips_when_model_is_not_installed(monkeypatch, capsys) -> None:
    def missing(*_args, **_kwargs):
        raise LookupError("model is not installed: fictional-model")

    monkeypatch.setattr(benchmark, "run", missing)

    assert benchmark.main(["--model", "fictional-model", "--execute"]) == 2
    assert "skip: model is not installed: fictional-model" in capsys.readouterr().err


def test_repository_output_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="outside the repository"):
        benchmark._safe_output_path(_SCRIPT.parents[1] / "benchmark.json")
    assert benchmark._safe_output_path(tmp_path / "benchmark.json") == tmp_path / "benchmark.json"


def test_metrics_avoid_payloads_and_calculate_rates() -> None:
    metrics = benchmark.RequestMetrics(
        phase="cold",
        status="completed",
        wall_seconds=1.0,
        ttft_seconds=0.1,
        completion_tokens=10,
        completion_tokens_per_second=20.0,
        prompt_tokens=4,
        prompt_tokens_per_second=40.0,
        ollama_total_seconds=0.8,
        ollama_load_seconds=0.2,
        queue_delay_seconds=None,
        cancelled=False,
    )
    assert "prompt_text" not in benchmark.asdict(metrics)
    assert benchmark._rate(10, 500_000_000) == 20.0
    assert benchmark._seconds(250_000_000) == 0.25


def test_stream_metrics_measure_ttft_and_token_rates_without_response_text(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    '{"response":"fictional answer"}',
                    (
                        '{"done":true,"eval_count":8,"eval_duration":400000000,'
                        '"prompt_eval_count":4,"prompt_eval_duration":100000000,'
                        '"total_duration":700000000,"load_duration":200000000}'
                    ),
                ]
            )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def stream(self, *_args, **_kwargs) -> Response:
            return Response()

    monkeypatch.setattr(benchmark.httpx, "Client", Client)

    metrics = benchmark._measure_request(
        model="fictional-model",
        endpoint="http://127.0.0.1:11434",
        prompt="fictional prompt",
        profile_options={"num_ctx": 2048},
        timeout_seconds=1.0,
        phase="cold",
    )

    assert metrics.status == "completed"
    assert metrics.ttft_seconds is not None
    assert metrics.completion_tokens_per_second == 20.0
    assert metrics.prompt_tokens_per_second == 40.0
    assert "fictional answer" not in benchmark.asdict(metrics).values()


def test_timeout_is_reported_as_an_aggregate_request_status(monkeypatch) -> None:
    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def stream(self, *_args, **_kwargs):
            raise httpx.ReadTimeout("fictional timeout")

    monkeypatch.setattr(benchmark.httpx, "Client", Client)

    metrics = benchmark._measure_request(
        model="fictional-model",
        endpoint="http://127.0.0.1:11434",
        prompt="fictional prompt",
        profile_options={"num_ctx": 2048},
        timeout_seconds=1.0,
        phase="warm-1",
    )

    assert metrics.status == "timeout"
    assert metrics.completion_tokens is None
