#!/usr/bin/env python3
"""Validate a local Markdown source directory before publishing it to a wiki.

The validator deliberately never contacts or checks out a wiki repository.  It
only reads the directory supplied by ``--source`` and exits non-zero when that
directory cannot safely be synchronized.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from docs_linking import DocumentationLinkError, SourceIndex, source_anchors, split_destination
from rewrite_wiki_links import rewrite_markdown_link_destinations

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_WIKI_LINK = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")


@dataclass(frozen=True)
class ValidationError:
    """One deterministic explanation for a rejected source directory."""

    message: str


def _relative_display(path: Path, source: Path) -> str:
    return path.relative_to(source).as_posix()


def _find_symlinks(source: Path) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for directory, directories, files in os.walk(source, followlinks=False):
        parent = Path(directory)
        for name in sorted([*directories, *files]):
            candidate = parent / name
            if candidate.is_symlink():
                errors.append(
                    ValidationError(
                        f"symlinked source is not supported: {_relative_display(candidate, source)}"
                    )
                )
    return errors


def _markdown_files(source: Path) -> list[Path]:
    return sorted(
        (path for path in source.rglob("*.md") if path.is_file() and not path.is_symlink()),
        key=lambda path: _relative_display(path, source),
    )


def _page_name(path: Path, source: Path) -> str:
    # GitHub wikis use a flat page namespace even when the canonical source is
    # organized into subdirectories.
    return path.stem


def _source_errors(source: Path, pages: Sequence[Path]) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if source.is_symlink():
        errors.append(ValidationError("symlinked source directory is not supported"))
    if not source.exists():
        errors.append(ValidationError(f"source directory does not exist: {source}"))
        return errors
    if not source.is_dir():
        errors.append(ValidationError(f"source path is not a directory: {source}"))
        return errors

    relative_pages = {_relative_display(page, source) for page in pages}
    if "Home.md" not in relative_pages:
        errors.append(ValidationError("required page is missing: Home.md"))

    by_casefolded_path: dict[str, str] = {}
    by_page_basename: dict[str, str] = {}
    for page in pages:
        relative_path = _relative_display(page, source)
        casefolded_path = relative_path.casefold()
        previous_path = by_casefolded_path.setdefault(casefolded_path, relative_path)
        if previous_path != relative_path:
            errors.append(
                ValidationError(
                    f"case-insensitive filename collision: {previous_path} and {relative_path}"
                )
            )

        page_basename = Path(_page_name(page, source)).name.casefold()
        previous_page = by_page_basename.setdefault(page_basename, relative_path)
        if previous_page != relative_path:
            errors.append(
                ValidationError(f"duplicate wiki page name: {previous_page} and {relative_path}")
            )
    return errors


def _target_from_wiki_link(raw_target: str) -> str:
    return raw_target.split("|", maxsplit=1)[0].strip()


def _target_from_markdown_link(raw_target: str) -> str:
    return raw_target.strip().split(maxsplit=1)[0]


def _is_external_target(target: str) -> bool:
    return "://" in target or target.startswith(("mailto:", "tel:"))


def _unsafe_target(target: str) -> bool:
    if not target or target.startswith("/") or "\\" in target or _WINDOWS_DRIVE.match(target):
        return True
    return any(part in {"", ".", ".."} for part in PurePosixPath(target).parts)


def _normalize_target(target: str) -> str:
    target_without_anchor = target.split("#", maxsplit=1)[0]
    return target_without_anchor.removesuffix(".md")


def _sidebar_targets(sidebar: Path) -> Iterable[tuple[str, str]]:
    text = sidebar.read_text(encoding="utf-8")
    for match in _WIKI_LINK.finditer(text):
        yield "wiki", _target_from_wiki_link(match.group(1))
    for match in _MARKDOWN_LINK.finditer(text):
        yield "markdown", _target_from_markdown_link(match.group(1))


def _navigation_errors(source: Path, pages: Sequence[Path]) -> list[ValidationError]:
    sidebar = source / "_Sidebar.md"
    if not sidebar.exists():
        return []

    known_pages = {_page_name(page, source) for page in pages}
    try:
        index = SourceIndex(source)
    except DocumentationLinkError:
        index = None
    errors: list[ValidationError] = []
    for style, target in _sidebar_targets(sidebar):
        if target.startswith("#") or _is_external_target(target):
            continue
        target_name = _normalize_target(target)
        # Wiki-style links address the flat Wiki namespace. Markdown links are
        # validated by the canonical path-aware pass below.
        if (
            (style == "wiki" and ("/" in target_name or _unsafe_target(target_name)))
            or target.startswith(("../", "/"))
            or _WINDOWS_DRIVE.match(target)
        ):
            errors.append(ValidationError(f"unsafe sidebar target: {target}"))
        elif style == "wiki" and target_name not in known_pages:
            errors.append(ValidationError(f"broken sidebar target: {target}"))
        elif style == "markdown" and index is not None:
            try:
                index.resolve(PurePosixPath("_Sidebar.md"), target)
            except DocumentationLinkError as error:
                label = "unsafe" if "unsafe" in str(error) else "broken"
                errors.append(ValidationError(f"{label} sidebar target: {target}"))
    return errors


def _link_errors(source: Path, pages: Sequence[Path]) -> list[ValidationError]:
    try:
        index = SourceIndex(source)
    except DocumentationLinkError as error:
        return [ValidationError(str(error))]

    anchors = {
        PurePosixPath(page.relative_to(source).as_posix()): source_anchors(
            page.read_text(encoding="utf-8")
        )
        for page in pages
    }
    errors: list[ValidationError] = []
    for page in pages:
        current = PurePosixPath(page.relative_to(source).as_posix())

        def validate(destination: str, current_page: PurePosixPath = current) -> str:
            target, _title = split_destination(destination)
            try:
                resolved = index.resolve(current_page, target)
            except DocumentationLinkError as error:
                errors.append(ValidationError(str(error)))
                return destination
            parsed = urlsplit(target)
            if not parsed.fragment or index.is_external(target):
                return destination
            if _BAD_PERCENT.search(parsed.fragment):
                errors.append(ValidationError(f"unsafe source anchor: {current_page} -> {target}"))
                return destination
            try:
                fragment = unquote(parsed.fragment, encoding="utf-8", errors="strict")
            except UnicodeDecodeError:
                errors.append(ValidationError(f"unsafe source anchor: {current_page} -> {target}"))
                return destination
            target_page = current_page if resolved is None else resolved.relative
            if target_page.suffix == ".md" and fragment not in anchors.get(target_page, set()):
                errors.append(ValidationError(f"missing source anchor: {current_page} -> {target}"))
            return destination

        rewrite_markdown_link_destinations(
            page.read_text(encoding="utf-8"), validate, include_images=True
        )
    return errors


def _metadata_errors(source: Path, pages: Sequence[Path]) -> list[ValidationError]:
    metadata_path = source / "_data" / "page_metadata.json"
    if not metadata_path.exists():
        return []
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return [ValidationError(f"invalid page metadata: {error}")]
    if not isinstance(raw, dict):
        return [ValidationError("invalid page metadata: expected an object")]

    public_pages = {_relative_display(page, source) for page in pages if page.name != "_Sidebar.md"}
    metadata_pages = {key for key in raw if isinstance(key, str) and not key.startswith("_")}
    errors = [
        ValidationError(f"missing page metadata: {page}")
        for page in sorted(public_pages - metadata_pages)
    ]
    errors.extend(
        ValidationError(f"metadata references missing page: {page}")
        for page in sorted(metadata_pages - public_pages)
    )
    titles: dict[str, str] = {}
    for page in sorted(metadata_pages & public_pages):
        value = raw.get(page)
        if not isinstance(value, dict):
            errors.append(ValidationError(f"invalid page metadata entry: {page}"))
            continue
        title = value.get("title")
        description = value.get("description")
        if not isinstance(title, str) or not title.strip():
            errors.append(ValidationError(f"missing page metadata title: {page}"))
        else:
            previous = titles.setdefault(title.casefold(), page)
            if previous != page:
                errors.append(
                    ValidationError(f"duplicate page metadata title: {previous} and {page}")
                )
        if not isinstance(description, str) or not description.strip():
            errors.append(ValidationError(f"missing page metadata description: {page}"))
    return errors


def validate_wiki_source(source: Path) -> list[ValidationError]:
    """Return all validation problems for ``source`` in stable display order."""
    if source.is_symlink():
        return [ValidationError("symlinked source directory is not supported")]
    if not source.exists():
        return [ValidationError(f"source directory does not exist: {source}")]
    if not source.is_dir():
        return [ValidationError(f"source path is not a directory: {source}")]

    symlink_errors = _find_symlinks(source)
    if symlink_errors:
        return sorted(symlink_errors, key=lambda error: error.message)
    pages = _markdown_files(source)
    errors = [
        *_source_errors(source, pages),
        *_navigation_errors(source, pages),
        *_link_errors(source, pages),
        *_metadata_errors(source, pages),
    ]
    return sorted(errors, key=lambda error: error.message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="local directory containing Markdown files destined for the wiki",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_wiki_source(args.source)
    if errors:
        for error in errors:
            print(f"wiki-validation: {error.message}", file=sys.stderr)
        return 1
    print("wiki-validation: source is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
