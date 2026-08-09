"""Regression contracts for the responsive GitHub Pages documentation layout."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "docs" / "_layouts" / "documentation.html"
STYLESHEET = ROOT / "docs" / "assets" / "css" / "documentation.css"


def test_documentation_layout_loads_a_pages_safe_responsive_stylesheet() -> None:
    layout = LAYOUT.read_text(encoding="utf-8")

    assert (
        '<link rel="stylesheet" href="{{ \'/assets/css/documentation.css?v=\' '
        '| append: site.github.build_revision | relative_url }}">'
    ) in layout
    assert "<style>" not in layout


def test_documentation_stylesheet_preserves_readability_and_overflow_access() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    assert ".documentation-shell" in stylesheet
    assert "grid-template-columns:" in stylesheet
    assert ".documentation-navigation" in stylesheet
    assert "position: sticky" in stylesheet
    assert ".documentation-content" in stylesheet
    assert "max-inline-size:" in stylesheet
    assert ".documentation-content pre" in stylesheet
    assert ".documentation-content table" in stylesheet
    assert stylesheet.count("overflow-x: auto") >= 2
    assert "@media (max-width: 48rem)" in stylesheet


def test_documentation_layout_keeps_the_semantic_navigation_and_article_regions() -> None:
    layout = LAYOUT.read_text(encoding="utf-8")

    assert '<main id="content" class="main-content" role="main">' in layout
    assert (
        '<aside class="documentation-navigation" aria-label="Documentation navigation">' in layout
    )
    assert '<article class="documentation-content" id="main-content">' in layout
