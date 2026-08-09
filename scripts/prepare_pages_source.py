#!/usr/bin/env python3
"""Prepare canonical Markdown documentation for the Jekyll Pages build."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from docs_linking import (
    DocumentationLinkError,
    SourceIndex,
    encode_path,
    split_destination,
)
from rewrite_wiki_links import rewrite_markdown_link_destinations
from validate_wiki_docs import validate_wiki_source

_FRONT_MATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<metadata>.*?)(?:\r?\n)---[ \t]*(?:\r?\n|\Z)", re.DOTALL
)
_HOME_PAGE = PurePosixPath("Home.md")
_SIDEBAR_PAGE = PurePosixPath("_Sidebar.md")
_PAGE_METADATA_PATH = PurePosixPath("_data/page_metadata.json")


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


def _with_documentation_layout(
    markdown: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> str:
    extra = "layout: documentation\n"
    if title:
        extra += f"title: {json.dumps(title)}\n"
    if description:
        extra += f"description: {json.dumps(description)}\n"

    front_matter = _FRONT_MATTER.match(markdown)
    if front_matter is not None:
        metadata = front_matter.group("metadata")
        metadata = re.sub(r"(?m)^(?:layout|title|description)[ \t]*:.*(?:\r?\n|\Z)", "", metadata)
        return (
            markdown[: front_matter.start("metadata")]
            + extra
            + metadata
            + markdown[front_matter.end("metadata") :]
        )
    return f"---\n{extra}---\n\n{markdown}"


def _relative_site_target(current: PurePosixPath, target: PurePosixPath) -> str:
    target_html = _output_path(target).with_suffix(".html")
    if target_html == PurePosixPath("index.html"):
        relative = posixpath.relpath(".", _output_path(current).parent.as_posix())
        return "./" if relative == "." else f"{relative.rstrip('/')}/"
    return encode_path(
        posixpath.relpath(
            target_html.as_posix(),
            _output_path(current).parent.as_posix(),
        )
    )


def _rewrite_page_destination(
    destination: str,
    *,
    index: SourceIndex,
    current_page: PurePosixPath,
) -> str:
    target, title = split_destination(destination)
    try:
        resolved = index.resolve(current_page, target)
    except DocumentationLinkError as error:
        raise PagesSourceError(str(error)) from error
    if resolved is None:
        return destination

    if current_page == _SIDEBAR_PAGE:
        if resolved.relative.suffix == ".md":
            target_path = _output_path(resolved.relative).with_suffix(".html")
            path = (
                "{{site.baseurl}}/"
                if target_path == PurePosixPath("index.html")
                else f"{{{{site.baseurl}}}}/{encode_path(target_path)}"
            )
        else:
            path = f"{{{{site.baseurl}}}}/{encode_path(resolved.relative)}"
    elif resolved.relative.suffix != ".md":
        return f"{index.relative_url(current_page, resolved)}{title}"
    else:
        path = _relative_site_target(current_page, resolved.relative)
    rewritten = resolved.url(path)
    return f"{rewritten}{title}"


def _pages_source_errors(source: Path) -> list[str]:
    return [error.message for error in validate_wiki_source(source)]


def _load_page_metadata(source: Path) -> dict[str, dict[str, str]]:
    metadata_path = source / _PAGE_METADATA_PATH
    if not metadata_path.is_file():
        return {}
    raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {
        key: value
        for key, value in raw.items()
        if not key.startswith("_") and isinstance(value, dict)
    }


def _stage_documentation(source: Path, staging: Path, *, source_sha: str) -> PagesSourceResult:
    page_count = 0
    asset_count = 0
    sidebar: str | None = None
    page_metadata = _load_page_metadata(source)
    index = SourceIndex(source)
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

        def rewrite(destination: str, current_page: PurePosixPath = relative) -> str:
            return _rewrite_page_destination(
                destination,
                index=index,
                current_page=current_page,
            )

        rewritten = rewrite_markdown_link_destinations(
            markdown,
            rewrite,
            include_images=True,
        )
        if relative == _SIDEBAR_PAGE:
            sidebar = rewritten
            continue

        meta = page_metadata.get(relative.as_posix(), {})
        destination = staging / _output_path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            _with_documentation_layout(
                rewritten,
                title=meta.get("title"),
                description=meta.get("description"),
            ),
            encoding="utf-8",
        )
        page_count += 1

    if sidebar is not None:
        sidebar_destination = staging / "_includes" / "sidebar.md"
        sidebar_destination.parent.mkdir(parents=True, exist_ok=True)
        sidebar_destination.write_text(sidebar, encoding="utf-8")
    build_metadata = staging / "_data" / "build.json"
    build_metadata.parent.mkdir(parents=True, exist_ok=True)
    build_metadata.write_text(
        json.dumps({"source_sha": source_sha}, indent=2) + "\n",
        encoding="utf-8",
    )
    return PagesSourceResult(page_count=page_count, asset_count=asset_count)


def prepare_pages_source(
    source: Path, destination: Path, *, source_sha: str = "local"
) -> PagesSourceResult:
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
        result = _stage_documentation(source, staging, source_sha=source_sha)
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
    parser.add_argument(
        "--source-sha", default="local", help="source revision embedded in rendered pages"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = prepare_pages_source(args.source, args.destination, source_sha=args.source_sha)
    except PagesSourceError as error:
        print(f"pages-source: {error}", file=sys.stderr)
        return 1
    print(f"pages-source: prepared {result.page_count} pages and {result.asset_count} assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
