from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
