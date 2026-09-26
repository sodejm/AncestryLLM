"""Native workbench capabilities are consumed once and results remain private."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from ancestryllm.application.jobs import JobLifecycleService, MemoryJobEventRepository
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager, JobState


def test_export_route_maps_desktop_anonymize_to_established_redaction_policy() -> None:
    from ancestryllm.api.rootsmagic_routes import FolderExportRequest, rootsmagic_router

    boundary = Mock()
    router = rootsmagic_router(lambda: boundary, lambda job: job, lambda: None)
    route = next(
        route for route in router.routes if route.operation_id == "exportInternalRootsMagicFolder"
    )
    request = FolderExportRequest.model_validate(
        {
            "schema_version": 1,
            "source_ref": "a" * 64,
            "output_capability": "b" * 64,
            "root_person_id": 1,
            "living": "anonymize",
        }
    )
    route.endpoint(request)
    assert boundary.export.call_args.kwargs["living"] == "redact"


if TYPE_CHECKING:
    from pathlib import Path


def test_native_source_capability_is_single_use_and_results_revoke(tmp_path: Path) -> None:
    from ancestryllm.api.rootsmagic_workbench import NativeRootsMagicWorkbench

    tree = tmp_path / "Fictional.rmtree"
    with closing(sqlite3.connect(tree)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable (PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER); CREATE TABLE NameTable (NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Given TEXT, Surname TEXT, IsPrimary INTEGER); INSERT INTO PersonTable VALUES(1,0,0); INSERT INTO NameTable VALUES(1,1,'Fictional','Example',1);"
        )
    capability = "1" * 64
    manifest = tmp_path / f"{capability}.rootsmagic-source.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "path": str(tree),
                "size_bytes": tree.stat().st_size,
                "sha256": hashlib.sha256(tree.read_bytes()).hexdigest(),
                "friendly_name": tree.name,
            }
        )
    )
    manifest.chmod(0o600)
    jobs = JobLifecycleService(JobManager(max_workers=1), MemoryJobEventRepository())
    boundary = NativeRootsMagicWorkbench(directory=tmp_path, jobs=jobs)
    try:
        job = boundary.inspect(capability)
        assert jobs.manager.wait(job.job_id, timeout=5).state is JobState.COMPLETED
        response = boundary.result(job.job_id)
        assert response["kind"] == "inspection"
        assert str(tmp_path) not in json.dumps(response)
        assert not manifest.exists()
        with pytest.raises(AncestryError):
            boundary.inspect(capability)
        boundary.discard(response["result"]["source_ref"])
        with pytest.raises(AncestryError):
            boundary.result(job.job_id)
    finally:
        boundary.close()
        jobs.close()


def test_native_inspection_normalizes_format_controls_in_source_name(tmp_path: Path) -> None:
    from ancestryllm.api.rootsmagic_workbench import NativeRootsMagicWorkbench

    tree = tmp_path / "Fictional\u200b.rmtree"
    with closing(sqlite3.connect(tree)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable (PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER);"
            "CREATE TABLE NameTable (NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Given TEXT, Surname TEXT, IsPrimary INTEGER);"
            "INSERT INTO PersonTable VALUES(1,0,0);"
            "INSERT INTO NameTable VALUES(1,1,'Fictional','Example',1);"
        )
    capability = "2" * 64
    manifest = tmp_path / f"{capability}.rootsmagic-source.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "path": str(tree),
                "size_bytes": tree.stat().st_size,
                "sha256": hashlib.sha256(tree.read_bytes()).hexdigest(),
                "friendly_name": "Fictional.rmtree",
            }
        )
    )
    manifest.chmod(0o600)
    jobs = JobLifecycleService(JobManager(max_workers=1), MemoryJobEventRepository())
    boundary = NativeRootsMagicWorkbench(directory=tmp_path, jobs=jobs)
    try:
        job = boundary.inspect(capability)
        assert jobs.manager.wait(job.job_id, timeout=5).state is JobState.COMPLETED
        assert boundary.result(job.job_id)["result"]["friendly_name"] == "Fictional.rmtree"
    finally:
        boundary.close()
        jobs.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"source_ref": "a" * 64, "query_id": "people", "sql": "SELECT 1"},
        {"source_ref": "/private/tree", "query_id": "people"},
        {"source_ref": "a" * 64, "query_id": "people", "page_size": 101},
        {"source_ref": "a" * 64, "query_id": "people", "person_id": True},
        {"source_ref": "a" * 64, "query_id": "events", "person_id": 2**53},
        {"source_ref": "a" * 64, "query_id": "SELECT 1"},
    ],
)
def test_query_rejects_untrusted_fields_and_bounds(payload: dict[str, object]) -> None:
    from ancestryllm.api.rootsmagic_routes import PresetQueryRequest

    with pytest.raises(ValidationError):
        PresetQueryRequest.model_validate({"schema_version": 1, **payload})


def test_shutdown_cleans_a_commit_result_arriving_after_close(tmp_path: Path) -> None:
    from threading import Event

    from ancestryllm.api.rootsmagic_workbench import NativeRootsMagicWorkbench, _OwnedJob
    from ancestryllm.core.jobs import CommittedJobResult

    jobs = JobLifecycleService(JobManager(max_workers=1), MemoryJobEventRepository())
    boundary = NativeRootsMagicWorkbench(directory=tmp_path, jobs=jobs)
    started, release, notified = Event(), Event(), Event()

    def committed(_reporter: object) -> CommittedJobResult:
        started.set()
        assert release.wait(5)
        return CommittedJobResult({"private": "fictional"})

    try:
        job = boundary._submit(_OwnedJob("export", None), committed)
        assert started.wait(5)
        boundary.close()
        unsubscribe = jobs.manager.subscribe(
            lambda snapshot: notified.set() if snapshot.state is JobState.COMPLETED else None
        )
        release.set()
        jobs.manager.wait(job.job_id, timeout=5)
        assert notified.wait(5)
        unsubscribe()
        assert jobs.manager.get(job.job_id).result is None
    finally:
        release.set()
        boundary.close()
        jobs.close()


@pytest.fixture
def native_client(tmp_path, api_settings):
    from fastapi.testclient import TestClient

    from ancestryllm.api.app import create_app
    from ancestryllm.api.openapi import _EmptyRegistry
    from ancestryllm.api.rootsmagic_workbench import NativeRootsMagicWorkbench
    from ancestryllm.application.executor import CommandExecutor
    from ancestryllm.application.secret_management import SecretManagementService
    from ancestryllm.application.settings import SettingsService
    from ancestryllm.core.config import AppConfig
    from ancestryllm.core.secrets import MemorySecretStore

    jobs = JobLifecycleService(JobManager(max_workers=1), MemoryJobEventRepository())
    boundary = NativeRootsMagicWorkbench(directory=tmp_path, jobs=jobs)
    app = create_app(
        settings=api_settings,
        registry=_EmptyRegistry(),
        executor=CommandExecutor(()),
        settings_service=SettingsService(
            AppConfig(config_path=tmp_path / "config.toml", data_dir=tmp_path / "data")
        ),
        secret_service=SecretManagementService(MemorySecretStore({})),
        job_service=lambda: jobs,
        rootsmagic_workbench=lambda: boundary,
    )
    try:
        with TestClient(app, base_url="http://127.0.0.1:8421") as client:
            yield client, jobs
    finally:
        boundary.close()
        jobs.close()


@pytest.mark.parametrize("cancel_after_publication", [False, True])
def test_native_http_auth_validation_and_full_query(
    native_client, api_headers, tmp_path, monkeypatch, cancel_after_publication
):
    client, jobs = native_client
    prefix = "/api/v1/rootsmagic"
    assert client.get(prefix + "/presets").status_code == 401
    definitions = client.get(prefix + "/presets", headers=api_headers)
    assert definitions.status_code == 200
    assert [item["query_id"] for item in definitions.json()["queries"]] == [
        "people",
        "family_links",
        "events",
    ]
    tree = tmp_path / "Fictional.rmtree"
    with closing(sqlite3.connect(tree)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable (PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER); "
            "INSERT INTO PersonTable VALUES (1,0,0); "
            "CREATE TABLE NameTable (NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Given TEXT, Surname TEXT, IsPrimary INTEGER); "
            "INSERT INTO NameTable VALUES (1,1,'Fictional','Example',1); "
            "CREATE TABLE FamilyTable (FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER); "
            "CREATE TABLE ChildTable (FamilyID INTEGER, ChildID INTEGER);"
        )
    capability = "d" * 64
    manifest = tmp_path / f"{capability}.rootsmagic-source.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "path": str(tree),
                "size_bytes": tree.stat().st_size,
                "sha256": hashlib.sha256(tree.read_bytes()).hexdigest(),
                "friendly_name": tree.name,
            }
        )
    )
    manifest.chmod(0o600)
    response = client.post(
        prefix + "/sources",
        headers=api_headers,
        json={"schema_version": 1, "source_capability": capability},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert jobs.manager.wait(job_id, timeout=5).state is JobState.COMPLETED
    result = client.get(prefix + f"/jobs/{job_id}/result", headers=api_headers).json()["result"]
    assert result["detected_version"] == "unknown"
    assert str(tmp_path) not in json.dumps(result)
    payload = {"schema_version": 1, "source_ref": result["source_ref"], "query_id": "people"}
    assert (
        client.post(
            prefix + "/queries",
            headers=api_headers,
            json={**payload, "sql": "DELETE FROM PersonTable"},
        ).status_code
        == 400
    )
    query = client.post(prefix + "/queries", headers=api_headers, json=payload)
    assert query.status_code == 200
    query_id = query.json()["job_id"]
    assert jobs.manager.wait(query_id, timeout=5).state is JobState.COMPLETED
    page = client.get(prefix + f"/jobs/{query_id}/result", headers=api_headers).json()["result"]
    assert page["returned_rows"] == 1
    assert page["rows"][0]["values"][0] == 1
    output_capability = "e" * 64
    destination = tmp_path / "Fictional export"
    output_manifest = tmp_path / f"{output_capability}.rootsmagic-output.json"
    output_manifest.write_text(json.dumps({"schema_version": 1, "path": str(destination)}))
    output_manifest.chmod(0o600)
    before = tree.read_bytes()
    from threading import Event

    from ancestryllm.application._rootsmagic_directory_export import RootsMagicDirectoryExporter

    published, release = Event(), Event()
    original_export = RootsMagicDirectoryExporter.export

    def export_then_pause(self, *args, **kwargs):
        result = original_export(self, *args, **kwargs)
        published.set()
        assert release.wait(5)
        return result

    if cancel_after_publication:
        monkeypatch.setattr(RootsMagicDirectoryExporter, "export", export_then_pause)
    exported = client.post(
        prefix + "/exports",
        headers=api_headers,
        json={
            "schema_version": 1,
            "source_ref": result["source_ref"],
            "output_capability": output_capability,
            "root_person_id": 1,
        },
    )
    assert exported.status_code == 200
    export_id = exported.json()["job_id"]
    if cancel_after_publication:
        try:
            assert published.wait(5)
            assert destination.is_dir()
            jobs.manager.cancel(export_id)
        finally:
            release.set()
    assert jobs.manager.wait(export_id, timeout=5).state is JobState.COMPLETED
    artifact = client.get(prefix + f"/jobs/{export_id}/result", headers=api_headers).json()
    assert artifact["kind"] == "export"
    assert artifact["result"]["artifact_id"].startswith("art_")
    assert artifact["result"]["display_name"] == destination.name
    assert str(tmp_path) not in json.dumps(artifact)
    assert {file.name for file in destination.iterdir()} == {
        "tree.ged",
        "report.md",
        "manifest.json",
    }
    assert tree.read_bytes() == before
    discarded = client.post(
        prefix + f"/sources/{result['source_ref']}/discard",
        headers=api_headers,
        json={"schema_version": 1},
    )
    assert discarded.json() == {"schema_version": 1}
    expired = client.post(prefix + "/queries", headers=api_headers, json=payload)
    assert expired.status_code == 409
    assert expired.json()["code"] == "ROOTSMAGIC_SOURCE_UNAVAILABLE"


def test_http_discard_waits_for_final_export_publication_guard(
    native_client, api_headers, tmp_path, monkeypatch
):
    from contextlib import contextmanager
    from threading import Event, Thread

    from ancestryllm.application._rootsmagic_workbench import (
        RootsMagicWorkbench,
        _SourceSession,
    )

    client, jobs = native_client
    prefix = "/api/v1/rootsmagic"
    tree = tmp_path / "Fictional.rmtree"
    with closing(sqlite3.connect(tree)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable (PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER); "
            "INSERT INTO PersonTable VALUES (1,0,0); "
            "CREATE TABLE NameTable (NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Given TEXT, Surname TEXT, IsPrimary INTEGER); "
            "INSERT INTO NameTable VALUES (1,1,'Fictional','Example',1); "
            "CREATE TABLE FamilyTable (FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER); "
            "CREATE TABLE ChildTable (FamilyID INTEGER, ChildID INTEGER);"
        )
    source_capability = "a" * 64
    source_manifest = tmp_path / f"{source_capability}.rootsmagic-source.json"
    source_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "path": str(tree),
                "size_bytes": tree.stat().st_size,
                "sha256": hashlib.sha256(tree.read_bytes()).hexdigest(),
                "friendly_name": tree.name,
            }
        )
    )
    source_manifest.chmod(0o600)
    inspected = client.post(
        prefix + "/sources",
        headers=api_headers,
        json={"schema_version": 1, "source_capability": source_capability},
    )
    inspect_id = inspected.json()["job_id"]
    assert jobs.manager.wait(inspect_id, timeout=5).state is JobState.COMPLETED
    source_ref = client.get(prefix + f"/jobs/{inspect_id}/result", headers=api_headers).json()[
        "result"
    ]["source_ref"]

    output_capability = "b" * 64
    destination = tmp_path / "Serialized export"
    output_manifest = tmp_path / f"{output_capability}.rootsmagic-output.json"
    output_manifest.write_text(json.dumps({"schema_version": 1, "path": str(destination)}))
    output_manifest.chmod(0o600)

    publication_entered = Event()
    release_publication = Event()
    discard_attempted = Event()
    discard_finished = Event()
    original_guard = _SourceSession.publication_guard
    original_discard = RootsMagicWorkbench.discard

    @contextmanager
    def guarded_publication(self):
        with original_guard(self):
            publication_entered.set()
            assert release_publication.wait(5)
            yield

    def signalled_discard(self, discarded_source_ref):
        discard_attempted.set()
        return original_discard(self, discarded_source_ref)

    monkeypatch.setattr(_SourceSession, "publication_guard", guarded_publication)
    monkeypatch.setattr(RootsMagicWorkbench, "discard", signalled_discard)
    exported = client.post(
        prefix + "/exports",
        headers=api_headers,
        json={
            "schema_version": 1,
            "source_ref": source_ref,
            "output_capability": output_capability,
            "root_person_id": 1,
        },
    )
    export_id = exported.json()["job_id"]
    discard_responses = []

    def discard_source() -> None:
        try:
            discard_responses.append(
                client.post(
                    prefix + f"/sources/{source_ref}/discard",
                    headers=api_headers,
                    json={"schema_version": 1},
                )
            )
        finally:
            discard_finished.set()

    discard_thread = Thread(target=discard_source)
    try:
        assert publication_entered.wait(5)
        assert not destination.exists()
        discard_thread.start()
        assert discard_attempted.wait(5)
        assert not discard_finished.is_set()
    finally:
        release_publication.set()
        discard_thread.join(5)

    assert not discard_thread.is_alive()
    assert discard_responses[0].status_code == 200
    assert jobs.manager.wait(export_id, timeout=5).state is JobState.COMPLETED
    assert destination.is_dir()
    unavailable = client.get(prefix + f"/jobs/{export_id}/result", headers=api_headers)
    assert unavailable.status_code == 409
    assert unavailable.json()["code"] == "ROOTSMAGIC_RESULT_UNAVAILABLE"


def test_discard_prevents_export(native_client, api_headers, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from threading import Event

    from ancestryllm.application._rootsmagic_workbench import _SourceSession

    client, jobs = native_client
    prefix = "/api/v1/rootsmagic"
    tree = tmp_path / "Fictional WAL.rmtree"
    writer = sqlite3.connect(tree)
    release_publication = Event()
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.executescript(
            "CREATE TABLE PersonTable (PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER); "
            "INSERT INTO PersonTable VALUES (1,0,0); "
            "CREATE TABLE NameTable (NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Given TEXT, Surname TEXT, IsPrimary INTEGER); "
            "INSERT INTO NameTable VALUES (1,1,'Fictional','Wal',1); "
            "CREATE TABLE FamilyTable (FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER); "
            "CREATE TABLE ChildTable (FamilyID INTEGER, ChildID INTEGER);"
        )
        writer.commit()
        source_paths = (
            tree,
            tree.with_name(tree.name + "-wal"),
            tree.with_name(tree.name + "-shm"),
        )
        assert all(path.is_file() for path in source_paths)
        source_bytes = {path: path.read_bytes() for path in source_paths}

        source_capability = "c" * 64
        source_manifest = tmp_path / f"{source_capability}.rootsmagic-source.json"
        source_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "path": str(tree),
                    "size_bytes": tree.stat().st_size,
                    "sha256": hashlib.sha256(tree.read_bytes()).hexdigest(),
                    "friendly_name": tree.name,
                }
            )
        )
        source_manifest.chmod(0o600)
        inspected = client.post(
            prefix + "/sources",
            headers=api_headers,
            json={"schema_version": 1, "source_capability": source_capability},
        )
        assert inspected.status_code == 200
        inspect_id = inspected.json()["job_id"]
        assert jobs.manager.wait(inspect_id, timeout=5).state is JobState.COMPLETED
        inspection = client.get(prefix + f"/jobs/{inspect_id}/result", headers=api_headers).json()[
            "result"
        ]
        source_ref = inspection["source_ref"]
        assert len(inspection["fingerprint"]) == 64

        output_capability = "f" * 64
        destination = tmp_path / "Revoked export"
        output_manifest = tmp_path / f"{output_capability}.rootsmagic-output.json"
        output_manifest.write_text(json.dumps({"schema_version": 1, "path": str(destination)}))
        output_manifest.chmod(0o600)

        publication_pending = Event()
        original_guard = _SourceSession.publication_guard

        @contextmanager
        def pause_before_publication(self):
            publication_pending.set()
            assert release_publication.wait(5)
            with original_guard(self):
                yield

        monkeypatch.setattr(_SourceSession, "publication_guard", pause_before_publication)
        exported = client.post(
            prefix + "/exports",
            headers=api_headers,
            json={
                "schema_version": 1,
                "source_ref": source_ref,
                "output_capability": output_capability,
                "root_person_id": 1,
            },
        )
        assert exported.status_code == 200
        export_id = exported.json()["job_id"]
        assert publication_pending.wait(5)
        assert not destination.exists()
        assert len(list(tmp_path.glob(".ancestry-export-*"))) == 1

        discarded = client.post(
            prefix + f"/sources/{source_ref}/discard",
            headers=api_headers,
            json={"schema_version": 1},
        )
        assert discarded.status_code == 200
        release_publication.set()

        failed = jobs.manager.wait(export_id, timeout=5)
        assert failed.state is JobState.FAILED
        assert failed.error_code == "ROOTSMAGIC_SOURCE_UNAVAILABLE"
        unavailable = client.get(prefix + f"/jobs/{export_id}/result", headers=api_headers)
        assert unavailable.status_code == 409
        assert unavailable.json()["code"] == "ROOTSMAGIC_RESULT_UNAVAILABLE"
        assert not destination.exists()
        assert not list(tmp_path.glob(".ancestry-export-*"))
        assert {path: path.read_bytes() for path in source_paths} == source_bytes
    finally:
        release_publication.set()
        writer.close()


def test_remote_adapter_does_not_mount_native_workbench(api_client, api_headers):
    assert api_client.get("/api/v1/rootsmagic/presets", headers=api_headers).status_code == 404


@pytest.mark.parametrize("size_bytes", [512 * 1024 * 1024 + 1, 8 * 1024 * 1024 * 1024])
def test_source_manifest_accepts_advertised_size_boundary(size_bytes: int) -> None:
    from ancestryllm.api.rootsmagic_workbench import _SourceManifest

    manifest = _SourceManifest.model_validate(
        {
            "schema_version": 1,
            "path": "/fictional/tree.rmtree",
            "size_bytes": size_bytes,
            "sha256": "a" * 64,
            "friendly_name": "tree.rmtree",
        }
    )
    assert manifest.size_bytes == size_bytes


def test_source_manifest_rejects_above_advertised_size() -> None:
    from ancestryllm.api.rootsmagic_workbench import _SourceManifest

    with pytest.raises(ValidationError):
        _SourceManifest.model_validate(
            {
                "schema_version": 1,
                "path": "/fictional/tree.rmtree",
                "size_bytes": 8 * 1024 * 1024 * 1024 + 1,
                "sha256": "a" * 64,
                "friendly_name": "tree.rmtree",
            }
        )
