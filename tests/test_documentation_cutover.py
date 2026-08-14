"""Contracts for the final Diataxis documentation publishing cutover."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_documentation_cutover.py"
SOURCE_SHA = "a" * 40
sys.path.insert(0, str(SCRIPT.parent))


def _load_cutover():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("verify_documentation_cutover", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_docs(source: Path) -> None:
    source.mkdir()
    (source / "Home.md").write_text(
        "# Home\n\n[Guide](Guide.md)\n\n[External](https://example.com/reference)\n",
        encoding="utf-8",
    )
    (source / "Guide.md").write_text(
        "# Guide\n\n[Home](Home.md#home)\n",
        encoding="utf-8",
    )
    (source / "_Sidebar.md").write_text(
        "- [Home](Home.md)\n- [Guide](Guide.md)\n",
        encoding="utf-8",
    )


def _write_exceptions(path: Path, payload: str = '{"exceptions": []}\n') -> None:
    path.write_text(payload, encoding="utf-8")


def test_cutover_repeats_pages_and_wiki_staging_without_network(tmp_path: Path) -> None:
    cutover = _load_cutover()
    source = tmp_path / "docs"
    exceptions = tmp_path / "external-link-exceptions.json"
    _write_docs(source)
    _write_exceptions(exceptions)

    result = cutover.verify_documentation_cutover(
        source=source,
        source_sha=SOURCE_SHA,
        exceptions_path=exceptions,
    )

    assert result == cutover.DocumentationCutoverResult(
        page_count=2,
        asset_count=0,
        wiki_file_count=3,
        external_link_count=1,
        exception_count=0,
    )


def test_cutover_rejects_an_invalid_source_revision(tmp_path: Path) -> None:
    cutover = _load_cutover()

    with pytest.raises(cutover.DocumentationCutoverError) as caught:
        cutover.verify_documentation_cutover(
            source=tmp_path / "docs",
            source_sha="HEAD",
            exceptions_path=tmp_path / "exceptions.json",
        )

    assert caught.value.code == "DOCSCUTOVER_SHA_INVALID"


def test_cutover_rejects_nondeterministic_pages_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cutover = _load_cutover()
    source = tmp_path / "docs"
    exceptions = tmp_path / "exceptions.json"
    _write_docs(source)
    _write_exceptions(exceptions)
    real_prepare = cutover.prepare_pages_source
    calls = 0

    def varying_prepare(source_path: Path, destination: Path, *, source_sha: str):
        nonlocal calls
        calls += 1
        result = real_prepare(source_path, destination, source_sha=source_sha)
        if calls == 2:
            (destination / "Guide.md").write_text("changed on the second pass\n", encoding="utf-8")
        return result

    monkeypatch.setattr(cutover, "prepare_pages_source", varying_prepare)

    with pytest.raises(cutover.DocumentationCutoverError) as caught:
        cutover.verify_documentation_cutover(
            source=source,
            source_sha=SOURCE_SHA,
            exceptions_path=exceptions,
        )

    assert caught.value.code == "DOCSCUTOVER_PAGES_NONDETERMINISTIC"


def test_cutover_rejects_a_non_idempotent_wiki_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cutover = _load_cutover()
    source = tmp_path / "docs"
    exceptions = tmp_path / "exceptions.json"
    _write_docs(source)
    _write_exceptions(exceptions)
    real_sync = cutover.sync_wiki_docs
    calls = 0

    def non_idempotent_sync(source_path: Path, destination: Path):
        nonlocal calls
        calls += 1
        result = real_sync(source_path, destination)
        if calls == 2:
            return type(result)(copied=("forced-change.md",), removed=())
        return result

    monkeypatch.setattr(cutover, "sync_wiki_docs", non_idempotent_sync)

    with pytest.raises(cutover.DocumentationCutoverError) as caught:
        cutover.verify_documentation_cutover(
            source=source,
            source_sha=SOURCE_SHA,
            exceptions_path=exceptions,
        )

    assert caught.value.code == "DOCSCUTOVER_WIKI_NOT_IDEMPOTENT"


def test_cutover_rejects_unowned_external_link_exceptions(tmp_path: Path) -> None:
    cutover = _load_cutover()
    source = tmp_path / "docs"
    exceptions = tmp_path / "exceptions.json"
    _write_docs(source)
    _write_exceptions(
        exceptions,
        '{"exceptions": [{"url": "https://example.com/reference", '
        '"reason": "temporary", "expires": "2999-01-01"}]}\n',
    )

    with pytest.raises(cutover.DocumentationCutoverError) as caught:
        cutover.verify_documentation_cutover(
            source=source,
            source_sha=SOURCE_SHA,
            exceptions_path=exceptions,
        )

    assert caught.value.code == "DOCSCUTOVER_EXCEPTIONS_INVALID"


def test_cutover_cli_emits_only_a_stable_error_code(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    cutover = _load_cutover()
    private_path = tmp_path / "private-user-path"
    exceptions = tmp_path / "exceptions.json"
    _write_exceptions(exceptions)

    result = cutover.main(
        [
            "--source",
            str(private_path),
            "--source-sha",
            SOURCE_SHA,
            "--exceptions",
            str(exceptions),
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "DOCSCUTOVER_PAGES_FAILED\n"
    assert str(private_path) not in captured.err
