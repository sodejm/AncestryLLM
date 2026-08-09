#!/usr/bin/env python3
"""Validate the generated GitHub Pages artifact without network access."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

_PRODUCTION_ORIGIN = "https://sodejm.github.io"


def _srcset_urls(value: str) -> list[str]:
    """Return URL candidates from a rendered ``srcset`` attribute."""
    return [
        candidate.strip().split(maxsplit=1)[0]
        for candidate in value.split(",")
        if candidate.strip()
    ]


def _within_baseurl(target: str, baseurl: str) -> bool:
    path = unquote(urlsplit(target).path).rstrip("/")
    return path == baseurl or path.startswith(baseurl + "/")


def _on_expected_origin(target: str, expected_origin: str) -> bool:
    parsed = urlsplit(target)
    expected = urlsplit(expected_origin)
    try:
        parsed_port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        expected_port = expected.port or (443 if expected.scheme.casefold() == "https" else 80)
    except ValueError:
        return False
    return (
        parsed.username is None
        and parsed.password is None
        and parsed.scheme.casefold() == expected.scheme.casefold()
        and (parsed.hostname or "").casefold() == (expected.hostname or "").casefold()
        and parsed_port == expected_port
    )


def _rendered_route(page: Path, site: Path, baseurl: str) -> str:
    relative = page.relative_to(site).as_posix()
    if relative == "index.html":
        suffix = ""
    elif relative.endswith("/index.html"):
        suffix = relative[: -len("index.html")]
    else:
        suffix = relative
    return f"{baseurl}/{suffix}"


class _SitemapParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.locations: list[str] = []
        self._location: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "loc":
            self._location = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "loc" and self._location is not None:
            self.locations.append("".join(self._location).strip())
            self._location = None

    def handle_data(self, data: str) -> None:
        if self._location is not None:
            self._location.append(data)


def _validate_sitemap(
    site: Path,
    html_files: list[Path],
    baseurl: str,
    expected_origin: str,
) -> list[str]:
    sitemap = site / "sitemap.xml"
    if not sitemap.is_file():
        return ["rendered site is missing sitemap.xml"]
    try:
        parser = _SitemapParser()
        parser.feed(sitemap.read_text(encoding="utf-8"))
        parser.close()
    except (OSError, UnicodeDecodeError) as error:
        return [f"sitemap.xml cannot be read: {error}"]

    routes: set[str] = set()
    errors: list[str] = []
    for location in parser.locations:
        parsed = urlsplit(location)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not _within_baseurl(location, baseurl)
        ):
            errors.append(f"sitemap URL is outside repository base URL: {location}")
            continue
        if not _on_expected_origin(location, expected_origin):
            errors.append(f"sitemap URL is not on the production origin: {location}")
            continue
        routes.add(unquote(parsed.path))

    for page in html_files:
        route = _rendered_route(page, site, baseurl)
        if route not in routes:
            errors.append(f"sitemap is missing rendered route: {route}")
    return errors


def _validate_robots(site: Path, baseurl: str, expected_origin: str) -> list[str]:
    robots = site / "robots.txt"
    if not robots.is_file():
        return ["rendered site is missing robots.txt"]

    wildcard_rules: list[tuple[str, str]] = []
    sitemaps: list[str] = []
    current_agents: list[str] = []
    group_has_rules = False
    for raw_line in robots.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = (part.strip() for part in line.split(":", 1))
        field = field.casefold()
        if field == "user-agent":
            if group_has_rules:
                current_agents = []
                group_has_rules = False
            current_agents.append(value.casefold())
        elif field in {"allow", "disallow"}:
            group_has_rules = True
            if "*" in current_agents:
                wildcard_rules.append((field, unquote(value)))
        elif field == "sitemap":
            sitemaps.append(value)

    errors: list[str] = []
    target = baseurl + "/"
    matching = [rule for rule in wildcard_rules if rule[1] and target.startswith(rule[1])]
    if matching:
        longest = max(len(path) for _, path in matching)
        decisive = [field for field, path in matching if len(path) == longest]
        if "allow" not in decisive:
            errors.append(f"robots.txt disallows repository documentation: {target}")

    expected_sitemap_path = baseurl + "/sitemap.xml"
    wrong_origin_sitemaps = [
        value
        for value in sitemaps
        if unquote(urlsplit(value).path) == expected_sitemap_path
        and not _on_expected_origin(value, expected_origin)
    ]
    errors.extend(
        f"robots.txt sitemap is not on the production origin: {value}"
        for value in wrong_origin_sitemaps
    )
    if not any(
        urlsplit(value).scheme in {"http", "https"}
        and urlsplit(value).netloc
        and _on_expected_origin(value, expected_origin)
        and unquote(urlsplit(value).path) == expected_sitemap_path
        for value in sitemaps
    ):
        errors.append(
            f"robots.txt does not declare the repository sitemap: {expected_sitemap_path}"
        )
    return errors


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.urls: list[str] = []
        self.title = ""
        self._in_title = False
        self.meta: dict[tuple[str, str], str] = {}
        self.canonical = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if identifier := values.get("id"):
            self.ids.add(identifier)
        for attribute in ("href", "src"):
            if target := values.get(attribute):
                self.urls.append(target)
        if srcset := values.get("srcset"):
            self.urls.extend(_srcset_urls(srcset))
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            if name := values.get("name"):
                self.meta[("name", name.casefold())] = values.get("content", "")
            if prop := values.get("property"):
                self.meta[("property", prop.casefold())] = values.get("content", "")
        elif tag == "link" and values.get("rel", "").casefold() == "canonical":
            self.canonical = values.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _page_for_url(site: Path, current: Path, target: str, baseurl: str) -> Path | None:
    parsed = urlsplit(target)
    if parsed.scheme in {"http", "https", "mailto", "tel"} or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if path.startswith(baseurl + "/"):
        path = path[len(baseurl) + 1 :]
        candidate = site / path
    elif path in {baseurl, baseurl + "/"}:
        candidate = site
    elif path.startswith("/"):
        return None
    elif not path:
        candidate = current
    else:
        candidate = current.parent / path
    if path.endswith("/"):
        candidate /= "index.html"
    if candidate.suffix == "":
        candidate /= "index.html"
    return candidate.resolve()


def validate_site(
    site: Path,
    *,
    baseurl: str,
    expected_source_sha: str,
    expected_origin: str = _PRODUCTION_ORIGIN,
) -> list[str]:
    """Return stable validation errors for one rendered site tree."""
    site = site.resolve()
    errors: list[str] = []
    html_files = sorted(site.rglob("*.html"), key=lambda path: path.relative_to(site).as_posix())
    if not html_files:
        return ["rendered site contains no HTML pages"]
    documents: dict[Path, _DocumentParser] = {}
    for page in html_files:
        parser = _DocumentParser()
        parser.feed(page.read_text(encoding="utf-8"))
        documents[page.resolve()] = parser
        relative = page.relative_to(site).as_posix()
        required = {
            "title": parser.title.strip(),
            "description": parser.meta.get(("name", "description"), ""),
            "canonical URL": parser.canonical,
            "Open Graph title": parser.meta.get(("property", "og:title"), ""),
            "Open Graph description": parser.meta.get(("property", "og:description"), ""),
            "Open Graph URL": parser.meta.get(("property", "og:url"), ""),
        }
        for label, value in required.items():
            if not value.strip():
                errors.append(f"{relative}: missing {label}")
        for label, value in (
            ("canonical URL", parser.canonical),
            ("Open Graph URL", parser.meta.get(("property", "og:url"), "")),
        ):
            parsed_url = urlsplit(value)
            if value and (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or not _within_baseurl(value, baseurl)
            ):
                errors.append(f"{relative}: {label} is outside repository base URL: {value}")
            elif value and not _on_expected_origin(value, expected_origin):
                errors.append(f"{relative}: {label} is not on the production origin: {value}")
            expected_route = _rendered_route(page, site, baseurl)
            if value and unquote(parsed_url.path) != expected_route:
                errors.append(
                    f"{relative}: {label} does not match rendered route {expected_route}: {value}"
                )
        open_graph_url = parser.meta.get(("property", "og:url"), "")
        if parser.canonical and open_graph_url and parser.canonical != open_graph_url:
            errors.append(f"{relative}: canonical URL and Open Graph URL do not match")
        marker = parser.meta.get(("name", "ancestryllm-source-commit"), "")
        if marker != expected_source_sha:
            errors.append(
                f"{relative}: source revision is {marker or '<missing>'}, expected {expected_source_sha}"
            )

    for page, document in documents.items():
        relative = page.relative_to(site).as_posix()
        for target in document.urls:
            parsed = urlsplit(target)
            if (
                not parsed.scheme
                and not parsed.netloc
                and parsed.path.startswith("/")
                and not _within_baseurl(target, baseurl)
            ):
                errors.append(
                    f"{relative}: rendered target is outside repository base URL: {target}"
                )
                continue
            candidate = _page_for_url(site, page, target, baseurl)
            if candidate is None:
                continue
            try:
                candidate.relative_to(site)
            except ValueError:
                errors.append(f"{relative}: unsafe rendered target: {target}")
                continue
            if not candidate.exists():
                errors.append(f"{relative}: broken rendered target: {target}")
                continue
            if parsed.fragment and candidate.suffix == ".html":
                target_document = documents.get(candidate)
                if (
                    target_document is not None
                    and unquote(parsed.fragment) not in target_document.ids
                ):
                    errors.append(f"{relative}: missing rendered anchor: {target}")

    errors.extend(_validate_sitemap(site, html_files, baseurl, expected_origin))
    errors.extend(_validate_robots(site, baseurl, expected_origin))
    return sorted(set(errors))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--baseurl", default="/AncestryLLM")
    parser.add_argument("--expected-origin", default=_PRODUCTION_ORIGIN)
    parser.add_argument("--expected-source-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_site(
        args.site,
        baseurl=args.baseurl.rstrip("/"),
        expected_source_sha=args.expected_source_sha,
        expected_origin=args.expected_origin.rstrip("/"),
    )
    if errors:
        for error in errors:
            print(f"rendered-docs: {error}", file=sys.stderr)
        return 1
    print("rendered-docs: artifact is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
