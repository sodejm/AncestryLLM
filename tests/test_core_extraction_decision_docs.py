"""Contract tests for CORE-42 extraction decision documentation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
DOCS = ROOT / "docs"
ADR = DOCS / "ADR-0027-core-package-extraction-decision.md"
REPORT = DOCS / "release-evidence" / "issue-170-core-extraction-benchmark-and-dependency-report.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_adr_0027_records_separate_gedcom_and_rootsmagic_decisions() -> None:
    text = _read(ADR)
    normalized = " ".join(text.split())

    assert "- Status: Accepted" in text
    assert "### GEDCOM" in text
    assert "### RootsMagic" in text
    assert "keep internal now (defer extraction)" in normalized
    assert "keep internal (decline extraction)" in normalized
    assert "independent consumer" in normalized
    assert "materially reduces dependency and release surface" in normalized
    assert "orchestration, grants, provider behavior, and publication" in normalized
    assert "#131" in text
    assert "#160" in text
    assert "#162" in text
    assert "#132 remains unblocked" in normalized


def test_adr_0027_preserves_required_safety_invariants() -> None:
    normalized = " ".join(_read(ADR).split())

    for requirement in (
        "loss-minimal and deterministic",
        "RootsMagic sources remain immutable",
        "`provider=none` remains network-free",
        "bounded, path-safe, opaque-reference based, and cancellation-safe",
    ):
        assert requirement in normalized


def test_adr_0027_keeps_unreproduced_performance_observations_provisional() -> None:
    normalized = " ".join(_read(ADR).split())

    assert "have not been independently reproduced" in normalized
    assert "the standard capture did not complete" in normalized
    assert "do not satisfy the reproducible `capture` gate" in normalized
    assert "are not acceptance evidence until reproduced" in normalized


def test_issue_170_report_records_methods_and_seven_run_medians() -> None:
    text = _read(REPORT)
    normalized = " ".join(text.split())

    assert "scripts/characterize_core_contracts.py verify" in text
    assert "scripts/check_architecture_contracts.py" in text
    assert "seven warm runs" in normalized
    assert "CLI cold start" in text
    assert "Offline GEDCOM merge representative warm operation" in text
    assert "median elapsed_ms: **609.040**" in text
    assert "median elapsed_ms: **1891.548**" in text
    assert "median peak_rss_bytes: **68,956,160**" in text
    assert "median peak_rss_bytes: **50,229,248**" in text
    assert "have not been independently reproduced in this review" in normalized
    assert "do not satisfy the reproducible `capture` gate" in normalized
    assert "must not be presented as completed acceptance evidence" in normalized


def test_issue_170_report_cites_dependency_facade_and_release_burden_evidence() -> None:
    normalized = " ".join(_read(REPORT).split())

    for phrase in (
        "eleven test modules importing `gedcom.engine` or `gedcom.incremental` directly",
        "exactly two",
        "compatibility import exceptions",
        "0 dependency exceptions",
        "2 characterization import exceptions",
        "SemVer/changelog ownership",
        "release/signing/attestation pipeline",
        "vulnerability response ownership",
    ):
        assert phrase in normalized
