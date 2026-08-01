#!/usr/bin/env python3
"""Prepare canonical Markdown documentation for the Jekyll Pages build."""

from __future__ import annotations

import argparse
import posixpath
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from rewrite_wiki_links import rewrite_markdown_link_destinations
from validate_wiki_docs import validate_wiki_source

_DESTINATION = re.compile(r"(?P<target>\S+)(?P<title>.*)", re.DOTALL)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_FRONT_MATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<metadata>.*?)(?:\r?\n)---[ \t]*(?:\r?\n|\Z)", re.DOTALL
)
_HOME_PAGE = PurePosixPath("Home.md")
_SIDEBAR_PAGE = PurePosixPath("_Sidebar.md")


class PagesSourceError(ValueError):
    """Raised when canonical documentation cannot be converted safely for Pages."""


@dataclass(frozen=True)
class PagesSourceResult:
    """A compact summary of the staged Jekyll source tree."""

    page_count: int
    asset_count: int


def _relative(path: Path, source: Path) -> PurePosixPath:
    return PurePosixPath(path.relative_to(source).as_posix())


def _output_path(page: PurePosixPath) -> PurePosixPath:
    return PurePosixPath("index.md") if page == _HOME_PAGE else page


def _with_documentation_layout(markdown: str) -> str:
    front_matter = _FRONT_MATTER.match(markdown)
    if front_matter is not None:
        metadata = front_matter.group("metadata")
        if re.search(r"(?m)^layout\s*:", metadata):
            return markdown
        return (
            markdown[: front_matter.start("metadata")]
            + "layout: documentation\n"
            + markdown[front_matter.start("metadata") :]
        )
    return f"---\nlayout: documentation\n---\n\n{markdown}"


def _relative_site_target(current: PurePosixPath, target: PurePosixPath) -> str:
    target_html = _output_path(target).with_suffix(".html")
    if target_html == PurePosixPath("index.html"):
        relative = posixpath.relpath(".", _output_path(current).parent.as_posix())
        return "./" if relative == "." else f"{relative.rstrip('/')}/"
    return posixpath.relpath(
        target_html.as_posix(),
        _output_path(current).parent.as_posix(),
    )


def _rewrite_page_destination(
    destination: str,
    *,
    source: Path,
    current_page: PurePosixPath,
) -> str:
    parsed = _DESTINATION.fullmatch(destination)
    if parsed is None:
        return destination

    target = parsed.group("target")
    path, separator, fragment = target.partition("#")
    if not path.endswith(".md") or "://" in path or path.startswith(("mailto:", "tel:")):
        return destination
    if path.startswith(("/", "\\")) or _WINDOWS_DRIVE.match(path):
        raise PagesSourceError(f"unsafe Markdown target: {current_page} -> {path}")

    candidate = (source / current_page.parent / path).resolve()
    try:
        target_page = _relative(candidate, source)
    except ValueError as error:
        raise PagesSourceError(f"unsafe Markdown target: {current_page} -> {path}") from error
    if not candidate.is_file() or candidate.suffix != ".md":
        raise PagesSourceError(f"broken Markdown target: {current_page} -> {path}")

    if current_page == _SIDEBAR_PAGE:
        target_html = _output_path(target_page).with_suffix(".html")
        rewritten = (
            "{{site.baseurl}}/"
            if target_html == PurePosixPath("index.html")
            else f"{{{{site.baseurl}}}}/{target_html.as_posix()}"
        )
    else:
        rewritten = _relative_site_target(current_page, target_page)
    if separator:
        rewritten = f"{rewritten}#{fragment}"
    return f"{rewritten}{parsed.group('title')}"


def _pages_source_errors(source: Path) -> list[str]:
    return [error.message for error in validate_wiki_source(source)]


def _stage_documentation(source: Path, staging: Path) -> PagesSourceResult:
    page_count = 0
    asset_count = 0
    sidebar: str | None = None
    files = sorted(
        (path for path in source.rglob("*") if path.is_file()),
        key=lambda path: _relative(path, source).as_posix(),
    )
    for source_file in files:
        relative = _relative(source_file, source)
        if source_file.suffix != ".md":
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            asset_count += 1
            continue

        markdown = source_file.read_text(encoding="utf-8")
        rewritten = rewrite_markdown_link_destinations(
            markdown,
            lambda destination, current_page=relative: _rewrite_page_destination(
                destination,
                source=source,
                current_page=current_page,
            ),
        )
        if relative == _SIDEBAR_PAGE:
            sidebar = rewritten
            continue

        destination = staging / _output_path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_with_documentation_layout(rewritten), encoding="utf-8")
        page_count += 1

    if sidebar is not None:
        sidebar_destination = staging / "_includes" / "sidebar.md"
        sidebar_destination.parent.mkdir(parents=True, exist_ok=True)
        sidebar_destination.write_text(sidebar, encoding="utf-8")
    return PagesSourceResult(page_count=page_count, asset_count=asset_count)


def prepare_pages_source(source: Path, destination: Path) -> PagesSourceResult:
    """Build an isolated, Jekyll-ready copy of canonical documentation."""
    source = source.resolve()
    destination = destination.resolve()
    errors = _pages_source_errors(source)
    if errors:
        raise PagesSourceError("; ".join(errors))
    if destination.exists():
        raise PagesSourceError(f"destination already exists: {destination}")
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise PagesSourceError("destination must not be inside the documentation source")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        result = _stage_documentation(source, staging)
        staging.replace(destination)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="canonical documentation directory"
    )
    parser.add_argument(
        "--destination", type=Path, required=True, help="new Jekyll source directory"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = prepare_pages_source(args.source, args.destination)
    except PagesSourceError as error:
        print(f"pages-source: {error}", file=sys.stderr)
        return 1
    print(f"pages-source: prepared {result.page_count} pages and {result.asset_count} assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
