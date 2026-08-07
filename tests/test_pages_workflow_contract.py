"""Contract checks for GitHub Pages deployment from canonical documentation."""

from __future__ import annotations

import json
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


def test_pages_workflow_uses_upload_artifact_v7_compatible_action() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    # upload-pages-artifact v5 uses upload-artifact v7 internally, matching
    # the repository's pinned artifact action version.
    assert "actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9" in workflow


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


def test_pages_config_enables_seo_and_sitemap_plugins() -> None:
    config = (ROOT / "docs" / "_config.yml").read_text(encoding="utf-8")

    assert "jekyll-seo-tag" in config
    assert "jekyll-sitemap" in config


def test_robots_txt_permits_all_routes_and_references_sitemap() -> None:
    robots = ROOT / "docs" / "robots.txt"

    assert robots.is_file()
    content = robots.read_text(encoding="utf-8")
    assert "User-agent: *" in content
    assert "Allow: /" in content
    assert "sitemap.xml" in content


def test_page_metadata_sidecar_covers_all_canonical_pages() -> None:
    metadata_path = ROOT / "docs" / "_data" / "page_metadata.json"
    assert metadata_path.is_file()

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    for key, value in metadata.items():
        if key.startswith("_"):
            continue
        assert "title" in value, f"missing title for {key}"
        assert "description" in value, f"missing description for {key}"
        assert value["title"], f"empty title for {key}"
        assert value["description"], f"empty description for {key}"
