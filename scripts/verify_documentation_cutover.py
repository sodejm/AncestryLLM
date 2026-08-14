#!/usr/bin/env python3
"""Verify the deterministic documentation publishing cutover on one source revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from check_external_doc_links import collect_external_links, validate_exception_records
from prepare_pages_source import PagesSourceError, prepare_pages_source
from sync_wiki_docs import WikiSyncError, sync_wiki_docs

_SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")


class DocumentationCutoverError(ValueError):
    """A fail-closed documentation cutover error with a stable public code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DocumentationCutoverResult:
    """Deterministic counts from the local prepublication verification."""

    page_count: int
    asset_count: int
    wiki_file_count: int
    external_link_count: int
    exception_count: int


def _tree_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or (path.exists() and not path.is_file() and not path.is_dir()):
            raise OSError(f"unsupported staged path: {relative}")
        if path.is_file():
            manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def _load_exceptions(path: Path, links: set[str]) -> list[object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as error:
        raise DocumentationCutoverError("DOCSCUTOVER_EXCEPTIONS_INVALID") from error
    records = document.get("exceptions") if isinstance(document, Mapping) else None
    if validate_exception_records(records, links):
        raise DocumentationCutoverError("DOCSCUTOVER_EXCEPTIONS_INVALID")
    assert isinstance(records, list)
    return records


def _prepare_pages(
    source: Path,
    destination: Path,
    *,
    source_sha: str,
) -> tuple[int, int, dict[str, str]]:
    try:
        result = prepare_pages_source(source, destination, source_sha=source_sha)
        manifest = _tree_manifest(destination)
    except (PagesSourceError, OSError, UnicodeError) as error:
        raise DocumentationCutoverError("DOCSCUTOVER_PAGES_FAILED") from error
    return result.page_count, result.asset_count, manifest


def _prepare_wiki(source: Path, destination: Path) -> dict[str, str]:
    destination.mkdir()
    try:
        sync_wiki_docs(source, destination)
        repeated = sync_wiki_docs(source, destination)
        if repeated.changed:
            raise DocumentationCutoverError("DOCSCUTOVER_WIKI_NOT_IDEMPOTENT")
        return _tree_manifest(destination)
    except DocumentationCutoverError:
        raise
    except (WikiSyncError, OSError, UnicodeError) as error:
        raise DocumentationCutoverError("DOCSCUTOVER_WIKI_FAILED") from error


def verify_documentation_cutover(
    *,
    source: Path,
    source_sha: str,
    exceptions_path: Path,
) -> DocumentationCutoverResult:
    """Verify Pages, Wiki, and external-link policy deterministically without network I/O."""
    if _SOURCE_SHA.fullmatch(source_sha) is None:
        raise DocumentationCutoverError("DOCSCUTOVER_SHA_INVALID")

    source = source.resolve()
    try:
        links = collect_external_links(source)
    except (OSError, UnicodeError, ValueError) as error:
        raise DocumentationCutoverError("DOCSCUTOVER_PAGES_FAILED") from error
    records = _load_exceptions(exceptions_path, links)

    with tempfile.TemporaryDirectory(prefix="ancestryllm-docs-cutover-") as temporary:
        root = Path(temporary)
        first_pages = _prepare_pages(source, root / "pages-first", source_sha=source_sha)
        second_pages = _prepare_pages(source, root / "pages-second", source_sha=source_sha)
        if first_pages != second_pages:
            raise DocumentationCutoverError("DOCSCUTOVER_PAGES_NONDETERMINISTIC")

        first_wiki = _prepare_wiki(source, root / "wiki-first")
        second_wiki = _prepare_wiki(source, root / "wiki-second")
        if first_wiki != second_wiki:
            raise DocumentationCutoverError("DOCSCUTOVER_WIKI_NONDETERMINISTIC")

    page_count, asset_count, _manifest = first_pages
    return DocumentationCutoverResult(
        page_count=page_count,
        asset_count=asset_count,
        wiki_file_count=len(first_wiki),
        external_link_count=len(links),
        exception_count=len(records),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--exceptions", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = verify_documentation_cutover(
            source=args.source,
            source_sha=args.source_sha,
            exceptions_path=args.exceptions,
        )
    except DocumentationCutoverError as error:
        print(error.code, file=sys.stderr)
        return 1
    print(
        "docs-cutover: verified "
        f"source={args.source_sha} "
        f"pages={result.page_count} "
        f"assets={result.asset_count} "
        f"wiki-files={result.wiki_file_count} "
        f"external-links={result.external_link_count} "
        f"exceptions={result.exception_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
