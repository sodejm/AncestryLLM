#!/usr/bin/env python3
"""Smoke-test the trusted production Pages deployment and its source revision."""

from __future__ import annotations

import argparse
import re
import sys
import time
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from collections.abc import Sequence

_PRODUCTION_HOST = "sodejm.github.io"
_MARKER = re.compile(
    r'<meta\s+name=["\']ancestryllm-source-commit["\']\s+content=["\'](?P<sha>[^"\']+)',
    re.IGNORECASE,
)
_RETRY_DELAYS = (1, 2, 4, 8, 15)


def _same_route(requested_url: str, final_url: str) -> bool:
    requested = urlsplit(requested_url)
    final = urlsplit(final_url)
    try:
        requested_port = requested.port or (443 if requested.scheme == "https" else 80)
        final_port = final.port or (443 if final.scheme == "https" else 80)
    except ValueError:
        return False
    return (
        final.username is None
        and final.password is None
        and final.scheme.casefold() == requested.scheme.casefold()
        and (final.hostname or "").casefold() == (requested.hostname or "").casefold()
        and final_port == requested_port
        and final.path == requested.path
        and final.query == requested.query
    )


def _fetch(url: str, *, retry_delays: tuple[int, ...] = _RETRY_DELAYS) -> str:
    for attempt in range(len(retry_delays) + 1):
        try:
            request = Request(  # noqa: S310 - caller restricts the URL to the production host
                url, headers={"User-Agent": "AncestryLLM-docs-smoke/1"}
            )
            with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed production host
                final_url = response.geturl()
                if not isinstance(final_url, str) or not _same_route(url, final_url):
                    raise RuntimeError(f"{url}: redirected outside requested route: {final_url}")
                if response.status != 200:
                    raise RuntimeError(f"{url}: HTTP {response.status}")
                content = response.read()
                if not isinstance(content, bytes):
                    raise RuntimeError(f"{url}: response body is not bytes")
                return content.decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as error:
            if attempt == len(retry_delays):
                raise RuntimeError(f"{url}: {error}") from error
            time.sleep(retry_delays[attempt])
    raise AssertionError("unreachable")


def smoke(base_url: str, expected_source_sha: str) -> list[str]:
    """Return production smoke failures for a fixed GitHub Pages host."""
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or parsed.hostname != _PRODUCTION_HOST:
        return [f"refusing non-production Pages URL: {base_url}"]
    errors: list[str] = []
    for path in ("", "reference/CLI.html", "robots.txt", "sitemap.xml"):
        url = urljoin(base_url.rstrip("/") + "/", path)
        try:
            content = _fetch(url)
        except RuntimeError as error:
            errors.append(str(error))
            continue
        if path in {"", "reference/CLI.html"}:
            match = _MARKER.search(content)
            actual = match.group("sha") if match else "<missing>"
            if actual != expected_source_sha:
                errors.append(f"{url}: source revision is {actual}, expected {expected_source_sha}")
    return errors


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the smoke pages deployment command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://sodejm.github.io/AncestryLLM/")
    parser.add_argument("--expected-source-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the smoke pages deployment command and return its exit status."""
    args = build_parser().parse_args(argv)
    errors = smoke(args.base_url, args.expected_source_sha)
    if errors:
        for error in errors:
            print(f"pages-smoke: {error}", file=sys.stderr)
        return 1
    print("pages-smoke: production deployment matches the expected source revision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
