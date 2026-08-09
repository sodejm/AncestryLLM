"""Tests for the trusted external documentation link checker."""

from __future__ import annotations

import importlib.util
import io
import sys
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "check_external_doc_links.py"
sys.path.insert(0, str(_SCRIPT.parent))
_SPEC = importlib.util.spec_from_file_location("check_external_doc_links", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
external_links = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = external_links
_SPEC.loader.exec_module(external_links)


def test_collect_external_links_ignores_code_and_deduplicates(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "Home.md").write_text(
        "[one](https://example.com/path?q=1#part)\n"
        "`[code](https://ignored.example)`\n"
        "```markdown\n[block](https://ignored.example/block)\n```\n"
        "![image](https://example.com/image.png)\n"
        "[again](https://example.com/path?q=1#part)\n",
        encoding="utf-8",
    )

    assert external_links.collect_external_links(docs) == {
        "https://example.com/image.png",
        "https://example.com/path?q=1#part",
    }


def test_collect_external_links_includes_reference_autolink_and_html_forms(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "Home.md").write_text(
        "[Provider docs][provider]\n"
        "<https://autolink.example/docs>\n"
        '<a href="https://html.example/guide">HTML guide</a>\n'
        '<img src="https://html.example/logo.svg">\n'
        '[provider]: https://reference.example/docs "Provider docs"\n'
        "```markdown\n"
        "[ignored]: https://ignored.example/docs\n"
        "<https://ignored.example/autolink>\n"
        "```\n",
        encoding="utf-8",
    )

    assert external_links.collect_external_links(docs) == {
        "https://autolink.example/docs",
        "https://html.example/guide",
        "https://html.example/logo.svg",
        "https://reference.example/docs",
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:secret@example.com/",
        "ftp://example.com/file",
    ],
)
def test_assert_public_url_rejects_unsafe_destinations(url: str) -> None:
    with pytest.raises(external_links.ExternalLinkError):
        external_links.assert_public_url(url)


def test_validate_exception_records_requires_owner_reason_and_future_expiry() -> None:
    links = {"https://example.com/retired"}
    errors = external_links.validate_exception_records(
        [
            {
                "url": "https://example.com/retired",
                "owner": "docs-maintainers",
                "reason": "Provider documentation is temporarily unavailable.",
                "expires": "2026-08-08",
            },
            {
                "url": "https://example.com/missing",
                "owner": "",
                "reason": "",
                "expires": "not-a-date",
            },
        ],
        links,
        today=date(2026, 8, 9),
    )

    messages = [error.message for error in errors]
    assert any("expired" in message for message in messages)
    assert any("owner" in message for message in messages)
    assert any("reason" in message for message in messages)
    assert any("expires" in message for message in messages)
    assert any("not present in canonical documentation" in message for message in messages)


def test_get_fallback_network_error_uses_non_http_retry_delay(monkeypatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def request(url: str, *, method: str, limiter: object) -> None:
        del limiter
        calls.append(method)
        if method == "HEAD":
            raise HTTPError(url, 405, "Method Not Allowed", {}, None)
        raise URLError("connection reset")

    monkeypatch.setattr(external_links, "_request", request)
    monkeypatch.setattr(external_links.time, "sleep", sleeps.append)

    issue = external_links.check_external_url(
        "https://example.com/unavailable",
        limiter=external_links._HostRateLimiter(0),
        attempts=2,
    )

    assert issue is not None
    assert "connection reset" in issue.message
    assert calls == ["HEAD", "GET", "HEAD", "GET"]
    assert sleeps == [1.0]


def test_terminal_http_error_response_is_closed(monkeypatch) -> None:
    response = io.BytesIO(b"not found")
    failure = HTTPError("https://example.com/missing", 404, "Not Found", {}, response)

    def request(url: str, *, method: str, limiter: object) -> None:
        del url, method, limiter
        raise failure

    monkeypatch.setattr(external_links, "_request", request)

    issue = external_links.check_external_url(
        "https://example.com/missing",
        limiter=external_links._HostRateLimiter(0),
    )

    assert issue is not None
    assert "HTTP Error 404" in issue.message
    assert response.closed
