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
        "[Home](Home.md#home)\n"
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
    assert "[Home](./#home)" in guide
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


def test_prepare_pages_source_injects_page_metadata_into_staged_pages(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "pages-source"
    _write_docs(source)
    (source / "_data").mkdir()
    (source / "_data" / "page_metadata.json").write_text(
        '{"Home.md": {"title": "Test home title", "description": "Test home desc"},'
        '"Guide.md": {"title": "Test guide title", "description": "Test guide desc"},'
        '"api/README.md": {"title": "Test API title", "description": "Test API desc"}}',
        encoding="utf-8",
    )

    pages_source.prepare_pages_source(source, destination)

    index_content = (destination / "index.md").read_text(encoding="utf-8")
    assert 'title: "Test home title"' in index_content
    assert 'description: "Test home desc"' in index_content
    assert "layout: documentation" in index_content
    guide_content = (destination / "Guide.md").read_text(encoding="utf-8")
    assert 'title: "Test guide title"' in guide_content


def test_prepare_pages_source_overrides_existing_front_matter_with_sidecar_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "pages-source"
    _write_docs(source)
    (source / "Home.md").write_text(
        "---\nlayout: legacy\ntitle: Stale title\ndescription: Stale description\ncustom: keep\n---\n"
        "# Home\n\n[Guide](Guide.md)\n",
        encoding="utf-8",
    )
    (source / "_data").mkdir()
    (source / "_data" / "page_metadata.json").write_text(
        '{"Home.md": {"title": "Canonical title", "description": "Canonical description"},'
        '"Guide.md": {"title": "Guide title", "description": "Guide description"},'
        '"api/README.md": {"title": "API title", "description": "API description"}}',
        encoding="utf-8",
    )

    pages_source.prepare_pages_source(source, destination)

    index_content = (destination / "index.md").read_text(encoding="utf-8")
    assert index_content.count("layout:") == 1
    assert "layout: documentation" in index_content
    assert index_content.count("title:") == 1
    assert 'title: "Canonical title"' in index_content
    assert "Stale title" not in index_content
    assert index_content.count("description:") == 1
    assert 'description: "Canonical description"' in index_content
    assert "custom: keep" in index_content


def test_prepare_pages_source_does_not_add_metadata_to_wiki_output(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    _write_docs(source)
    (source / "_data").mkdir()
    (source / "_data" / "page_metadata.json").write_text(
        '{"Home.md": {"title": "Test title", "description": "Test desc"},'
        '"Guide.md": {"title": "Guide title", "description": "Guide desc"},'
        '"api/README.md": {"title": "API title", "description": "API desc"}}',
        encoding="utf-8",
    )

    import importlib.util as _ilu
    import sys as _sys

    _WIKI_SCRIPT = ROOT / "scripts" / "sync_wiki_docs.py"
    _WIKI_SPEC = _ilu.spec_from_file_location("sync_wiki_docs_meta_test", _WIKI_SCRIPT)
    assert _WIKI_SPEC is not None and _WIKI_SPEC.loader is not None
    sync_wiki = _ilu.module_from_spec(_WIKI_SPEC)
    _sys.modules[_WIKI_SPEC.name] = sync_wiki
    _WIKI_SPEC.loader.exec_module(sync_wiki)

    wiki_dest = tmp_path / "wiki"
    wiki_dest.mkdir()
    (wiki_dest / ".git").mkdir()
    sync_wiki.sync_wiki_docs(source, wiki_dest)

    home_wiki = (wiki_dest / "Home.md").read_text(encoding="utf-8")
    assert "layout:" not in home_wiki
    assert "title:" not in home_wiki


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


def test_prepare_resolves_encoded_links_queries_titles_and_assets(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "pages-source"
    (source / "guides").mkdir(parents=True)
    (source / "assets").mkdir()
    (source / "Home.md").write_text("# Home\n\n[Guide](guides/Guide%20One.md)\n", encoding="utf-8")
    (source / "_Sidebar.md").write_text(
        "[Guide](guides/Guide%20One.md) ![Logo](assets/logo.svg)\n", encoding="utf-8"
    )
    (source / "guides" / "Guide One.md").write_text(
        '[Home](../Home.md?mode=full#home "Home title") ![Logo](../assets/logo.svg)\n',
        encoding="utf-8",
    )
    (source / "assets" / "logo.svg").write_text("<svg/>\n", encoding="utf-8")

    pages_source.prepare_pages_source(source, destination)

    assert "[Guide](guides/Guide%20One.html)" in (destination / "index.md").read_text(
        encoding="utf-8"
    )
    guide = (destination / "guides" / "Guide One.md").read_text(encoding="utf-8")
    assert '[Home](../?mode=full#home "Home title")' in guide
    assert "![Logo](../assets/logo.svg)" in guide
    sidebar = (destination / "_includes" / "sidebar.md").read_text(encoding="utf-8")
    assert "![Logo]({{site.baseurl}}/assets/logo.svg)" in sidebar


def test_prepare_preserves_query_only_same_page_links(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "pages-source"
    _write_docs(source)
    (source / "Guide.md").write_text(
        "# Guide\n\n## Topic\n\n[Filtered view](?view=compact#topic)\n",
        encoding="utf-8",
    )

    pages_source.prepare_pages_source(source, destination)

    guide = (destination / "Guide.md").read_text(encoding="utf-8")
    assert "[Filtered view](?view=compact#topic)" in guide


def test_prepare_rejects_missing_assets_before_creating_destination(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    destination = tmp_path / "pages-source"
    _write_docs(source)
    (source / "Guide.md").write_text("![Missing](assets/missing.svg)\n", encoding="utf-8")

    with pytest.raises(pages_source.PagesSourceError, match="broken local target"):
        pages_source.prepare_pages_source(source, destination)

    assert not destination.exists()
