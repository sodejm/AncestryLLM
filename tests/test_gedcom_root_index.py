"""Private SQLite projection tests for GEDCOM root candidates."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest

from ancestryllm.application.operations import RootCandidate
from ancestryllm.gedcom.root_index import (
    RootIndexError,
    RootIndexPage,
    build_root_index,
    read_root_page,
    root_index_count,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _candidate(number: int, *, person_ref: str | None = None) -> RootCandidate:
    return RootCandidate(
        person_ref=person_ref or f"person:{number:04d}",
        reason_code="selectable",
        display_name=f"Person {number:04d}",
        source_identifier=f"@I{number}@",
        birth_date="1900",
        death_date="1980",
        relationship_summary="Fictional family",
    )


def _private_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    return directory


def test_build_streams_candidates_into_private_complete_index(tmp_path: Path) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"
    consumed: list[int] = []

    def candidates() -> Iterator[RootCandidate]:
        for number in range(3):
            consumed.append(number)
            yield _candidate(number)

    assert build_root_index(path, candidates(), max_bytes=1_000_000) == 3
    assert consumed == [0, 1, 2]
    assert root_index_count(path) == 3
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(directory.glob(".*.tmp*")) == []


def test_build_rejects_duplicate_person_refs_without_publishing(tmp_path: Path) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"

    with pytest.raises(RootIndexError, match="duplicate"):
        build_root_index(
            path,
            (_candidate(1), _candidate(2, person_ref="person:0001")),
            max_bytes=1_000_000,
        )

    assert not path.exists()
    assert list(directory.glob(".*.tmp*")) == []


def test_build_never_overwrites_an_existing_index(tmp_path: Path) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"
    path.write_bytes(b"existing")
    path.chmod(0o600)

    with pytest.raises(RootIndexError, match="already exists"):
        build_root_index(path, (_candidate(1),), max_bytes=1_000_000)

    assert path.read_bytes() == b"existing"


def test_build_requires_a_private_parent_directory(tmp_path: Path) -> None:
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)

    with pytest.raises(RootIndexError, match="private directory"):
        build_root_index(
            directory / "roots.sqlite3",
            (_candidate(1),),
            max_bytes=1_000_000,
        )


def test_read_empty_query_uses_stable_display_order_and_offsets(tmp_path: Path) -> None:
    path = _private_directory(tmp_path) / "roots.sqlite3"
    candidates = (
        _candidate(3),
        _candidate(1),
        _candidate(2),
    )
    build_root_index(path, candidates, max_bytes=1_000_000)

    first = read_root_page(path, limit=2)
    second = read_root_page(path, offset=first.next_offset or 0, limit=2)

    assert isinstance(first, RootIndexPage)
    assert [candidate.person_ref for candidate in first.candidates] == [
        "person:0001",
        "person:0002",
    ]
    assert first.total_count == 3
    assert first.next_offset == 2
    assert [candidate.person_ref for candidate in second.candidates] == ["person:0003"]
    assert second.total_count == 3
    assert second.next_offset is None


@pytest.mark.parametrize("query", ["person:0002", "@I2@"])
def test_read_exact_person_or_source_identifier_is_indexed_and_pageable(
    tmp_path: Path,
    query: str,
) -> None:
    path = _private_directory(tmp_path) / "roots.sqlite3"
    build_root_index(path, (_candidate(1), _candidate(2), _candidate(3)), max_bytes=1_000_000)

    page = read_root_page(path, query=query, limit=1)

    assert [candidate.person_ref for candidate in page.candidates] == ["person:0002"]
    assert page.total_count == 1
    assert page.next_offset is None


def test_read_substring_treats_sql_wildcards_literally(tmp_path: Path) -> None:
    path = _private_directory(tmp_path) / "roots.sqlite3"
    literal = RootCandidate(
        person_ref="person:literal",
        reason_code="selectable",
        display_name="A 100%_literal name",
        source_identifier="@IX@",
    )
    build_root_index(path, (_candidate(1), literal), max_bytes=1_000_000)

    page = read_root_page(path, query="%_literal")

    assert [candidate.person_ref for candidate in page.candidates] == ["person:literal"]
    assert page.total_count == 1
    assert page.next_offset is None


def test_read_substring_stops_after_4096_scanned_rows_and_resumes(tmp_path: Path) -> None:
    path = _private_directory(tmp_path) / "roots.sqlite3"

    def candidates() -> Iterator[RootCandidate]:
        for number in range(4097):
            candidate = _candidate(number)
            if number == 4096:
                candidate = RootCandidate(
                    person_ref=candidate.person_ref,
                    reason_code=candidate.reason_code,
                    display_name="ZZZZ Needle Person",
                    source_identifier=candidate.source_identifier,
                )
            yield candidate

    build_root_index(path, candidates(), max_bytes=4_000_000)

    first = read_root_page(path, query="Needle", limit=50)
    resumed = read_root_page(path, query="Needle", offset=first.next_offset or 0, limit=50)

    assert first.candidates == ()
    assert first.total_count is None
    assert first.next_offset == 4096
    assert [candidate.person_ref for candidate in resumed.candidates] == ["person:4096"]
    assert resumed.next_offset is None
    assert resumed.total_count is None


@pytest.mark.parametrize(
    ("offset", "limit"),
    [(-1, 50), (0, 0), (0, 101), (True, 50), (0, False)],
)
def test_read_rejects_invalid_page_bounds(
    tmp_path: Path,
    offset: int,
    limit: int,
) -> None:
    path = _private_directory(tmp_path) / "roots.sqlite3"
    build_root_index(path, (_candidate(1),), max_bytes=1_000_000)

    with pytest.raises(RootIndexError, match="page bounds"):
        read_root_page(path, offset=offset, limit=limit)
