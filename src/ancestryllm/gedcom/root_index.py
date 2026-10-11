"""Bounded private SQLite projection for selectable GEDCOM root candidates."""

from __future__ import annotations

import errno
import os
import re
import sqlite3
import stat
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4

from ancestryllm.application.operations import RootCandidate
from ancestryllm.gedcom._private_files import (
    harden_private_file,
    is_private_directory,
    is_private_file,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from ancestryllm.application.ports import CancellationPort

_SCHEMA_VERSION = 1
_INSERT_BATCH_SIZE = 128
_SUBSTRING_SCAN_LIMIT = 4096
_VALIDATION_CACHE_LIMIT = 64
type _IndexIdentity = tuple[int, int, int, int, int]
_VALIDATED_INDEXES: OrderedDict[Path, _IndexIdentity] = OrderedDict()
_VALIDATION_CACHE_LOCK = Lock()
_PERSON_REF_PATTERN = re.compile(r"person:[0-9a-f]{32}")
_REQUIRED_INDEXES = {
    "root_candidates_display_order",
    "root_candidates_source_identifier",
}
_CANDIDATE_COLUMNS = (
    "person_ref",
    "reason_code",
    "display_name",
    "source_identifier",
    "birth_date",
    "death_date",
    "relationship_summary",
)
_ORDERED_PAGE_SQL = """
    SELECT person_ref, reason_code, display_name, source_identifier,
           birth_date, death_date, relationship_summary
    FROM root_candidates
    ORDER BY display_name COLLATE NOCASE, display_name,
             source_identifier, person_ref
    LIMIT ? OFFSET ?
"""
_EXACT_PERSON_PAGE_SQL = """
    SELECT person_ref, reason_code, display_name, source_identifier,
           birth_date, death_date, relationship_summary
    FROM root_candidates
    WHERE person_ref = ?
    ORDER BY display_name COLLATE NOCASE, display_name,
             source_identifier, person_ref
    LIMIT ? OFFSET ?
"""
_EXACT_SOURCE_PAGE_SQL = """
    SELECT person_ref, reason_code, display_name, source_identifier,
           birth_date, death_date, relationship_summary
    FROM root_candidates
    WHERE source_identifier = ? COLLATE NOCASE
    ORDER BY display_name COLLATE NOCASE, display_name,
             source_identifier, person_ref
    LIMIT ? OFFSET ?
"""


class RootIndexError(RuntimeError):
    """Report a sanitized private-index validation or publication failure."""


class RootIndexLimitError(RootIndexError):
    """Report that private-index construction exceeded its byte ceiling."""


@dataclass(frozen=True, slots=True)
class RootIndexPage:
    """One bounded page from a private root-candidate index."""

    candidates: tuple[RootCandidate, ...]
    total_count: int | None
    next_offset: int | None


def build_root_index(
    path: Path,
    candidates: Iterable[RootCandidate],
    *,
    max_bytes: int,
    cancellation: CancellationPort | None = None,
) -> int:
    """Stream candidates into a complete private index and publish it atomically.

    The caller must supply an existing directory inaccessible to group and other
    users. Publication refuses to replace any existing destination. Candidate
    payloads are consumed in bounded chunks and an incomplete staged database is
    removed after cancellation or failure.
    """
    _validate_budget(max_bytes)
    _validate_private_parent(path)
    if _path_exists(path):
        raise RootIndexError("The private root index already exists.")

    staged = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(staged)
        if not harden_private_file(staged):
            raise RootIndexError("The private root index could not be protected.")
        _configure_writer(connection)
        _create_schema(connection)
        _check_staged_size(staged, max_bytes)

        count = 0
        batch: list[tuple[str, ...]] = []
        iterator = iter(candidates)
        while True:
            _check_cancelled(cancellation)
            try:
                candidate = next(iterator)
            except StopIteration:
                break
            batch.append(_candidate_row(candidate))
            if len(batch) == _INSERT_BATCH_SIZE:
                _insert_batch(connection, staged, batch, max_bytes)
                count += len(batch)
                batch.clear()
        if batch:
            _insert_batch(connection, staged, batch, max_bytes)
            count += len(batch)
        _check_cancelled(cancellation)
        _mark_complete(connection, staged, count, max_bytes)
        connection.close()
        connection = None

        if not harden_private_file(staged):
            raise RootIndexError("The private root index could not be protected.")
        _check_staged_size(staged, max_bytes)
        _sync_file(staged)
        _check_cancelled(cancellation)
        _publish_without_overwrite(staged, path)
        _sync_directory(path.parent)
        return count
    except sqlite3.IntegrityError as exc:
        raise RootIndexError(
            "The private root index contains a duplicate person reference."
        ) from exc
    except sqlite3.Error as exc:
        raise RootIndexError("The private root index could not be built.") from exc
    except OSError as exc:
        raise RootIndexError("The private root index could not be built.") from exc
    except RootIndexError:
        raise
    except BaseException:
        raise
    finally:
        if connection is not None:
            connection.close()
        _remove_staged_files(staged)


def root_index_count(path: Path) -> int:
    """Return the verified candidate count from one complete private index."""
    with _open_valid_index(path) as connection:
        return _recorded_candidate_count(connection)


def read_root_page(
    path: Path,
    *,
    query: str = "",
    offset: int = 0,
    limit: int = 50,
) -> RootIndexPage:
    """Read one validated deterministic page through a read-only connection.

    Empty and exact-identifier queries have known totals. Other queries scan at
    most 4,096 candidates per call; their ``next_offset`` is a stable position
    in the deterministic candidate order, including when no match was found.
    """
    _validate_page_request(query, offset, limit)
    with _open_valid_index(path) as connection:
        candidate_count = _recorded_candidate_count(connection)
        if query == "":
            rows = connection.execute(
                _ORDERED_PAGE_SQL,
                (limit, offset),
            ).fetchall()
            next_offset = offset + len(rows)
            return RootIndexPage(
                candidates=tuple(_candidate_from_row(row) for row in rows),
                total_count=candidate_count,
                next_offset=next_offset if next_offset < candidate_count else None,
            )

        exact_page = _read_exact_page(
            connection,
            count_sql="SELECT COUNT(*) FROM root_candidates WHERE person_ref = ?",
            page_sql=_EXACT_PERSON_PAGE_SQL,
            query=query,
            offset=offset,
            limit=limit,
        )
        if exact_page is not None:
            return exact_page
        exact_page = _read_exact_page(
            connection,
            count_sql="""
                SELECT COUNT(*) FROM root_candidates
                WHERE source_identifier = ? COLLATE NOCASE
            """,
            page_sql=_EXACT_SOURCE_PAGE_SQL,
            query=query,
            offset=offset,
            limit=limit,
        )
        if exact_page is not None:
            return exact_page
        if _PERSON_REF_PATTERN.fullmatch(query.casefold()) is not None:
            return RootIndexPage(candidates=(), total_count=0, next_offset=None)

        scan_rows = connection.execute(
            _ORDERED_PAGE_SQL,
            (_SUBSTRING_SCAN_LIMIT, offset),
        )
        needle = query.casefold()
        matches: list[RootCandidate] = []
        scanned = 0
        lookahead_offset: int | None = None
        for row in scan_rows:
            row_offset = offset + scanned
            scanned += 1
            if any(needle in value.casefold() for value in row):
                if len(matches) < limit:
                    matches.append(_candidate_from_row(row))
                else:
                    lookahead_offset = row_offset
                    break
        resume_offset = offset + scanned
        reached_end = resume_offset >= candidate_count
        total_count: int | None = None
        if offset == 0 and reached_end:
            total_count = len(matches) + int(lookahead_offset is not None)
        substring_next_offset: int | None
        if lookahead_offset is not None:
            substring_next_offset = lookahead_offset
        elif reached_end:
            substring_next_offset = None
        else:
            substring_next_offset = resume_offset
        return RootIndexPage(
            candidates=tuple(matches),
            total_count=total_count,
            next_offset=substring_next_offset,
        )


def _read_exact_page(
    connection: sqlite3.Connection,
    *,
    count_sql: str,
    page_sql: str,
    query: str,
    offset: int,
    limit: int,
) -> RootIndexPage | None:
    count_row = connection.execute(count_sql, (query,)).fetchone()
    if count_row is None:
        raise RootIndexError("The private root index is invalid.")
    exact_count = int(count_row[0])
    if exact_count == 0:
        return None
    rows = connection.execute(page_sql, (query, limit, offset)).fetchall()
    next_offset = offset + len(rows)
    return RootIndexPage(
        candidates=tuple(_candidate_from_row(row) for row in rows),
        total_count=exact_count,
        next_offset=next_offset if next_offset < exact_count else None,
    )


def _validate_page_request(query: str, offset: int, limit: int) -> None:
    if not isinstance(query, str):
        raise RootIndexError("The private root index query is invalid.")
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100
    ):
        raise RootIndexError("The private root index page bounds are invalid.")


def _candidate_from_row(row: Sequence[str]) -> RootCandidate:
    return RootCandidate(
        person_ref=row[0],
        reason_code=row[1],
        display_name=row[2],
        source_identifier=row[3],
        birth_date=row[4],
        death_date=row[5],
        relationship_summary=row[6],
    )


def _recorded_candidate_count(connection: sqlite3.Connection) -> int:
    metadata = connection.execute(
        "SELECT value FROM root_index_metadata WHERE key = 'candidate_count'"
    ).fetchone()
    if metadata is None:
        raise RootIndexError("The private root index is invalid.")
    try:
        count = int(metadata[0])
    except (TypeError, ValueError) as exc:
        raise RootIndexError("The private root index is invalid.") from exc
    if count < 0:
        raise RootIndexError("The private root index is invalid.")
    return count


def _validate_budget(max_bytes: int) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise RootIndexError("The private root index byte budget is invalid.")


def _validate_private_parent(path: Path) -> None:
    if not is_private_directory(path.parent):
        raise RootIndexError("The private root index requires a private directory.")


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RootIndexError("The private root index destination is unavailable.") from exc
    return True


def _configure_writer(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA page_size = 4096")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA temp_store = FILE")
    connection.execute("PRAGMA trusted_schema = OFF")


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE root_candidates (
            person_ref TEXT PRIMARY KEY NOT NULL,
            reason_code TEXT NOT NULL,
            display_name TEXT NOT NULL,
            source_identifier TEXT NOT NULL,
            birth_date TEXT NOT NULL,
            death_date TEXT NOT NULL,
            relationship_summary TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX root_candidates_source_identifier
            ON root_candidates(source_identifier COLLATE NOCASE);
        CREATE INDEX root_candidates_display_order
            ON root_candidates(
                display_name COLLATE NOCASE,
                display_name,
                source_identifier,
                person_ref
            );
        CREATE TABLE root_index_metadata (
            key TEXT PRIMARY KEY NOT NULL,
            value TEXT NOT NULL
        ) WITHOUT ROWID;
        INSERT INTO root_index_metadata(key, value) VALUES
            ('schema_version', '1'),
            ('complete', '0');
        """
    )
    connection.commit()


def _candidate_row(candidate: RootCandidate) -> tuple[str, ...]:
    return (
        candidate.person_ref,
        candidate.reason_code,
        candidate.display_name,
        candidate.source_identifier,
        candidate.birth_date,
        candidate.death_date,
        candidate.relationship_summary,
    )


def _insert_batch(
    connection: sqlite3.Connection,
    staged: Path,
    batch: Sequence[tuple[str, ...]],
    max_bytes: int,
) -> None:
    connection.executemany(
        """
        INSERT INTO root_candidates(
            person_ref,
            reason_code,
            display_name,
            source_identifier,
            birth_date,
            death_date,
            relationship_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        batch,
    )
    _check_staged_size(staged, max_bytes)
    connection.commit()
    _check_staged_size(staged, max_bytes)


def _mark_complete(
    connection: sqlite3.Connection,
    staged: Path,
    count: int,
    max_bytes: int,
) -> None:
    connection.execute(
        "INSERT INTO root_index_metadata(key, value) VALUES ('candidate_count', ?)",
        (str(count),),
    )
    connection.execute("UPDATE root_index_metadata SET value = '1' WHERE key = 'complete'")
    _check_staged_size(staged, max_bytes)
    connection.commit()
    _check_staged_size(staged, max_bytes)


def _check_staged_size(staged: Path, max_bytes: int) -> None:
    total = 0
    for candidate in (
        staged,
        Path(f"{staged}-journal"),
        Path(f"{staged}-wal"),
        Path(f"{staged}-shm"),
    ):
        try:
            total += candidate.stat().st_size
        except FileNotFoundError:
            continue
    if total > max_bytes:
        raise RootIndexLimitError("The private root index exceeds its byte budget.")


def _check_cancelled(cancellation: CancellationPort | None) -> None:
    if cancellation is not None:
        cancellation.check_cancelled()


def _sync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_without_overwrite(staged: Path, path: Path) -> None:
    try:
        os.link(staged, path)
    except FileExistsError as exc:
        raise RootIndexError("The private root index already exists.") from exc
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise RootIndexError("The private root index already exists.") from exc
        raise RootIndexError("The private root index could not be published.") from exc


def _sync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_staged_files(staged: Path) -> None:
    for candidate in (
        staged,
        Path(f"{staged}-journal"),
        Path(f"{staged}-wal"),
        Path(f"{staged}-shm"),
    ):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RootIndexError(
                "The private root index staging files could not be removed."
            ) from exc


@contextmanager
def _open_valid_index(path: Path) -> Iterator[sqlite3.Connection]:
    """Validate immutable file identities once while checking permissions on every read."""
    path = path.absolute()
    _validate_private_parent(path)
    identity = _index_identity(path)

    uri = f"file:{quote(str(path), safe='/:')}?mode=ro&immutable=1"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        if not _is_validated_index(path, identity):
            _validate_schema(connection)
        if _index_identity(path) != identity:
            raise RootIndexError("The private root index changed during validation.")
        _remember_valid_index(path, identity)
    except RootIndexError:
        if connection is not None:
            connection.close()
        raise
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise RootIndexError("The private root index is invalid.") from exc
    try:
        yield connection
        if _index_identity(path) != identity:
            raise RootIndexError("The private root index changed during reading.")
    except sqlite3.Error as exc:
        raise RootIndexError("The private root index is invalid.") from exc
    finally:
        connection.close()


def _index_identity(path: Path) -> _IndexIdentity:
    """Return replacement and mutation markers for a regular private file."""
    try:
        details = path.lstat()
    except OSError as exc:
        raise RootIndexError("The private root index is unavailable.") from exc
    if not stat.S_ISREG(details.st_mode) or not is_private_file(path):
        raise RootIndexError("The private root index has invalid permissions.")
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _is_validated_index(path: Path, identity: _IndexIdentity) -> bool:
    """Reuse only full validation of the same immutable file identity."""
    with _VALIDATION_CACHE_LOCK:
        if _VALIDATED_INDEXES.get(path) != identity:
            _VALIDATED_INDEXES.pop(path, None)
            return False
        _VALIDATED_INDEXES.move_to_end(path)
        return True


def _remember_valid_index(path: Path, identity: _IndexIdentity) -> None:
    """Retain a bounded set of validated identities without candidate payloads."""
    with _VALIDATION_CACHE_LOCK:
        _VALIDATED_INDEXES[path] = identity
        _VALIDATED_INDEXES.move_to_end(path)
        while len(_VALIDATED_INDEXES) > _VALIDATION_CACHE_LIMIT:
            _VALIDATED_INDEXES.popitem(last=False)


def _validate_schema(connection: sqlite3.Connection) -> None:
    integrity = connection.execute("PRAGMA quick_check").fetchone()
    if integrity != ("ok",):
        raise RootIndexError("The private root index is invalid.")
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name"
        )
    }
    if tables != {"root_candidates", "root_index_metadata"}:
        raise RootIndexError("The private root index is invalid.")
    columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(root_candidates)"))
    if columns != _CANDIDATE_COLUMNS:
        raise RootIndexError("The private root index is invalid.")
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'index' AND tbl_name = ?",
            ("root_candidates",),
        )
    }
    if not indexes >= _REQUIRED_INDEXES:
        raise RootIndexError("The private root index is invalid.")
    metadata = dict(connection.execute("SELECT key, value FROM root_index_metadata"))
    if metadata.get("schema_version") != str(_SCHEMA_VERSION) or metadata.get("complete") != "1":
        raise RootIndexError("The private root index is incomplete or unsupported.")
    recorded_count = _recorded_candidate_count(connection)
    actual_count = connection.execute("SELECT COUNT(*) FROM root_candidates").fetchone()
    if actual_count != (recorded_count,):
        raise RootIndexError("The private root index is invalid.")


__all__ = [
    "RootIndexError",
    "RootIndexLimitError",
    "RootIndexPage",
    "build_root_index",
    "read_root_page",
    "root_index_count",
]
