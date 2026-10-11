"""Private SQLite projection tests for GEDCOM root candidates."""

from __future__ import annotations

import platform
import sqlite3
import stat
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from ancestryllm.application.operations import RootCandidate
from ancestryllm.gedcom import _private_files, root_index
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


def test_build_requires_a_private_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    if platform.system() == "Windows":
        monkeypatch.setattr(
            _private_files,
            "_windows_path_is_private",
            lambda _path, *, directory: False,
        )
    else:
        directory.chmod(0o755)

    with pytest.raises(RootIndexError, match="private directory"):
        build_root_index(
            directory / "roots.sqlite3",
            (_candidate(1),),
            max_bytes=1_000_000,
        )


def test_windows_hardening_applies_native_acl_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "private"
    directory.mkdir()
    private_file = directory / "private.bin"
    private_file.write_bytes(b"private")
    calls: list[tuple[str, Path, bool]] = []

    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(
        _private_files,
        "_harden_windows_path",
        lambda path, *, directory: calls.append(("harden", path, directory)),
    )

    def validate(path: Path, *, directory: bool) -> bool:
        calls.append(("validate", path, directory))
        return calls[-2] == ("harden", path, directory)

    monkeypatch.setattr(_private_files, "_windows_path_is_private", validate)

    assert _private_files.harden_private_directory(directory)
    assert _private_files.harden_private_file(private_file)
    assert calls == [
        ("harden", directory, True),
        ("validate", directory, True),
        ("harden", private_file, False),
        ("validate", private_file, False),
    ]


def test_windows_privacy_uses_native_acl_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "private"
    directory.mkdir()
    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(
        _private_files,
        "_windows_path_is_private",
        lambda path, *, directory: calls.append((path, directory)) or True,
    )

    assert _private_files.is_private_directory(directory)
    assert calls == [(directory, True)]


@pytest.mark.skipif(platform.system() != "Windows", reason="requires Windows security APIs")
def test_windows_native_privacy_accepts_current_user_temp_objects(tmp_path: Path) -> None:
    directory = tmp_path / "private"
    directory.mkdir()
    private_file = directory / "private.bin"
    private_file.write_bytes(b"private")

    assert _private_files.current_user_scope().startswith("sid-")
    assert _private_files.harden_private_directory(directory)
    assert _private_files.harden_private_file(private_file)


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


def test_read_exact_source_identifier_is_case_insensitive_and_indexed(
    tmp_path: Path,
) -> None:
    path = _private_directory(tmp_path) / "roots.sqlite3"
    decoy = RootCandidate(
        person_ref="person:decoy",
        reason_code="selectable",
        display_name="A mention of @i2@",
        source_identifier="@I9@",
    )
    build_root_index(path, (_candidate(1), _candidate(2), decoy), max_bytes=1_000_000)

    page = read_root_page(path, query="@i2@", limit=1)
    with closing(sqlite3.connect(path)) as connection:
        query_plan = connection.execute(
            f"EXPLAIN QUERY PLAN {root_index._EXACT_SOURCE_PAGE_SQL}",
            ("@i2@", 1, 0),
        ).fetchall()

    assert [candidate.person_ref for candidate in page.candidates] == ["person:0002"]
    assert page.total_count == 1
    assert page.next_offset is None
    assert any("root_candidates_source_identifier" in row[3] for row in query_plan)


def test_read_unknown_person_anchor_never_falls_back_to_display_search(
    tmp_path: Path,
) -> None:
    path = _private_directory(tmp_path) / "roots.sqlite3"
    missing_anchor = "person:" + "0" * 32
    imported_name = RootCandidate(
        person_ref="person:" + "1" * 32,
        reason_code="selectable",
        display_name=f"Named {missing_anchor} Example",
        source_identifier="@I1@",
    )
    build_root_index(path, (imported_name,), max_bytes=1_000_000)

    page = read_root_page(path, query=missing_anchor)

    assert page.candidates == ()
    assert page.total_count == 0
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


def test_read_substring_looks_ahead_to_finish_a_full_terminal_page(tmp_path: Path) -> None:
    path = _private_directory(tmp_path) / "roots.sqlite3"
    first = RootCandidate(
        person_ref="person:first",
        reason_code="selectable",
        display_name="A Needle",
        source_identifier="@I1@",
    )
    second = RootCandidate(
        person_ref="person:second",
        reason_code="selectable",
        display_name="B Needle",
        source_identifier="@I2@",
    )
    build_root_index(
        path,
        (first, second, _candidate(3), _candidate(4)),
        max_bytes=1_000_000,
    )

    page = read_root_page(path, query="Needle", limit=2)

    assert page.candidates == (first, second)
    assert page.total_count == 2
    assert page.next_offset is None


def test_read_substring_lookahead_preserves_the_next_matching_row(tmp_path: Path) -> None:
    path = _private_directory(tmp_path) / "roots.sqlite3"
    candidates = tuple(
        RootCandidate(
            person_ref=f"person:{number}",
            reason_code="selectable",
            display_name=f"{number} Needle",
            source_identifier=f"@I{number}@",
        )
        for number in range(3)
    )
    build_root_index(path, candidates, max_bytes=1_000_000)

    first = read_root_page(path, query="Needle", limit=2)
    resumed = read_root_page(
        path,
        query="Needle",
        offset=first.next_offset or 0,
        limit=2,
    )

    assert first.candidates == candidates[:2]
    assert first.total_count == 3
    assert first.next_offset == 2
    assert resumed.candidates == candidates[2:]
    assert resumed.total_count is None
    assert resumed.next_offset is None


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


def test_staged_database_is_private_while_candidates_are_consumed(tmp_path: Path) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"

    def candidates() -> Iterator[RootCandidate]:
        staged = list(directory.glob(".*.tmp"))
        assert len(staged) == 1
        assert stat.S_IMODE(staged[0].stat().st_mode) == 0o600
        yield _candidate(1)

    assert build_root_index(path, candidates(), max_bytes=1_000_000) == 1


def test_cancellation_removes_every_staged_database(tmp_path: Path) -> None:
    class Cancelled(Exception):
        pass

    class Cancellation:
        calls = 0

        def check_cancelled(self) -> None:
            self.calls += 1
            if self.calls == 3:
                raise Cancelled

    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"

    with pytest.raises(Cancelled):
        build_root_index(
            path,
            (_candidate(number) for number in range(500)),
            max_bytes=2_000_000,
            cancellation=Cancellation(),
        )

    assert not path.exists()
    assert list(directory.iterdir()) == []


def test_byte_budget_failure_removes_staging_and_does_not_publish(tmp_path: Path) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"

    with pytest.raises(RootIndexError, match="byte budget") as caught:
        build_root_index(path, (_candidate(1),), max_bytes=1)

    assert type(caught.value).__name__ == "RootIndexLimitError"
    assert not path.exists()
    assert list(directory.iterdir()) == []


def test_publication_race_preserves_the_winning_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"
    real_link = root_index.os.link

    def competing_link(source: Path, destination: Path) -> None:
        destination.write_bytes(b"winner")
        destination.chmod(0o600)
        real_link(source, destination)

    monkeypatch.setattr(root_index.os, "link", competing_link)

    with pytest.raises(RootIndexError, match="already exists"):
        build_root_index(path, (_candidate(1),), max_bytes=1_000_000)

    assert path.read_bytes() == b"winner"
    assert list(directory.glob(".*.tmp*")) == []


def test_incomplete_or_count_mismatched_index_fails_closed(tmp_path: Path) -> None:
    directory = _private_directory(tmp_path)
    incomplete = directory / "incomplete.sqlite3"
    mismatched = directory / "mismatched.sqlite3"
    build_root_index(incomplete, (_candidate(1),), max_bytes=1_000_000)
    build_root_index(mismatched, (_candidate(1),), max_bytes=1_000_000)
    with closing(sqlite3.connect(incomplete)) as connection:
        connection.execute("UPDATE root_index_metadata SET value = '0' WHERE key = 'complete'")
        connection.commit()
    with closing(sqlite3.connect(mismatched)) as connection:
        connection.execute(
            "UPDATE root_index_metadata SET value = '2' WHERE key = 'candidate_count'"
        )
        connection.commit()

    with pytest.raises(RootIndexError, match="incomplete"):
        read_root_page(incomplete)
    with pytest.raises(RootIndexError, match="invalid"):
        root_index_count(mismatched)
    with pytest.raises(RootIndexError, match="invalid"):
        read_root_page(mismatched)


def test_missing_lookup_index_is_rejected(tmp_path: Path) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"
    build_root_index(path, (_candidate(1),), max_bytes=1_000_000)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP INDEX root_candidates_source_identifier")
        connection.commit()

    with pytest.raises(RootIndexError, match="invalid"):
        read_root_page(path, query="@I1@")


@pytest.mark.parametrize("query", ("@I9999@", "person:9999"))
def test_admitted_exact_lookup_has_bounded_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"
    build_root_index(path, (_candidate(number) for number in range(10_000)), max_bytes=8_000_000)
    assert root_index_count(path) == 10_000
    real_connect = sqlite3.connect
    vm_steps = 0

    def counted_connect(database: str, *, uri: bool = False) -> sqlite3.Connection:
        connection = real_connect(database, uri=uri)

        def record_steps() -> int:
            nonlocal vm_steps
            vm_steps += 100
            return 0

        connection.set_progress_handler(record_steps, 100)
        return connection

    monkeypatch.setattr(root_index.sqlite3, "connect", counted_connect)

    page = read_root_page(path, query=query)

    assert page.candidates == (_candidate(9999),)
    assert page.total_count == 1
    assert vm_steps < 5_000


@pytest.mark.parametrize("mutation", ("index", "count", "replacement", "permissions"))
def test_changed_admitted_index_is_rejected(tmp_path: Path, mutation: str) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "roots.sqlite3"
    build_root_index(path, (_candidate(1),), max_bytes=1_000_000)
    assert root_index_count(path) == 1

    if mutation == "replacement":
        replacement = directory / "replacement.sqlite3"
        replacement.write_bytes(b"not a database")
        replacement.chmod(0o600)
        replacement.replace(path)
    elif mutation == "permissions":
        path.chmod(0o640)
    else:
        with closing(sqlite3.connect(path)) as connection:
            if mutation == "index":
                connection.execute("DROP INDEX root_candidates_source_identifier")
            else:
                connection.execute(
                    "UPDATE root_index_metadata SET value = '2' WHERE key = 'candidate_count'"
                )
            connection.commit()

    with pytest.raises(RootIndexError):
        read_root_page(path, query="@I1@")


def test_build_sanitizes_sqlite_failures_and_candidate_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "sensitive-family-name.sqlite3"
    sensitive_value = "sensitive-person-value"

    def fail_insert(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError(sensitive_value)

    monkeypatch.setattr(root_index, "_insert_batch", fail_insert)

    with pytest.raises(RootIndexError) as failure:
        build_root_index(
            path,
            (_candidate(1, person_ref=sensitive_value),),
            max_bytes=1_000_000,
        )

    assert sensitive_value not in str(failure.value)
    assert path.name not in str(failure.value)
    assert not path.exists()


def test_build_sanitizes_filesystem_failures_and_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = _private_directory(tmp_path)
    path = directory / "sensitive-family-name.sqlite3"
    sensitive_detail = "sensitive-filesystem-detail"

    def fail_sync(*args: object, **kwargs: object) -> None:
        raise OSError(sensitive_detail)

    monkeypatch.setattr(root_index, "_sync_file", fail_sync)

    with pytest.raises(RootIndexError) as failure:
        build_root_index(path, (_candidate(1),), max_bytes=1_000_000)

    assert sensitive_detail not in str(failure.value)
    assert path.name not in str(failure.value)
    assert not path.exists()
    assert list(directory.iterdir()) == []


def test_read_rejects_non_private_or_corrupt_indexes(tmp_path: Path) -> None:
    directory = _private_directory(tmp_path)
    public = directory / "public.sqlite3"
    corrupt = directory / "corrupt.sqlite3"
    build_root_index(public, (_candidate(1),), max_bytes=1_000_000)
    public.chmod(0o640)
    corrupt.write_bytes(b"not a database")
    corrupt.chmod(0o600)

    with pytest.raises(RootIndexError, match="permissions"):
        read_root_page(public)
    with pytest.raises(RootIndexError, match="invalid"):
        read_root_page(corrupt)
