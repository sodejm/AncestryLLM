"""Private source-session behavior against fictional SQLite inputs."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from ancestryllm.application.operations import RootsMagicPresetQueryRequest
from ancestryllm.core.errors import AncestryError

if TYPE_CHECKING:
    from pathlib import Path


def fixture_source(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER);"
            "CREATE TABLE NameTable(NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Given TEXT, "
            "Surname TEXT, IsPrimary INTEGER);"
            "INSERT INTO PersonTable VALUES(1,0,0);"
            "INSERT INTO NameTable VALUES(1,1,'Fictional','Example',1);"
        )


def test_source_session_repeated_queries_and_discard(tmp_path: Path) -> None:
    from ancestryllm.application._rootsmagic_workbench import RootsMagicWorkbench

    path = tmp_path / "fictional.rmtree"
    fixture_source(path)
    before = path.read_bytes()
    workbench = RootsMagicWorkbench()
    summary = workbench.inspect(
        path, size_bytes=len(before), sha256=hashlib.sha256(before).hexdigest()
    )
    assert summary.friendly_name == path.name
    assert summary.detected_version == "unknown"
    assert str(tmp_path) not in summary.to_json()
    request = RootsMagicPresetQueryRequest(summary.source_ref, "people", None, "", 0, 25)
    assert workbench.query(request).returned_rows == 1
    assert workbench.query(request).rows[0].values[1] == "Fictional Example"
    assert path.read_bytes() == before
    workbench.discard(summary.source_ref)
    with pytest.raises(AncestryError, match="unavailable"):
        workbench.query(request)


def test_source_change_and_restart_revoke_session(tmp_path: Path) -> None:
    from ancestryllm.application._rootsmagic_workbench import RootsMagicWorkbench

    path = tmp_path / "fictional.rmtree"
    fixture_source(path)
    before = path.read_bytes()
    workbench = RootsMagicWorkbench()
    summary = workbench.inspect(
        path, size_bytes=len(before), sha256=hashlib.sha256(before).hexdigest()
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE PersonTable SET Living=1")
        connection.commit()
    request = RootsMagicPresetQueryRequest(summary.source_ref, "people", None, "", 0, 25)
    with pytest.raises(AncestryError) as changed:
        workbench.query(request)
    assert changed.value.code == "FILE_INPUT_CHANGED"
    workbench.close()
    with pytest.raises(AncestryError):
        workbench.query(request)
    with pytest.raises(AncestryError):
        RootsMagicWorkbench().query(request)


def test_wrong_picker_digest_never_creates_session(tmp_path: Path) -> None:
    from ancestryllm.application._rootsmagic_workbench import RootsMagicWorkbench

    path = tmp_path / "fictional.rmtree"
    fixture_source(path)
    with pytest.raises(AncestryError) as failure:
        RootsMagicWorkbench().inspect(path, size_bytes=path.stat().st_size, sha256="0" * 64)
    assert failure.value.code == "FILE_INPUT_CHANGED"


def test_source_publication_guard_serializes_revocation(tmp_path: Path) -> None:
    from threading import Event, Thread

    from ancestryllm.application._rootsmagic_workbench import RootsMagicWorkbench

    path = tmp_path / "fictional.rmtree"
    fixture_source(path)
    workbench = RootsMagicWorkbench()
    summary = workbench.inspect(
        path, size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    source = workbench.source(summary.source_ref)
    started, discarded = Event(), Event()

    def revoke() -> None:
        started.set()
        workbench.discard(summary.source_ref)
        discarded.set()

    with source.publication_guard():
        thread = Thread(target=revoke)
        thread.start()
        assert started.wait(2)
        assert not discarded.wait(0.05)
    thread.join(2)
    assert discarded.is_set()
    with pytest.raises(AncestryError), source.publication_guard():
        pytest.fail("A revoked source cannot enter publication")


def test_wal_source_session_preserves_all_original_bytes(tmp_path: Path) -> None:
    from ancestryllm.application._rootsmagic_workbench import RootsMagicWorkbench

    path = tmp_path / "fictional-wal.rmtree"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.executescript(
            "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY);"
            "INSERT INTO PersonTable VALUES(1); INSERT INTO PersonTable VALUES(2);"
        )
        paths = (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm"))
        before = {item: item.read_bytes() for item in paths}
        workbench = RootsMagicWorkbench()
        summary = workbench.inspect(
            path, size_bytes=len(before[path]), sha256=hashlib.sha256(before[path]).hexdigest()
        )
        first = workbench.query(
            RootsMagicPresetQueryRequest(summary.source_ref, "people", None, "", 0, 1)
        )
        second = workbench.query(
            RootsMagicPresetQueryRequest(summary.source_ref, "people", None, "", 1, 1)
        )
        assert first.rows[0].values[0] == 1
        assert second.rows[0].values[0] == 2
        workbench.close()
        assert {item: item.read_bytes() for item in paths} == before
