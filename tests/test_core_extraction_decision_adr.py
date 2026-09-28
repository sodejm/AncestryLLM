"""Contract checks for the CORE-42 extraction decision record."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ADR = _ROOT / "docs" / "ADR-0027-core-package-extraction-decision.md"
_HOME = _ROOT / "docs" / "Home.md"
_SIDEBAR = _ROOT / "docs" / "_Sidebar.md"


def test_core_extraction_decision_records_separate_outcomes_and_invariants() -> None:
    text = _ADR.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "### GEDCOM" in text
    assert "### RootsMagic" in text
    gedcom = text.split("### GEDCOM", 1)[1].split("### RootsMagic", 1)[0]
    rootsmagic = text.split("### RootsMagic", 1)[1].split("\n## ", 1)[0]
    assert "keep internal now (defer extraction)" in " ".join(gedcom.split())
    assert "keep internal (decline extraction)" in " ".join(rootsmagic.split())
    for invariant in (
        "loss-minimal and deterministic",
        "RootsMagic sources remain immutable",
        "`provider=none` remains network-free",
        "bounded, path-safe, opaque-reference based, and cancellation-safe",
    ):
        assert invariant in normalized
    assert "No package is published by this story" in normalized
    assert "#132 remains unblocked" in normalized


def test_core_extraction_decision_adr_is_linked_from_docs_navigation() -> None:
    for page in (_HOME, _SIDEBAR):
        text = page.read_text(encoding="utf-8")
        assert "ADR-0027-core-package-extraction-decision.md" in text


def test_core_extraction_performance_capture_preserves_acceptance_limits() -> None:
    normalized = " ".join(_ADR.read_text(encoding="utf-8").split())

    assert "all 51 semantic nodes" in normalized
    assert "raw seven-run capture" in normalized
    assert "matched baseline comparison remains required" in normalized
    assert "Historical private-helper observations remain provisional" in normalized
