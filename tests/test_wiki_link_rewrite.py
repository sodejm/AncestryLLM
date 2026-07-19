"""Regression tests for GitHub Wiki UI link rendering."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[1] / "scripts" / "rewrite_wiki_links.py"
_SPEC = importlib.util.spec_from_file_location("rewrite_wiki_links", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
wiki_links = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = wiki_links
_SPEC.loader.exec_module(wiki_links)


def test_local_markdown_links_use_wiki_page_targets() -> None:
    markdown = "[Threat model](THREAT_MODEL.md)\n[CLI](CLI.md#providers-and-secrets)\n"

    assert wiki_links.rewrite_wiki_links(markdown) == (
        "[Threat model](THREAT_MODEL)\n[CLI](CLI#providers-and-secrets)\n"
    )


def test_non_page_targets_are_preserved() -> None:
    markdown = (
        "[External](https://example.com/Guide.md)\n"
        "![Diagram](THREAT_MODEL.md)\n"
        "[Section](#release-decision)\n"
        "[Email](mailto:docs@example.com)\n"
    )

    assert wiki_links.rewrite_wiki_links(markdown) == markdown


def test_code_examples_are_preserved() -> None:
    markdown = (
        "Use `[Guide](Guide.md)` as the source form.\n"
        "```markdown\n"
        "[Guide](Guide.md)\n"
        "```\n"
        "Navigate with [Guide](Guide.md).\n"
    )

    assert wiki_links.rewrite_wiki_links(markdown) == (
        "Use `[Guide](Guide.md)` as the source form.\n"
        "```markdown\n"
        "[Guide](Guide.md)\n"
        "```\n"
        "Navigate with [Guide](Guide).\n"
    )


def test_directory_rewrite_updates_only_regular_markdown_pages(tmp_path: Path) -> None:
    page = tmp_path / "_Sidebar.md"
    page.write_text("[Threat model](THREAT_MODEL.md)\n", encoding="utf-8")
    (tmp_path / "asset.txt").write_text("[Guide](Guide.md)\n", encoding="utf-8")

    wiki_links.rewrite_wiki_directory(tmp_path)

    assert page.read_text(encoding="utf-8") == "[Threat model](THREAT_MODEL)\n"
    assert (tmp_path / "asset.txt").read_text(encoding="utf-8") == "[Guide](Guide.md)\n"
