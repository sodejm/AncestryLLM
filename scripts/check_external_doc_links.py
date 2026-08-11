#!/usr/bin/env python3
"""Check canonical documentation's external links in a trusted network context."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from docs_linking import split_destination
from rewrite_wiki_links import rewrite_markdown_link_destinations

_MAX_REDIRECTS = 5
_MAX_WORKERS = 8
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_FENCE = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(?P<marker>`+).*?(?P=marker)")
_REFERENCE_DEFINITION = re.compile(
    r"^ {0,3}\[[^]\n]+\]:[ \t]*(?P<target><[^>\n]+>|\S+)", re.IGNORECASE
)
_AUTOLINK = re.compile(r"<(?P<url>https?://[^<>\s]+)>", re.IGNORECASE)
_HTML_URL = re.compile(
    r"\b(?:href|src)[ \t]*=[ \t]*(?P<quote>[\"'])(?P<url>https?://.*?)(?P=quote)",
    re.IGNORECASE,
)


class ExternalLinkError(ValueError):
    """Raised when an external link is unsafe or cannot be checked."""


@dataclass(frozen=True)
class ExternalLinkIssue:
    """One deterministic external-link or exception-policy error."""

    message: str


def _addresses(hostname: str, port: int) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            raise ExternalLinkError(
                f"cannot resolve external-link host {hostname}: {error}"
            ) from error
        return {ipaddress.ip_address(record[4][0]) for record in records}
    return {literal}


def assert_public_url(url: str, *, resolve_dns: bool = False) -> None:
    """Reject credentials, non-HTTP URLs, and private or special-use destinations."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ExternalLinkError(f"unsupported external URL: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise ExternalLinkError(f"credentials are forbidden in external URL: {url}")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise ExternalLinkError(f"invalid port in external URL: {url}") from error
    if port not in {80, 443}:
        raise ExternalLinkError(f"non-standard port is forbidden in external URL: {url}")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ExternalLinkError(f"local host is forbidden in external URL: {url}")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ExternalLinkError(f"non-public address is forbidden in external URL: {url}")
    if resolve_dns:
        addresses = _addresses(hostname, port)
        unsafe = sorted(str(address) for address in addresses if not address.is_global)
        if not addresses or unsafe:
            detail = ", ".join(unsafe) if unsafe else "no addresses"
            raise ExternalLinkError(
                f"external URL resolves to a non-public address ({detail}): {url}"
            )


def collect_external_links(source: Path) -> set[str]:
    """Collect unique rendered HTTP(S) links outside code spans and fences."""
    links: set[str] = set()
    for page in sorted(source.rglob("*.md"), key=lambda path: path.relative_to(source).as_posix()):
        if not page.is_file() or page.is_symlink():
            continue

        def collect(destination: str) -> str:
            target, _title = split_destination(destination)
            if urlsplit(target).scheme.casefold() in {"http", "https"}:
                links.add(target)
            return destination

        markdown = page.read_text(encoding="utf-8")
        rewrite_markdown_link_destinations(markdown, collect, include_images=True)

        fence_marker: str | None = None
        for line in markdown.splitlines():
            fence = _FENCE.match(line)
            if fence_marker is None:
                if fence is not None:
                    fence_marker = fence.group("marker")
                    continue
            else:
                if (
                    fence is not None
                    and fence.group("marker")[0] == fence_marker[0]
                    and len(fence.group("marker")) >= len(fence_marker)
                ):
                    fence_marker = None
                continue

            visible = _INLINE_CODE.sub("", line)
            if definition := _REFERENCE_DEFINITION.match(visible):
                target = definition.group("target").removeprefix("<").removesuffix(">")
                if urlsplit(target).scheme.casefold() in {"http", "https"}:
                    links.add(target)
            links.update(match.group("url") for match in _AUTOLINK.finditer(visible))
            links.update(match.group("url") for match in _HTML_URL.finditer(visible))
    return links


def validate_exception_records(
    records: object,
    links: set[str],
    *,
    today: date | None = None,
) -> list[ExternalLinkIssue]:
    """Validate owned, reasoned, expiring exceptions against canonical links."""
    if not isinstance(records, list):
        return [ExternalLinkIssue("external-link exceptions must be a list")]
    current = today or datetime.now(UTC).date()
    errors: list[ExternalLinkIssue] = []
    seen: set[str] = set()
    for position, record in enumerate(records, start=1):
        label = f"external-link exception {position}"
        if not isinstance(record, Mapping):
            errors.append(ExternalLinkIssue(f"{label} must be an object"))
            continue
        url = record.get("url")
        owner = record.get("owner")
        reason = record.get("reason")
        expires = record.get("expires")
        if not isinstance(url, str) or not url:
            errors.append(ExternalLinkIssue(f"{label} is missing url"))
            continue
        if url in seen:
            errors.append(ExternalLinkIssue(f"duplicate external-link exception: {url}"))
        seen.add(url)
        if url not in links:
            errors.append(
                ExternalLinkIssue(
                    f"external-link exception is not present in canonical documentation: {url}"
                )
            )
        if not isinstance(owner, str) or not owner.strip():
            errors.append(ExternalLinkIssue(f"{label} is missing owner: {url}"))
        if not isinstance(reason, str) or not reason.strip():
            errors.append(ExternalLinkIssue(f"{label} is missing reason: {url}"))
        if not isinstance(expires, str):
            errors.append(ExternalLinkIssue(f"{label} is missing expires date: {url}"))
        else:
            try:
                expiry = date.fromisoformat(expires)
            except ValueError:
                errors.append(ExternalLinkIssue(f"{label} has invalid expires date: {url}"))
            else:
                if expiry < current:
                    errors.append(
                        ExternalLinkIssue(f"external-link exception expired {expires}: {url}")
                    )
        try:
            assert_public_url(url)
        except ExternalLinkError as error:
            errors.append(ExternalLinkIssue(str(error)))
    return sorted(set(errors), key=lambda error: error.message)


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.redirects = 0

    def redirect_request(  # type: ignore[no-untyped-def]
        self, request, file_pointer, code, message, headers, new_url
    ):
        self.redirects += 1
        if self.redirects > _MAX_REDIRECTS:
            raise ExternalLinkError(f"too many redirects from {request.full_url}")
        destination = urljoin(request.full_url, new_url)
        assert_public_url(destination, resolve_dns=True)
        return super().redirect_request(request, file_pointer, code, message, headers, destination)


class _HostRateLimiter:
    def __init__(self, minimum_delay: float) -> None:
        self.minimum_delay = minimum_delay
        self._lock = threading.Lock()
        self._last_request: dict[str, float] = {}

    def wait(self, url: str) -> None:
        hostname = urlsplit(url).hostname or ""
        with self._lock:
            elapsed = time.monotonic() - self._last_request.get(hostname, 0.0)
            if elapsed < self.minimum_delay:
                time.sleep(self.minimum_delay - elapsed)
            self._last_request[hostname] = time.monotonic()


def _retry_delay(error: HTTPError, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            try:
                return min(
                    max(parsedate_to_datetime(retry_after).timestamp() - time.time(), 0.0), 30.0
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return float(2**attempt)


def _request(url: str, *, method: str, limiter: _HostRateLimiter) -> None:
    assert_public_url(url, resolve_dns=True)
    limiter.wait(url)
    request = Request(  # noqa: S310 - URL is restricted and SSRF-validated above
        url, method=method, headers={"User-Agent": "AncestryLLM-docs-links/1"}
    )
    opener = build_opener(_SafeRedirectHandler())
    with opener.open(request, timeout=15) as response:
        if response.status >= 400:
            raise HTTPError(url, response.status, response.reason, response.headers, None)


def check_external_url(
    url: str,
    *,
    limiter: _HostRateLimiter,
    attempts: int = 3,
) -> ExternalLinkIssue | None:
    """Check one public URL with bounded retries and a GET fallback for HEAD rejection."""
    try:
        assert_public_url(url)
    except ExternalLinkError as error:
        return ExternalLinkIssue(str(error))
    for attempt in range(attempts):
        try:
            _request(url, method="HEAD", limiter=limiter)
            return None
        except HTTPError as head_error:
            failure: ExternalLinkError | HTTPError | URLError | TimeoutError = head_error
            if head_error.code in {405, 501}:
                head_error.close()
                try:
                    _request(url, method="GET", limiter=limiter)
                    return None
                except (ExternalLinkError, HTTPError, URLError, TimeoutError) as get_error:
                    failure = get_error
            retryable = not isinstance(failure, HTTPError) or failure.code in _RETRYABLE_STATUS
            message = f"external link failed: {url}: {failure}"
            delay = (
                _retry_delay(failure, attempt)
                if isinstance(failure, HTTPError)
                else float(2**attempt)
            )
            if isinstance(failure, HTTPError):
                failure.close()
            if not retryable or attempt == attempts - 1:
                return ExternalLinkIssue(message)
            time.sleep(delay)
        except (ExternalLinkError, URLError, TimeoutError) as error:
            if attempt == attempts - 1:
                return ExternalLinkIssue(f"external link failed: {url}: {error}")
            time.sleep(float(2**attempt))
    raise AssertionError("unreachable")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--minimum-delay", type=float, default=0.2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.workers <= _MAX_WORKERS:
        print(f"external-links: workers must be between 1 and {_MAX_WORKERS}", file=sys.stderr)
        return 2
    links = collect_external_links(args.source)
    try:
        document = json.loads(args.exceptions.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"external-links: invalid exceptions file: {error}", file=sys.stderr)
        return 1
    records = document.get("exceptions") if isinstance(document, Mapping) else None
    policy_errors = validate_exception_records(records, links)
    if policy_errors:
        for issue in policy_errors:
            print(f"external-links: {issue.message}", file=sys.stderr)
        return 1
    if not isinstance(records, list):
        raise AssertionError("validated exception records must be a list")
    exceptions = {record["url"] for record in records if isinstance(record, Mapping)}
    limiter = _HostRateLimiter(max(args.minimum_delay, 0.0))
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = executor.map(
            lambda url: check_external_url(url, limiter=limiter), sorted(links - exceptions)
        )
    errors = sorted((result for result in results if result is not None), key=lambda e: e.message)
    if errors:
        for issue in errors:
            print(f"external-links: {issue.message}", file=sys.stderr)
        return 1
    print(f"external-links: checked {len(links) - len(exceptions)} URL(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
