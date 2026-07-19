#!/usr/bin/env python3
"""Validate a local Markdown source directory before publishing it to a wiki.

The validator deliberately never contacts or checks out a wiki repository.  It
only reads the directory supplied by ``--source`` and exits non-zero when that
directory cannot safely be synchronized.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_WIKI_LINK = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


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
    return _relative_display(path.with_suffix(""), source)


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


def _sidebar_targets(sidebar: Path) -> Iterable[str]:
    text = sidebar.read_text(encoding="utf-8")
    for match in _WIKI_LINK.finditer(text):
        yield _target_from_wiki_link(match.group(1))
    for match in _MARKDOWN_LINK.finditer(text):
        yield _target_from_markdown_link(match.group(1))


def _navigation_errors(source: Path, pages: Sequence[Path]) -> list[ValidationError]:
    sidebar = source / "_Sidebar.md"
    if not sidebar.exists():
        return []

    known_pages = {_page_name(page, source) for page in pages}
    errors: list[ValidationError] = []
    for target in _sidebar_targets(sidebar):
        if target.startswith("#") or _is_external_target(target):
            continue
        target_name = _normalize_target(target)
        if _unsafe_target(target_name):
            errors.append(ValidationError(f"unsafe sidebar target: {target}"))
        elif target_name not in known_pages:
            errors.append(ValidationError(f"broken sidebar target: {target}"))
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
    errors = [*_source_errors(source, pages), *_navigation_errors(source, pages)]
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
