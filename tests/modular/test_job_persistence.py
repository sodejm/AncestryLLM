"""Verify encrypted, restart-safe persistence for background jobs."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from ancestryllm.application.jobs import JobEventKind, JobLifecycleState
from ancestryllm.core.errors import AncestryError, StorageError
from ancestryllm.core.jobs import JobSnapshot, JobState
from ancestryllm.core.secrets import MemorySecretStore
from ancestryllm.storage.database import Database
from ancestryllm.storage.job_events import SqlJobEventRepository
from ancestryllm.storage.models import Base

if TYPE_CHECKING:
    from pathlib import Path


def _snapshot(*, state: JobState = JobState.RUNNING) -> JobSnapshot:
    return JobSnapshot(
        job_id="j000001",
        name="fictional encrypted import",
        state=state,
        submitted_at="2026-08-12T12:00:00+00:00",
        started_at=(None if state is JobState.QUEUED else "2026-08-12T12:00:01+00:00"),
        finished_at=None,
        resource_keys=("resource_" + "a" * 64,),
    )


def _legacy_database(path: Path, secrets: MemorySecretStore) -> Database:
    database = Database(path, secrets)
    database.open()
    with database.engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in {"jobs", "job_events"}:
                table.create(connection, checkfirst=True)
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('0001')")
    return database


def test_revision_0001_migrates_atomically_to_restart_safe_job_storage(tmp_path: Path) -> None:
    database = _legacy_database(tmp_path / "workspace.db", MemorySecretStore({}))

    database.initialize()

    with database.engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"jobs", "job_events"} <= tables
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0002"
        )


def test_unknown_revision_is_rejected_before_job_tables_are_created(tmp_path: Path) -> None:
    database = Database(tmp_path / "workspace.db", MemorySecretStore({}))
    database.open()
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('future')")

    with pytest.raises(StorageError) as raised:
        database.initialize()

    assert raised.value.code == "DATABASE_MIGRATION_REQUIRED"
    with database.engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "jobs" not in tables
    assert "job_events" not in tables


def test_interrupted_job_is_reconciled_once_across_encrypted_restarts(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"
    secrets = MemorySecretStore({})
    first_database = Database(path, secrets)
    first_repository = SqlJobEventRepository(first_database, max_events_per_job=8)
    first_repository.record_core(_snapshot())
    first_database.close()

    second_database = Database(path, secrets)
    second_repository = SqlJobEventRepository(second_database, max_events_per_job=8)
    reconciled = second_repository.reconcile_active()

    assert len(reconciled) == 1
    assert reconciled[0].kind is JobEventKind.TERMINAL
    assert reconciled[0].snapshot.state is JobLifecycleState.FAILED
    assert reconciled[0].snapshot.error_code == "JOB_INTERRUPTED"
    second_database.close()

    third_database = Database(path, secrets)
    third_repository = SqlJobEventRepository(third_database, max_events_per_job=8)
    assert third_repository.reconcile_active() == ()
    replay = third_repository.replay("j000001", after=0)
    assert [event.kind for event in replay.events].count(JobEventKind.TERMINAL) == 1
    assert third_repository.next_job_number() == 2


def test_sql_repository_retains_a_bounded_replay_window(tmp_path: Path) -> None:
    repository = SqlJobEventRepository(
        Database(tmp_path / "workspace.db", MemorySecretStore({})),
        max_events_per_job=2,
    )
    repository.record_core(_snapshot(state=JobState.QUEUED))
    repository.record_core(_snapshot())
    repository.record_core(
        replace(
            _snapshot(),
            state=JobState.COMPLETED,
            finished_at="2026-08-12T12:00:03+00:00",
        )
    )

    assert [event.sequence for event in repository.replay("j000001", after=1).events] == [2, 3]
    with pytest.raises(AncestryError) as raised:
        repository.replay("j000001", after=0)
    assert raised.value.code == "JOB_EVENT_REPLAY_EXPIRED"
