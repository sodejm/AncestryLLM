"""Cancellation coverage for immutable RootsMagic SQLite work."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

import ancestryllm.rootsmagic.reader as reader_module
from ancestryllm.core.cancellation import (
    CancellationError,
    CancellationToken,
    bind_cancellation_token,
)
from ancestryllm.core.jobs import JobManager, JobState
from ancestryllm.rootsmagic.reader import RootsMagicReader


def _tree(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO PersonTable VALUES (1)")
    connection.commit()
    connection.close()


class _RowsCursor:
    description = (("PersonID", None, None, None, None, None, None),)

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self._position = 0

    def fetchall(self) -> list[Any]:
        return self.rows

    def fetchmany(self, _size: int) -> list[Any]:
        return self.rows

    def fetchone(self) -> Any:
        if self._position >= len(self.rows):
            return None
        row = self.rows[self._position]
        self._position += 1
        return row


class _InterruptingConnection:
    row_factory = None

    def __init__(
        self,
        target: str,
        interrupted: threading.Event,
        allow_raise: threading.Event,
    ) -> None:
        self.target = target
        self.interrupted = interrupted
        self.allow_raise = allow_raise
        self.progress_handler: Any = None

    def execute(self, sql: str, *_args: object) -> _RowsCursor:
        if self.target in sql and self.progress_handler is not None:
            self.interrupted.set()
            assert self.allow_raise.wait(2)
            if self.progress_handler():
                raise sqlite3.OperationalError("interrupted")
        if "sqlite_master" in sql:
            return _RowsCursor(
                [("PersonTable", "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY)")]
            )
        if "COUNT(*)" in sql:
            return _RowsCursor([(1,)])
        if sql.startswith("SELECT"):
            return _RowsCursor([{"PersonID": 1}])
        return _RowsCursor([])

    def enable_load_extension(self, _enabled: bool) -> None:
        return None

    def set_authorizer(self, _authorizer: Any) -> None:
        return None

    def set_progress_handler(self, handler: Any, _steps: int) -> None:
        self.progress_handler = handler

    def close(self) -> None:
        return None


class _UnrelatedFailureConnection(_InterruptingConnection):
    def execute(self, sql: str, *_args: object) -> _RowsCursor:
        if self.target in sql and self.progress_handler is not None:
            self.interrupted.set()
            assert self.allow_raise.wait(2)
            raise sqlite3.OperationalError("fictional corrupt query")
        return super().execute(sql, *_args)


def test_sqlite_progress_handler_translates_requested_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tree.rmtree"
    _tree(source)
    started = threading.Event()
    release = threading.Event()

    class FakeConnection:
        def __init__(self) -> None:
            self.handler = None
            self.interrupted = False
            self.closed = False

        def execute(self, sql: str):
            if sql == "SELECT fictional_long_query":
                started.set()
                assert release.wait(2)
                assert self.handler is not None
                if self.handler():
                    self.interrupted = True
                    raise sqlite3.OperationalError("interrupted")
            return self

        def enable_load_extension(self, _enabled: bool) -> None:
            return None

        def set_authorizer(self, _authorizer) -> None:
            return None

        def set_progress_handler(self, handler, _steps: int) -> None:
            self.handler = handler

        def close(self) -> None:
            self.closed = True

    fake = FakeConnection()
    monkeypatch.setattr(reader_module.sqlite3, "connect", lambda *_args, **_kwargs: fake)
    reader = RootsMagicReader([tmp_path], timeout_seconds=30)
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "RootsMagic long query",
            lambda: _run_fake_query(reader, source),
        )
        assert started.wait(2)
        manager.cancel(job.job_id)
        release.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        release.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.error_code == "JOB_CANCELLED"
    assert fake.interrupted is True
    assert fake.closed is True


def _run_fake_query(reader: RootsMagicReader, source: Path) -> None:
    with reader.connection(source) as connection:
        connection.execute("SELECT fictional_long_query")


@pytest.mark.parametrize("operation", ("schema", "query", "read_table"))
def test_public_read_progress_interruption_becomes_job_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    source = tmp_path / "tree.rmtree"
    _tree(source)
    started = threading.Event()
    release = threading.Event()

    targets = {
        "schema": "sqlite_master",
        "query": "PersonID FROM PersonTable",
        "read_table": 'SELECT * FROM "PersonTable"',
    }
    monkeypatch.setattr(
        reader_module.sqlite3,
        "connect",
        lambda *_args, **_kwargs: _InterruptingConnection(
            targets[operation],
            started,
            release,
        ),
    )
    reader = RootsMagicReader([tmp_path], timeout_seconds=30)
    manager = JobManager(max_workers=1, max_pending=1)

    def action():
        if operation == "schema":
            return reader.schema(source)
        if operation == "query":
            return reader.query(source, "SELECT PersonID FROM PersonTable")
        return reader.read_table(source, "PersonTable")

    try:
        job = manager.submit(f"RootsMagic {operation}", action)
        assert started.wait(2)
        manager.cancel(job.job_id)
        release.set()
        result = manager.wait(job.job_id, timeout=2)
    finally:
        release.set()
        manager.shutdown()

    assert result.state is JobState.CANCELLED
    assert result.error_code == "JOB_CANCELLED"


def test_sqlite_timeout_wins_race_with_later_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tree.rmtree"
    _tree(source)
    interrupted = threading.Event()
    allow_raise = threading.Event()
    monkeypatch.setattr(
        reader_module.sqlite3,
        "connect",
        lambda *_args, **_kwargs: _InterruptingConnection(
            "PersonID FROM PersonTable",
            interrupted,
            allow_raise,
        ),
    )
    reader = RootsMagicReader([tmp_path], timeout_seconds=-1.0)
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "RootsMagic timed query",
            lambda: reader.query(source, "SELECT PersonID FROM PersonTable"),
        )
        assert interrupted.wait(2)
        manager.cancel(job.job_id)
        allow_raise.set()
        result = manager.wait(job.job_id, timeout=2)
    finally:
        allow_raise.set()
        manager.shutdown()

    assert result.state is JobState.FAILED
    assert result.error_code == "ROOTSMAGIC_QUERY_TIMEOUT"
    assert result.cancellation_requested_at is not None


def test_unrelated_sqlite_failure_wins_race_with_later_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tree.rmtree"
    _tree(source)
    failed_inside_sqlite = threading.Event()
    allow_raise = threading.Event()
    monkeypatch.setattr(
        reader_module.sqlite3,
        "connect",
        lambda *_args, **_kwargs: _UnrelatedFailureConnection(
            "PersonID FROM PersonTable",
            failed_inside_sqlite,
            allow_raise,
        ),
    )
    reader = RootsMagicReader([tmp_path])
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "RootsMagic failed query",
            lambda: reader.query(source, "SELECT PersonID FROM PersonTable"),
        )
        assert failed_inside_sqlite.wait(2)
        manager.cancel(job.job_id)
        allow_raise.set()
        result = manager.wait(job.job_id, timeout=2)
    finally:
        allow_raise.set()
        manager.shutdown()

    assert result.state is JobState.FAILED
    assert result.error_code == "ROOTSMAGIC_QUERY_FAILED"
    assert result.cancellation_requested_at is not None


def test_bound_copy_cancellation_removes_partial_snapshot_and_preserves_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tree.rmtree"
    _tree(source)
    source_bytes = source.read_bytes()
    destination = tmp_path / "owned-snapshot.rmtree"
    reader = RootsMagicReader([tmp_path])
    expected = reader.fingerprint_source(source)
    original_read = reader_module.os.read
    token = CancellationToken()
    reads = 0

    def request_after_first_read(descriptor: int, count: int) -> bytes:
        nonlocal reads
        if count <= 16:
            return original_read(descriptor, count)
        chunk = original_read(descriptor, min(count, 8))
        reads += 1
        if reads == 1:
            token.request()
        return chunk

    monkeypatch.setattr(reader_module.os, "read", request_after_first_read)

    with bind_cancellation_token(token), pytest.raises(CancellationError):
        reader._copy_bound_to(source, destination, expected)

    assert source.read_bytes() == source_bytes
    assert not destination.exists()


def test_bound_copy_cancellation_preserves_unrelated_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tree.rmtree"
    _tree(source)
    source_bytes = source.read_bytes()
    destination = tmp_path / "owned-snapshot.rmtree"
    replacement = tmp_path / "replacement.rmtree"
    sentinel = b"fictional replacement sentinel"
    replacement.write_bytes(sentinel)
    reader = RootsMagicReader([tmp_path])
    expected = reader.fingerprint_source(source)
    original_checkpoint = reader_module.cancellation_checkpoint
    checkpoints = 0

    def replace_then_cancel() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 1:
            os.replace(replacement, destination)
            raise CancellationError("cancel after destination replacement")
        original_checkpoint()

    monkeypatch.setattr(reader_module, "cancellation_checkpoint", replace_then_cancel)

    with pytest.raises(CancellationError, match="cancel after destination replacement"):
        reader._copy_bound_to(source, destination, expected)

    assert source.read_bytes() == source_bytes
    assert destination.read_bytes() == sentinel


def test_schema_row_traversal_checks_for_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cursor:
        def __init__(self) -> None:
            self._rows = iter(
                [
                    ("PersonTable", "CREATE TABLE PersonTable(PersonID INTEGER)"),
                    ("FamilyTable", "CREATE TABLE FamilyTable(FamilyID INTEGER)"),
                ]
            )

        def fetchone(self) -> tuple[str, str] | None:
            return next(self._rows, None)

    class Connection:
        @staticmethod
        def execute(_sql: str) -> Cursor:
            return Cursor()

    calls = 0

    def cancel_between_schema_rows() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CancellationError("cancel schema traversal")

    reader = RootsMagicReader([Path.cwd()])
    monkeypatch.setattr(reader_module, "cancellation_checkpoint", cancel_between_schema_rows)

    with pytest.raises(CancellationError, match="cancel schema traversal"):
        reader._schema_from_connection(Connection())  # type: ignore[arg-type]


def test_real_operation_failure_is_not_masked_by_concurrent_cancellation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tree.rmtree"
    _tree(source)
    reader = RootsMagicReader([tmp_path])
    token = CancellationToken()

    with pytest.raises(RuntimeError, match="real operation failure"):
        with bind_cancellation_token(token), reader.connection(source):
            token.request()
            raise RuntimeError("real operation failure")


def test_connection_verifies_source_on_failure_without_masking_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tree.rmtree"
    _tree(source)
    reader = RootsMagicReader([tmp_path])
    original_verify = reader.verify_source
    fail_final = False
    final_verifications = 0

    def verify_then_fail(path, expected) -> None:
        nonlocal final_verifications
        if fail_final:
            final_verifications += 1
            raise RuntimeError("fictional verification failure")
        original_verify(path, expected)

    monkeypatch.setattr(reader, "verify_source", verify_then_fail)

    with pytest.raises(ValueError, match="primary operation failure"):
        with reader.connection(source):
            fail_final = True
            raise ValueError("primary operation failure")

    assert final_verifications == 1
