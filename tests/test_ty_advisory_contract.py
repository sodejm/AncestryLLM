"""Contracts for the 0.6 ty advisory evaluation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_parity_harness_covers_language_and_pydantic_diagnostics() -> None:
    harness = (ROOT / "scripts/check_typechecker_parity.py").read_text(encoding="utf-8")

    for fixture in (
        "tests/fixtures/typecheck/language_error.py",
        "tests/fixtures/typecheck/pydantic_error.py",
    ):
        assert (ROOT / fixture).is_file()
        assert fixture in harness

    assert '("mypy",)' in harness
    assert '("ty", "check")' in harness
    assert "TYPEPARITY_DIAGNOSTIC_MISSED" in harness
    assert "TYPEPARITY_CHECKER_FAILED" in harness


def test_evaluation_record_names_every_required_decision_input() -> None:
    report = (ROOT / "docs/reference/TY_ADVISORY_EVALUATION.md").read_text(encoding="utf-8")

    for required_text in (
        "ty 0.0.69",
        "Genuine code defect",
        "Pydantic/model behavior gap",
        "Missing third-party typing",
        "ty defect or unsupported feature",
        "Suppressions",
        "Execution time",
        "Python 3.12",
        "quality-profile count is 58",
        "Full setup",
        "**53**",
        "mypy remains authoritative",
    ):
        assert required_text in report
