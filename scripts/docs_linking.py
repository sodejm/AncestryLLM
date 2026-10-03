"""Shared, deterministic link resolution for canonical documentation publishers."""

from __future__ import annotations

import html
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_EXTERNAL_SCHEMES = frozenset({"http", "https", "mailto", "tel"})
_URL_SAFE = "/:@-._~!$&'()*+,;="
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+(?P<title>.*?)[ \t]*#*[ \t]*$")
_SETEXT_HEADING = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_EXPLICIT_HEADING_ID = re.compile(r"[ \t]+\{#(?P<identifier>[^}]+)\}[ \t]*$")
_HTML_ANCHOR = re.compile(r"\b(?:id|name)=[\"'](?P<identifier>[^\"']+)[\"']", re.IGNORECASE)


class DocumentationLinkError(ValueError):
    """Raised when a canonical local documentation target is not publishable."""


@dataclass(frozen=True)
class ResolvedTarget:
    """A resolved canonical file plus the URL components that must be retained."""

    relative: PurePosixPath
    query: str
    fragment: str

    def url(self, path: str) -> str:
        """Return ``path`` with the original query and fragment."""
        return urlunsplit(("", "", path, self.query, self.fragment))


def split_destination(destination: str) -> tuple[str, str]:
    """Split a Markdown destination into URL target and optional title suffix."""
    if destination.startswith("<"):
        closing = destination.find(">")
        if closing >= 0:
            return destination[1:closing], destination[closing + 1 :]
    match = re.fullmatch(r"(?P<target>\S+)(?P<title>.*)", destination, re.DOTALL)
    if match is None:
        return destination, ""
    return match.group("target"), match.group("title")


def encode_path(path: PurePosixPath | str) -> str:
    """Encode a canonical POSIX path for a Markdown URL."""
    return quote(str(path), safe=_URL_SAFE)


def source_anchors(markdown: str) -> set[str]:
    """Return deterministic Kramdown-style heading and explicit HTML anchors."""
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    fence: str | None = None
    previous_line: str | None = None

    def add_heading(title: str) -> None:
        explicit = _EXPLICIT_HEADING_ID.search(title)
        if explicit is not None:
            anchors.add(explicit.group("identifier"))
            return
        title = re.sub(r"!?(?:\[([^]]*)\])\([^)]*\)", r"\1", title)
        title = re.sub(r"<[^>]+>", "", title)
        title = re.sub(r"[`*_~]", "", html.unescape(title)).casefold().strip()
        identifier = re.sub(r"[^\w -]", "", title, flags=re.UNICODE)
        identifier = re.sub(r"[ _]+", "-", identifier)
        if not identifier:
            return
        occurrence = counts.get(identifier, 0)
        counts[identifier] = occurrence + 1
        anchors.add(identifier if occurrence == 0 else f"{identifier}-{occurrence}")

    for line in markdown.splitlines():
        stripped = line.lstrip()
        fence_match = re.match(r"(`{3,}|~{3,})", stripped)
        if fence_match is not None:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            previous_line = None
            continue
        if fence is not None:
            continue
        anchors.update(match.group("identifier") for match in _HTML_ANCHOR.finditer(line))
        if _SETEXT_HEADING.match(line) is not None and previous_line and previous_line.strip():
            add_heading(previous_line)
            previous_line = None
            continue
        heading = _ATX_HEADING.match(line)
        if heading is not None:
            add_heading(heading.group("title"))
            previous_line = None
            continue
        previous_line = line
    return anchors


class SourceIndex:
    """Case-safe index that resolves links from canonical source locations."""

    def __init__(self, source: Path) -> None:
        self.source = source.resolve()
        files = sorted(
            (path for path in self.source.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(self.source).as_posix(),
        )
        self.files: dict[str, Path] = {}
        folded: dict[str, str] = {}
        for path in files:
            relative = path.relative_to(self.source).as_posix()
            previous = folded.setdefault(relative.casefold(), relative)
            if previous != relative:
                raise DocumentationLinkError(
                    f"case-insensitive filename collision: {previous} and {relative}"
                )
            self.files[relative] = path

    @staticmethod
    def is_external(target: str) -> bool:
        """Return whether ``target`` is a supported external or protocol link."""
        parsed = urlsplit(target)
        return parsed.scheme.casefold() in _EXTERNAL_SCHEMES or bool(parsed.netloc)

    def resolve(self, current: PurePosixPath, target: str) -> ResolvedTarget | None:
        """Resolve one local URL; same-page and external URLs return ``None``."""
        if target.startswith("#") or not target:
            return None
        if self.is_external(target):
            return None
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            raise DocumentationLinkError(f"unsafe local target: {current} -> {target}")
        if not parsed.path:
            return None
        raw_path = parsed.path
        if (
            raw_path.startswith(("/", "\\"))
            or "\\" in raw_path
            or _WINDOWS_DRIVE.match(raw_path)
            or _BAD_PERCENT.search(raw_path)
        ):
            raise DocumentationLinkError(f"unsafe local target: {current} -> {target}")
        try:
            decoded = unquote(raw_path, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise DocumentationLinkError(f"unsafe local target: {current} -> {target}") from error
        combined = posixpath.normpath((current.parent / decoded).as_posix())
        if combined == ".." or combined.startswith("../") or combined.startswith("/"):
            raise DocumentationLinkError(f"unsafe local target: {current} -> {target}")
        path = self.files.get(combined)
        if path is None:
            kind = (
                "broken Markdown target"
                if PurePosixPath(decoded).suffix == ".md"
                else "broken local target"
            )
            raise DocumentationLinkError(f"{kind}: {current} -> {target}")
        return ResolvedTarget(PurePosixPath(combined), parsed.query, parsed.fragment)

    def relative_url(self, current: PurePosixPath, target: ResolvedTarget) -> str:
        """Return a normalized URL from ``current`` to a resolved source file."""
        relative = posixpath.relpath(target.relative.as_posix(), current.parent.as_posix())
        return target.url(encode_path(relative))
