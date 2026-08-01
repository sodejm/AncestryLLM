"""Regression tests for the canonical documentation Pages build adapter."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "prepare_pages_source.py"
sys.path.insert(0, str(_SCRIPT.parent))
_SPEC = importlib.util.spec_from_file_location("prepare_pages_source", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
pages_source = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = pages_source
_SPEC.loader.exec_module(pages_source)


def _write_docs(source: Path) -> None:
    (source / "_layouts").mkdir(parents=True)
    (source / "api").mkdir()
    (source / "Home.md").write_text(
        "# Home\n\n[Guide](Guide.md)\n",
        encoding="utf-8",
    )
    (source / "Guide.md").write_text(
        "# Guide\n\n"
        "[Home](Home.md#top)\n"
        "[API](api/README.md)\n"
        "Use `[Guide](Guide.md)` as the source form.\n"
        "```markdown\n[Guide](Guide.md)\n```\n",
        encoding="utf-8",
    )
    (source / "api" / "README.md").write_text("# API\n", encoding="utf-8")
    (source / "api" / "openapi-v1.json").write_text("{}\n", encoding="utf-8")
    (source / "_Sidebar.md").write_text(
        "- [Home](Home.md)\n- [Guide](Guide.md)\n",
        encoding="utf-8",
    )
    (source / "_config.yml").write_text("title: Test docs\n", encoding="utf-8")
    (source / "_layouts" / "documentation.html").write_text(
        "{{ content }}\n",
        encoding="utf-8",
    )


def test_prepare_pages_source_preserves_docs_and_rewrites_build_copy(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "pages-source"
    _write_docs(source)

    result = pages_source.prepare_pages_source(source, destination)

    assert result.page_count == 3
    assert (destination / "index.md").read_text(encoding="utf-8") == (
        "---\nlayout: documentation\n---\n\n# Home\n\n[Guide](Guide.html)\n"
    )
    guide = (destination / "Guide.md").read_text(encoding="utf-8")
    assert "[Home](./#top)" in guide
    assert "[API](api/README.html)" in guide
    assert "`[Guide](Guide.md)`" in guide
    assert "```markdown\n[Guide](Guide.md)\n```" in guide
    assert (destination / "api" / "README.md").is_file()
    assert (destination / "api" / "openapi-v1.json").read_text(encoding="utf-8") == "{}\n"
    assert (destination / "_includes" / "sidebar.md").read_text(encoding="utf-8") == (
        "- [Home]({{site.baseurl}}/)\n- [Guide]({{site.baseurl}}/Guide.html)\n"
    )
    assert (source / "Home.md").read_text(encoding="utf-8") == "# Home\n\n[Guide](Guide.md)\n"
    assert not (destination / "Home.md").exists()


def test_prepare_pages_source_rejects_broken_markdown_targets(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "pages-source"
    _write_docs(source)
    (source / "Home.md").write_text("# Home\n\n[Missing](Missing.md)\n", encoding="utf-8")

    with pytest.raises(pages_source.PagesSourceError, match="broken Markdown target"):
        pages_source.prepare_pages_source(source, destination)

    assert not destination.exists()


def test_repository_docs_stage_every_markdown_page(tmp_path: Path) -> None:
    source = ROOT / "docs"
    destination = tmp_path / "pages-source"

    pages_source.prepare_pages_source(source, destination)

    for page in source.rglob("*.md"):
        relative = page.relative_to(source)
        if relative == Path("_Sidebar.md"):
            continue
        staged = destination / ("index.md" if relative == Path("Home.md") else relative)
        assert staged.is_file(), relative
    assert (destination / "api" / "openapi-v1.json").is_file()
