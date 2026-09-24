"""Contract checks for the CORE-42 extraction decision record."""

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_ADR = _ROOT / "docs" / "ADR-0027-core-package-extraction-decision.md"
_HOME = _ROOT / "docs" / "Home.md"
_SIDEBAR = _ROOT / "docs" / "_Sidebar.md"


def test_core_extraction_decision_records_separate_outcomes_and_invariants() -> None:
    text = _ADR.read_text(encoding="utf-8")

    assert "### GEDCOM" in text
    assert "### RootsMagic" in text
    for invariant in (
        "loss-minimal deterministic",
        "GEDCOM handling",
        "immutable RootsMagic sources",
        "network-free `provider=none`",
        "bounded ingress",
        "opaque artifacts",
        "cancellation-safe publication",
    ):
        assert invariant in text
    assert "No packaging epic is created from #170" in text


def test_core_extraction_decision_adr_is_linked_from_docs_navigation() -> None:
    for page in (_HOME, _SIDEBAR):
        text = page.read_text(encoding="utf-8")
        assert "ADR-0027-core-package-extraction-decision.md" in text
