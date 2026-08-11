#!/usr/bin/env python3
"""Deterministically mirror validated Markdown documentation into a wiki checkout."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from docs_linking import SourceIndex, encode_path, split_destination
from rewrite_wiki_links import rewrite_markdown_link_destinations
from validate_wiki_docs import validate_wiki_source

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_ASSET_MANIFEST = ".ancestryllm-managed-assets.json"


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


def _source_content(source: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    index = SourceIndex(source)
    pages = sorted(source.rglob("*.md"), key=lambda path: path.relative_to(source).as_posix())
    rewritten: dict[str, bytes] = {}
    assets: dict[str, bytes] = {}
    for page in pages:
        current = PurePosixPath(page.relative_to(source).as_posix())

        def rewrite(destination: str, current_page: PurePosixPath = current) -> str:
            target, title = split_destination(destination)
            resolved = index.resolve(current_page, target)
            if resolved is None:
                return destination
            if resolved.relative.suffix != ".md":
                asset_name = resolved.relative.as_posix()
                assets[asset_name] = index.files[asset_name].read_bytes()
            path = (
                encode_path(resolved.relative.stem)
                if resolved.relative.suffix == ".md"
                else encode_path(resolved.relative)
            )
            return f"{resolved.url(path)}{title}"

        rewritten[page.name] = rewrite_markdown_link_destinations(
            page.read_text(encoding="utf-8"), rewrite, include_images=True
        ).encode("utf-8")
    return rewritten, dict(sorted(assets.items()))


def _safe_asset_name(value: object) -> str:
    if not isinstance(value, str):
        raise WikiSyncError("managed asset manifest contains a non-string path")
    path = PurePosixPath(value)
    if (
        not value
        or value != path.as_posix()
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.casefold() == ".git" for part in path.parts)
        or "\\" in value
        or value == _ASSET_MANIFEST
        or path.suffix.casefold() == ".md"
    ):
        raise WikiSyncError(f"managed asset manifest contains an unsafe path: {value}")
    return value


def _previous_assets(destination: Path) -> set[str]:
    manifest = destination / _ASSET_MANIFEST
    if manifest.is_symlink():
        raise WikiSyncError("managed asset manifest must not be a symlink")
    if not manifest.exists():
        return set()
    if not manifest.is_file():
        raise WikiSyncError("managed asset manifest is not a regular file")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
        raise WikiSyncError(f"managed asset manifest is invalid: {error}") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise WikiSyncError("managed asset manifest has an unsupported format")
    values = payload.get("assets")
    if not isinstance(values, list):
        raise WikiSyncError("managed asset manifest has an unsupported format")
    assets = [_safe_asset_name(value) for value in values]
    if len(assets) != len(set(assets)):
        raise WikiSyncError("managed asset manifest contains duplicate paths")
    return set(assets)


def _asset_manifest(assets: Mapping[str, bytes]) -> bytes:
    payload = {"version": 1, "assets": sorted(assets)}
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _validate_destination_root(destination: Path) -> None:
    if destination.is_symlink():
        raise WikiSyncError("symlinked destination directory is not supported")
    if not destination.exists():
        raise WikiSyncError(f"destination directory does not exist: {destination}")
    if not destination.is_dir():
        raise WikiSyncError(f"destination path is not a directory: {destination}")


def _validate_destination(
    destination: Path,
    pages: Mapping[str, bytes],
    assets: Mapping[str, bytes],
    previous_assets: set[str],
) -> None:
    for page_name in pages:
        target = destination / page_name
        if target.exists() and target.is_dir() and not target.is_symlink():
            raise WikiSyncError(f"destination page path is a directory: {page_name}")

    destination_names = {name.casefold(): name for name in (*pages, _ASSET_MANIFEST)}
    for asset_name in sorted(set(assets) | previous_assets):
        _safe_asset_name(asset_name)
        previous = destination_names.setdefault(asset_name.casefold(), asset_name)
        if previous != asset_name:
            raise WikiSyncError(
                f"case-insensitive destination collision: {previous} and {asset_name}"
            )
        target = destination / PurePosixPath(asset_name)
        for candidate in (target, *target.parents):
            if candidate == destination.parent:
                break
            if candidate.is_symlink():
                raise WikiSyncError(f"managed asset path contains a symlink: {asset_name}")
            if candidate != target and candidate.exists() and not candidate.is_dir():
                raise WikiSyncError(f"managed asset parent is not a directory: {asset_name}")
        if target.exists() and not target.is_file():
            raise WikiSyncError(f"managed asset path is not a regular file: {asset_name}")
        if asset_name in assets and asset_name not in previous_assets and target.exists():
            raise WikiSyncError(f"refusing to overwrite unrecorded wiki asset: {asset_name}")


def sync_wiki_docs(source: Path, destination: Path) -> SyncResult:
    """Mirror ``source/**/*.md`` to flat, top-level pages in ``destination``.

    Top-level Markdown paths in the destination are the managed wiki namespace.
    Referenced assets recorded in the managed manifest are synchronized too;
    other paths, including ``.git`` and unrecorded non-Markdown content, are untouched.
    """
    validation_errors = validate_wiki_source(source)
    if validation_errors:
        messages = "; ".join(error.message for error in validation_errors)
        raise WikiSyncError(f"source validation failed: {messages}")

    pages, assets = _source_content(source)
    _validate_destination_root(destination)
    previous_assets = _previous_assets(destination)
    _validate_destination(destination, pages, assets, previous_assets)

    managed_pages = sorted(
        (path for path in destination.glob("*.md") if path.is_file() or path.is_symlink()),
        key=lambda path: path.name,
    )
    removed: list[str] = [path.name for path in managed_pages if path.name not in pages]
    for page_name in removed:
        (destination / page_name).unlink()

    for asset_name in sorted(previous_assets - set(assets)):
        target = destination / PurePosixPath(asset_name)
        if target.exists():
            target.unlink()
            removed.append(asset_name)
        parent = target.parent
        while parent != destination and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    copied: list[str] = []
    for page_name, content in pages.items():
        target = destination / page_name
        if target.is_symlink():
            target.unlink()
        elif target.exists() and target.read_bytes() == content:
            continue
        target.write_bytes(content)
        copied.append(page_name)

    for asset_name, content in assets.items():
        target = destination / PurePosixPath(asset_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == content:
            continue
        target.write_bytes(content)
        copied.append(asset_name)

    manifest = destination / _ASSET_MANIFEST
    if assets:
        content = _asset_manifest(assets)
        if not manifest.exists() or manifest.read_bytes() != content:
            manifest.write_bytes(content)
            copied.append(_ASSET_MANIFEST)
    elif manifest.exists():
        manifest.unlink()
        removed.append(_ASSET_MANIFEST)

    return SyncResult(copied=tuple(copied), removed=tuple(removed))


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

    print(f"wiki-sync: copied {len(result.copied)} file(s), removed {len(result.removed)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
