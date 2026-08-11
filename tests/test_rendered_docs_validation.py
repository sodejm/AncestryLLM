"""Tests for generated Pages artifact validation and deployment safety."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.error import URLError

import pytest
from pytest import MonkeyPatch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rendered_docs = _load("validate_rendered_docs")
pages_smoke = _load("smoke_pages_deployment")


def _write_page(
    path: Path,
    *,
    sha: str,
    link: str = "",
    extra_markup: str = "",
    canonical: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if canonical is None:
        route = path.name
        if route == "index.html":
            route = ""
        canonical = f"https://sodejm.github.io/AncestryLLM/{route}"
    path.write_text(
        f"""<!doctype html>
<html><head>
<title>Documentation</title>
<meta name="description" content="Description">
<meta name="ancestryllm-source-commit" content="{sha}">
<meta property="og:title" content="Documentation">
<meta property="og:description" content="Description">
<meta property="og:url" content="{canonical}">
<link rel="canonical" href="{canonical}">
</head><body><main id="topic"><a href="{link}">next</a>{extra_markup}</main></body></html>
""",
        encoding="utf-8",
    )


def _write_discovery_files(site: Path, routes: tuple[str, ...]) -> None:
    locations = "".join(
        f"<url><loc>https://sodejm.github.io{route}</loc></url>" for route in routes
    )
    (site / "robots.txt").write_text(
        "User-agent: *\n"
        "Allow: /AncestryLLM/\n"
        "Sitemap: https://sodejm.github.io/AncestryLLM/sitemap.xml\n",
        encoding="utf-8",
    )
    (site / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locations}</urlset>\n',
        encoding="utf-8",
    )


def test_rendered_validation_accepts_metadata_routes_and_anchors(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _write_page(site / "index.html", sha="abc123", link="/AncestryLLM/Guide.html#topic")
    _write_page(
        site / "Guide.html",
        sha="abc123",
        canonical="https://sodejm.github.io/AncestryLLM/Guide.html",
    )
    _write_discovery_files(site, ("/AncestryLLM/", "/AncestryLLM/Guide.html"))

    assert (
        rendered_docs.validate_site(site, baseurl="/AncestryLLM", expected_source_sha="abc123")
        == []
    )


def test_rendered_validation_rejects_wrong_revision_and_missing_anchor(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _write_page(site / "index.html", sha="wrong", link="/AncestryLLM/Guide.html#missing")
    _write_page(
        site / "Guide.html",
        sha="wrong",
        canonical="https://sodejm.github.io/AncestryLLM/Guide.html",
    )
    _write_discovery_files(site, ("/AncestryLLM/", "/AncestryLLM/Guide.html"))

    errors = rendered_docs.validate_site(site, baseurl="/AncestryLLM", expected_source_sha="abc123")

    assert any("source revision is wrong" in error for error in errors)
    assert any("missing rendered anchor" in error for error in errors)


def test_rendered_validation_checks_srcset_and_repository_url_confinement(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    _write_page(
        site / "index.html",
        sha="abc123",
        link="/outside-repository/Guide.html",
        extra_markup=(
            '<img srcset="/AncestryLLM/logo-small.svg 1x, /outside-repository/logo-large.svg 2x">'
        ),
        canonical="https://sodejm.github.io/outside-repository/",
    )
    (site / "logo-small.svg").write_text("<svg/>\n", encoding="utf-8")
    _write_discovery_files(site, ("/AncestryLLM/",))

    errors = rendered_docs.validate_site(site, baseurl="/AncestryLLM", expected_source_sha="abc123")

    assert any("outside repository base URL" in error and "Guide.html" in error for error in errors)
    assert any(
        "outside repository base URL" in error and "logo-large.svg" in error for error in errors
    )
    assert any("canonical URL is outside repository base URL" in error for error in errors)
    assert any("Open Graph URL is outside repository base URL" in error for error in errors)


def test_rendered_validation_requires_complete_sitemap_and_permissive_robots(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    _write_page(site / "index.html", sha="abc123")
    _write_page(
        site / "Guide.html",
        sha="abc123",
        canonical="https://sodejm.github.io/AncestryLLM/Guide.html",
    )
    (site / "robots.txt").write_text(
        "User-agent: *\n"
        "Disallow: /AncestryLLM/\n"
        "Sitemap: https://sodejm.github.io/elsewhere/sitemap.xml\n",
        encoding="utf-8",
    )
    (site / "sitemap.xml").write_text(
        "<urlset><url><loc>https://sodejm.github.io/AncestryLLM/</loc></url>"
        "<url><loc>https://sodejm.github.io/elsewhere/Guide.html</loc></url></urlset>\n",
        encoding="utf-8",
    )

    errors = rendered_docs.validate_site(site, baseurl="/AncestryLLM", expected_source_sha="abc123")

    assert any(
        "sitemap is missing rendered route" in error and "Guide.html" in error for error in errors
    )
    assert any("sitemap URL is outside repository base URL" in error for error in errors)
    assert any("robots.txt disallows repository documentation" in error for error in errors)
    assert any("robots.txt does not declare the repository sitemap" in error for error in errors)


def test_smoke_refuses_non_production_host_without_network_access() -> None:
    assert pages_smoke.smoke("https://example.com/AncestryLLM/", "abc123") == [
        "refusing non-production Pages URL: https://example.com/AncestryLLM/"
    ]


def test_rendered_validation_resolves_fragment_only_links_against_current_page(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    _write_page(site / "index.html", sha="abc123", extra_markup='<div id="missing"></div>')
    _write_page(
        site / "Guide.html",
        sha="abc123",
        link="?view=compact#missing",
        canonical="https://sodejm.github.io/AncestryLLM/Guide.html",
    )
    _write_discovery_files(site, ("/AncestryLLM/", "/AncestryLLM/Guide.html"))

    errors = rendered_docs.validate_site(site, baseurl="/AncestryLLM", expected_source_sha="abc123")

    assert any("Guide.html: missing rendered anchor: ?view=compact#missing" in e for e in errors)


def test_rendered_validation_accepts_present_same_page_fragment(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _write_page(site / "index.html", sha="abc123")
    _write_page(
        site / "Guide.html",
        sha="abc123",
        link="#topic",
        canonical="https://sodejm.github.io/AncestryLLM/Guide.html",
    )
    _write_discovery_files(site, ("/AncestryLLM/", "/AncestryLLM/Guide.html"))

    assert (
        rendered_docs.validate_site(site, baseurl="/AncestryLLM", expected_source_sha="abc123")
        == []
    )


def test_rendered_validation_rejects_root_metadata_on_child_page(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _write_page(site / "index.html", sha="abc123")
    _write_page(
        site / "Guide.html",
        sha="abc123",
        canonical="https://sodejm.github.io/AncestryLLM/",
    )
    _write_discovery_files(site, ("/AncestryLLM/", "/AncestryLLM/Guide.html"))

    errors = rendered_docs.validate_site(site, baseurl="/AncestryLLM", expected_source_sha="abc123")

    assert any("Guide.html: canonical URL does not match rendered route" in e for e in errors)
    assert any("Guide.html: Open Graph URL does not match rendered route" in e for e in errors)


def test_rendered_validation_requires_production_origin_for_discovery_metadata(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    wrong_origin = "https://wrong.example/AncestryLLM/"
    _write_page(site / "index.html", sha="abc123", canonical=wrong_origin)
    (site / "robots.txt").write_text(
        "User-agent: *\n"
        "Allow: /AncestryLLM/\n"
        "Sitemap: https://wrong.example/AncestryLLM/sitemap.xml\n",
        encoding="utf-8",
    )
    (site / "sitemap.xml").write_text(
        "<urlset><url><loc>https://wrong.example/AncestryLLM/</loc></url></urlset>\n",
        encoding="utf-8",
    )

    errors = rendered_docs.validate_site(site, baseurl="/AncestryLLM", expected_source_sha="abc123")

    assert any("canonical URL is not on the production origin" in error for error in errors)
    assert any("Open Graph URL is not on the production origin" in error for error in errors)
    assert any("sitemap URL is not on the production origin" in error for error in errors)
    assert any("robots.txt sitemap is not on the production origin" in error for error in errors)


def test_smoke_fetch_retries_transient_failures_with_bounded_backoff(
    monkeypatch: MonkeyPatch,
) -> None:
    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"ready"

        def geturl(self) -> str:
            return "https://sodejm.github.io/AncestryLLM/"

    outcomes: list[object] = [*[URLError("not ready") for _ in range(5)], Response()]
    sleeps: list[int] = []

    def fake_urlopen(*_args: object, **_kwargs: object) -> object:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(pages_smoke, "urlopen", fake_urlopen)
    monkeypatch.setattr(pages_smoke.time, "sleep", sleeps.append)

    assert pages_smoke._fetch("https://sodejm.github.io/AncestryLLM/") == "ready"
    assert sleeps == [1, 2, 4, 8, 15]
    assert sum(sleeps) == 30


@pytest.mark.parametrize(
    "redirected_url",
    [
        "https://example.com/AncestryLLM/",
        "https://sodejm.github.io/",
    ],
)
def test_smoke_fetch_rejects_redirects_outside_requested_route(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    redirected_url: str,
) -> None:
    del tmp_path

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"unexpected"

        def geturl(self) -> str:
            return redirected_url

    monkeypatch.setattr(pages_smoke, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(RuntimeError, match="redirected outside requested route"):
        pages_smoke._fetch("https://sodejm.github.io/AncestryLLM/")
