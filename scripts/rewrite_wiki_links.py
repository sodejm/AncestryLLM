#!/usr/bin/env python3
"""Rewrite repository Markdown links for GitHub's Wiki page router."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_FENCE = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(?P<marker>`+).*?(?P=marker)", flags=re.DOTALL)
_MARKDOWN_LINK = re.compile(
    r"(?P<prefix>!?\[[^\]]*\]\()"
    r"(?P<destination>[^)\n]+)"
    r"(?P<suffix>\))"
)
_DESTINATION = re.compile(r"(?P<target>\S+)(?P<title>.*)", re.DOTALL)


def _rewrite_destination(destination: str) -> str:
    match = _DESTINATION.fullmatch(destination)
    if match is None:
        return destination

    target = match.group("target")
    path, separator, fragment = target.partition("#")
    if not path.endswith(".md") or "://" in path or path.startswith(("/", "mailto:", "tel:")):
        return destination

    wiki_target = path.removesuffix(".md")
    if separator:
        wiki_target = f"{wiki_target}#{fragment}"
    return f"{wiki_target}{match.group('title')}"


def _rewrite_links(
    fragment: str,
    rewrite_destination: Callable[[str], str],
    *,
    include_images: bool,
) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group("prefix").startswith("!") and not include_images:
            return match.group(0)
        destination = rewrite_destination(match.group("destination"))
        return f"{match.group('prefix')}{destination}{match.group('suffix')}"

    return _MARKDOWN_LINK.sub(replace, fragment)


def _rewrite_fragment(
    fragment: str,
    rewrite_destination: Callable[[str], str],
    *,
    include_images: bool,
) -> str:
    rewritten: list[str] = []
    cursor = 0
    for match in _INLINE_CODE.finditer(fragment):
        rewritten.append(
            _rewrite_links(
                fragment[cursor : match.start()],
                rewrite_destination,
                include_images=include_images,
            )
        )
        rewritten.append(match.group(0))
        cursor = match.end()
    rewritten.append(
        _rewrite_links(fragment[cursor:], rewrite_destination, include_images=include_images)
    )
    return "".join(rewritten)


def rewrite_markdown_link_destinations(
    markdown: str,
    rewrite_destination: Callable[[str], str],
    *,
    include_images: bool = False,
) -> str:
    """Rewrite Markdown links and images without changing code examples."""
    rewritten: list[str] = []
    outside_fence: list[str] = []
    fence_marker: str | None = None

    def flush_outside_fence() -> None:
        if not outside_fence:
            return
        rewritten.append(
            _rewrite_fragment(
                "".join(outside_fence),
                rewrite_destination,
                include_images=include_images,
            )
        )
        outside_fence.clear()

    for line in markdown.splitlines(keepends=True):
        fence = _FENCE.match(line)
        if fence_marker is None:
            if fence is not None:
                flush_outside_fence()
                fence_marker = fence.group("marker")
                rewritten.append(line)
            elif not line.strip():
                flush_outside_fence()
                rewritten.append(line)
            else:
                outside_fence.append(line)
            continue

        rewritten.append(line)
        if (
            fence is not None
            and fence.group("marker")[0] == fence_marker[0]
            and len(fence.group("marker")) >= len(fence_marker)
        ):
            fence_marker = None
    flush_outside_fence()
    return "".join(rewritten)


def rewrite_wiki_links(markdown: str) -> str:
    """Return Markdown whose local page links use extensionless Wiki targets."""
    return rewrite_markdown_link_destinations(markdown, _rewrite_destination)


def rewrite_wiki_directory(wiki: Path) -> None:
    """Rewrite all regular Markdown files in an already prepared Wiki tree."""
    for page in sorted(wiki.glob("*.md")):
        if not page.is_file() or page.is_symlink():
            continue
        source = page.read_text(encoding="utf-8")
        rewritten = rewrite_wiki_links(source)
        if rewritten != source:
            page.write_text(rewritten, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki", type=Path, required=True, help="prepared GitHub Wiki checkout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rewrite_wiki_directory(args.wiki)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
