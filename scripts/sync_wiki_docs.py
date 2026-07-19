#!/usr/bin/env python3
"""Deterministically mirror validated Markdown documentation into a wiki checkout."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rewrite_wiki_links import rewrite_wiki_links
from validate_wiki_docs import validate_wiki_source


@dataclass(frozen=True)
class SyncResult:
    """The destination pages changed by one synchronization run."""

    copied: tuple[str, ...]
    removed: tuple[str, ...]

    @property
    def changed(self) -> bool:
        """Return whether the destination content changed."""
        return bool(self.copied or self.removed)


class WikiSyncError(ValueError):
    """A deterministic, user-facing synchronization failure."""


def _source_pages(source: Path) -> dict[str, bytes]:
    pages = sorted(source.rglob("*.md"), key=lambda path: path.relative_to(source).as_posix())
    return {
        page.name: rewrite_wiki_links(page.read_text(encoding="utf-8")).encode("utf-8")
        for page in pages
    }


def _validate_destination(destination: Path, pages: Mapping[str, bytes]) -> None:
    if destination.is_symlink():
        raise WikiSyncError("symlinked destination directory is not supported")
    if not destination.exists():
        raise WikiSyncError(f"destination directory does not exist: {destination}")
    if not destination.is_dir():
        raise WikiSyncError(f"destination path is not a directory: {destination}")

    for page_name in pages:
        target = destination / page_name
        if target.exists() and target.is_dir() and not target.is_symlink():
            raise WikiSyncError(f"destination page path is a directory: {page_name}")


def sync_wiki_docs(source: Path, destination: Path) -> SyncResult:
    """Mirror ``source/**/*.md`` to flat, top-level pages in ``destination``.

    Top-level Markdown paths in the destination are the managed wiki namespace.
    Other paths, including ``.git`` and non-Markdown content, are untouched.
    """
    validation_errors = validate_wiki_source(source)
    if validation_errors:
        messages = "; ".join(error.message for error in validation_errors)
        raise WikiSyncError(f"source validation failed: {messages}")

    pages = _source_pages(source)
    _validate_destination(destination, pages)

    managed_pages = sorted(
        (path for path in destination.glob("*.md") if path.is_file() or path.is_symlink()),
        key=lambda path: path.name,
    )
    removed = tuple(path.name for path in managed_pages if path.name not in pages)
    for page_name in removed:
        (destination / page_name).unlink()

    copied: list[str] = []
    for page_name, content in pages.items():
        target = destination / page_name
        if target.is_symlink():
            target.unlink()
        elif target.exists() and target.read_bytes() == content:
            continue
        target.write_bytes(content)
        copied.append(page_name)

    return SyncResult(copied=tuple(copied), removed=removed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="local directory containing canonical Markdown documentation",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="local wiki checkout whose top-level Markdown pages will be managed",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = sync_wiki_docs(args.source, args.destination)
    except WikiSyncError as error:
        print(f"wiki-sync: {error}", file=sys.stderr)
        return 1

    if not result.changed:
        print("wiki-sync: destination is already synchronized")
        return 0

    print(f"wiki-sync: copied {len(result.copied)} page(s), removed {len(result.removed)} page(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
