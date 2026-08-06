"""Tests for committed core contract characterization artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import characterize_core_contracts as characterization


def _report(
    *,
    cli_ms: float = 50.0,
    merge_ms: float = 600.0,
    cli_rss: int | None = 100_000,
    merge_rss: int | None = 200_000,
    dependency_digest: str = "dependencies-a",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "baseline_id": "0.3.0-core-contracts",
        "fixture_digest": "fixtures",
        "semantic_digest": "semantics",
        "dependencies": {"digest": dependency_digest},
        "performance_policy": {
            "runs": 7,
            "warm_iterations_per_run": 60,
            "cold_start_regression_percent": 10.0,
            "cold_start_regression_floor_ms": 100.0,
            "warm_regression_percent": 10.0,
            "warm_regression_minimum_ms": 500.0,
            "peak_rss_regression_percent": 10.0,
        },
        "performance": {
            "cli_cold_start": {
                "median_elapsed_ms": cli_ms,
                "median_peak_rss_bytes": cli_rss,
            },
            "offline_gedcom_merge": {
                "median_elapsed_ms": merge_ms,
                "median_peak_rss_bytes": merge_rss,
            },
        },
    }


def test_committed_characterization_inventory_is_fixed_and_resolvable() -> None:
    manifest = characterization.load_manifest()

    counts = characterization.verify_inventory(manifest)
    snapshot = characterization.dependency_snapshot()

    assert counts == {
        "fixtures": 13,
        "semantic_groups": 5,
        "test_nodes": 51,
        "public_facades": 10,
    }
    assert snapshot["digest"] == characterization._canonical_digest(
        {
            "module_graph": snapshot["module_graph"],
            "declared_runtime_dependencies": snapshot["declared_runtime_dependencies"],
            "lockfile_sha256": snapshot["lockfile_sha256"],
        }
    )
    assert set(manifest["public_facades"]) <= set(snapshot["module_graph"])


def test_semantic_runner_never_copies_captured_payloads_into_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = characterization.load_manifest()
    expected_hashes = characterization.fixture_hashes(manifest)
    private_payload = b"FICTIONAL_PERSON_PAYLOAD_MUST_NOT_ESCAPE"

    def completed_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=private_payload, stderr=private_payload)

    monkeypatch.setattr(characterization.subprocess, "run", completed_run)

    results = characterization._run_semantic_groups(manifest, expected_hashes, tmp_path)

    assert all(result["status"] == "passed" for result in results)
    assert private_payload.decode() not in json.dumps(results)


def test_semantic_runner_failure_is_payload_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = characterization.load_manifest()
    expected_hashes = characterization.fixture_hashes(manifest)
    private_payload = b"FICTIONAL_PERSON_PAYLOAD_MUST_NOT_ESCAPE"

    def failed_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=7, stdout=private_payload, stderr=private_payload)

    monkeypatch.setattr(characterization.subprocess, "run", failed_run)

    with pytest.raises(characterization.CharacterizationError) as raised:
        characterization._run_semantic_groups(manifest, expected_hashes, tmp_path)

    assert private_payload.decode() not in str(raised.value)
    assert "exit code 7" in str(raised.value)


def test_offline_merge_measurement_is_network_blocked_and_deterministic(tmp_path: Path) -> None:
    result = characterization._measure_offline_merge(2, tmp_path)

    assert result["iterations"] == 2
    assert result["people_read"] == 6
    assert result["people_written"] == 4
    assert len(result["result_digest"]) == 64


def test_capture_checks_source_digest_even_when_execution_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = characterization.load_manifest()
    digests = iter(("source-before", "source-after"))

    monkeypatch.setattr(characterization, "verify_inventory", lambda _manifest: {})
    monkeypatch.setattr(
        characterization,
        "fixture_hashes",
        lambda _manifest: dict(manifest["fixture_sha256"]),
    )
    monkeypatch.setattr(characterization, "_source_tree_digest", lambda: next(digests))

    def failed_groups(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise characterization.CharacterizationError("semantic execution failed")

    monkeypatch.setattr(characterization, "_run_semantic_groups", failed_groups)

    with pytest.raises(characterization.CharacterizationError) as raised:
        characterization.capture_report(manifest)

    assert str(raised.value) == "characterization mutated the application source tree"


def test_comparison_applies_tracker_thresholds_and_treats_dependencies_as_information() -> None:
    baseline = _report()
    within_threshold = _report(
        cli_ms=150.0,
        merge_ms=660.0,
        cli_rss=110_000,
        merge_rss=220_000,
        dependency_digest="dependencies-b",
    )

    comparison = characterization.compare_reports(baseline, within_threshold)

    assert comparison["passed"] is True
    assert comparison["dependency_change"]["changed"] is True
    assert comparison["violations"] == []
    assert all(item["passed"] for item in comparison["performance"])


@pytest.mark.parametrize(
    ("candidate", "expected_gate"),
    [
        (_report(cli_ms=150.001), "cli_cold_start.median_elapsed_ms"),
        (_report(merge_ms=660.001), "offline_gedcom_merge.median_elapsed_ms"),
        (_report(cli_rss=110_001), "cli_cold_start.median_peak_rss_bytes"),
        (_report(merge_rss=None), "offline_gedcom_merge.median_peak_rss_bytes"),
    ],
)
def test_comparison_fails_closed_above_each_gate(
    candidate: dict[str, object],
    expected_gate: str,
) -> None:
    comparison = characterization.compare_reports(_report(), candidate)

    assert comparison["passed"] is False
    assert expected_gate in {item["gate"] for item in comparison["violations"]}


def test_warm_timing_gate_only_applies_at_the_documented_minimum() -> None:
    baseline = _report(merge_ms=499.999)
    candidate = _report(merge_ms=900.0)

    comparison = characterization.compare_reports(baseline, candidate)
    merge_timing = next(
        item
        for item in comparison["performance"]
        if item["gate"] == "offline_gedcom_merge.median_elapsed_ms"
    )

    assert merge_timing["applies"] is False
    assert merge_timing["passed"] is True
    assert comparison["passed"] is True


def test_reports_cannot_be_written_inside_the_repository() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        characterization._external_output_path(
            str(characterization.ROOT / "characterization-report.json")
        )


def test_json_reports_are_written_atomically_outside_the_repository(tmp_path: Path) -> None:
    output = tmp_path / "nested/report.json"

    characterization._write_json(output, {"passed": True})

    assert json.loads(output.read_text(encoding="utf-8")) == {"passed": True}
    assert list(output.parent.glob("*.tmp")) == []
