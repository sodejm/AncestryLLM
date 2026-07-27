"""Focused cancellation boundaries for incremental GEDCOM synchronization."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import ancestryllm.gedcom.incremental as incremental
from ancestryllm.core.cancellation import (
    CancellationError,
    CancellationToken,
    bind_cancellation_token,
)


class _ParsedLine:
    def __init__(self, line: str) -> None:
        parts = line.split(maxsplit=2)
        self.level = int(parts[0])
        self.tag = parts[1]
        self.value = parts[2] if len(parts) == 3 else ""


class _Core:
    @staticmethod
    def parse_gedcom_line(line: str) -> _ParsedLine:
        return _ParsedLine(line)

    @staticmethod
    def normalise_gedcom_date(value: str) -> str:
        return value

    @staticmethod
    def _normalise_country(value: str) -> str:
        return value.casefold()


def test_relative_line_traversal_checks_for_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def cancel_during_traversal() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CancellationError("cancel traversal")

    monkeypatch.setattr(incremental, "cancellation_checkpoint", cancel_during_traversal)

    with pytest.raises(CancellationError, match="cancel traversal"):
        incremental._relative_lines(
            ("1 NAME Fictional /Person/", "2 GIVN Fictional", "2 SURN Person"),
            _Core,
        )


def test_people_matching_checks_before_expensive_source_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        incremental,
        "cancellation_checkpoint",
        lambda: (_ for _ in ()).throw(CancellationError("cancel matching")),
    )
    core = SimpleNamespace(
        _individual_from_record=lambda _record: pytest.fail("record traversal must not start"),
        enrich_relationship_context=lambda *_args: pytest.fail("enrichment must not start"),
    )
    source = SimpleNamespace(records=[])

    with pytest.raises(CancellationError, match="cancel matching"):
        incremental._match_people(
            (source,),
            (),
            {"next_ids": {}},
            core,
            incremental.SyncStats(),
        )


def test_staged_artifact_write_honours_preexisting_cancellation(tmp_path: Path) -> None:
    destination = tmp_path / "manifest.json"
    token = CancellationToken()
    token.request()

    with bind_cancellation_token(token), pytest.raises(CancellationError):
        incremental._write_bytes(destination, b'{"fictional": true}\n')

    assert not destination.exists()
