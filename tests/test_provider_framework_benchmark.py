from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_provider_frameworks.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_provider_frameworks", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = benchmark
_SPEC.loader.exec_module(benchmark)


def test_provider_framework_report_is_deterministic_and_network_free() -> None:
    first = benchmark.build_report()
    second = benchmark.build_report()

    assert first == second
    assert first["network"] == "disabled"
    assert first["data"] == "fictional-metadata-only"
    assert {item["framework"] for item in first["frameworks"]} == {
        "native",
        "langchain",
        "litellm",
    }


def test_provider_framework_report_recommends_retaining_native_adapters(capsys) -> None:
    assert benchmark.main([]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["decision"]["recommendation"] == "retain-native-adapters"
    assert {item["recommendation"] for item in report["frameworks"]} == {
        "adopt",
        "defer",
    }


def test_provider_framework_report_can_be_written_without_payloads(tmp_path: Path) -> None:
    output = tmp_path / "provider-frameworks.json"

    assert benchmark.main(["--output", str(output)]) == 0

    content = output.read_text(encoding="utf-8")
    assert "Fictional genealogy" not in content
    assert "Ada Example" not in content
    assert json.loads(content)["version"] == 1
