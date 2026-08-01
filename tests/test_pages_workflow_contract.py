"""Contract checks for GitHub Pages deployment from canonical documentation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "jekyll-gh-pages.yml"


def test_pages_workflow_builds_the_prepared_documentation_source() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Prepare canonical documentation for Pages" in workflow
    assert (
        "python scripts/prepare_pages_source.py --source docs --destination _pages_source"
        in workflow
    )
    assert "actions/jekyll-build-pages@44a6e6beabd48582f863aeeb6cb2151cc1716697" in workflow
    assert "source: ./_pages_source" in workflow
    assert "destination: ./_site" in workflow
    assert "path: ./_site" in workflow


def test_pages_documentation_uses_the_canonical_docs_source() -> None:
    config = ROOT / "docs" / "_config.yml"
    layout = ROOT / "docs" / "_layouts" / "documentation.html"
    home = (ROOT / "docs" / "Home.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    wiki_sync = (ROOT / "docs" / "WIKI_SYNC.md").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert config.is_file()
    assert layout.is_file()
    assert 'url: "https://sodejm.github.io"' in config.read_text(encoding="utf-8")
    assert 'baseurl: "/AncestryLLM"' in config.read_text(encoding="utf-8")
    assert "https://sodejm.github.io/AncestryLLM/" in home
    assert "https://sodejm.github.io/AncestryLLM/" in readme
    assert "https://sodejm.github.io/AncestryLLM/" in architecture
    assert "https://sodejm.github.io/AncestryLLM/" in wiki_sync
    assert 'Documentation = "https://sodejm.github.io/AncestryLLM/"' in project
