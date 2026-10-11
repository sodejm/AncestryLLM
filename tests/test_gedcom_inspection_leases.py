"""Private inspection leases retain bounded metadata and erase owned projections."""

from __future__ import annotations

import shutil
import threading
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

import ancestryllm.gedcom.inspection_worker as worker_module
import ancestryllm.gedcom.service as service_module
from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.operations import (
    GedcomInspectRequest,
    GedcomInspectResult,
    GedcomSourceSummary,
    RootCandidate,
)
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode
from ancestryllm.gedcom.root_index import build_root_index
from ancestryllm.gedcom.service import GedcomService

if TYPE_CHECKING:
    from pathlib import Path


def _inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, ttl: float = 900.0
) -> tuple[GedcomService, GedcomInspectRequest, list[Path]]:
    source = tmp_path / "fictional.ged"
    source.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    registry = _ArtifactRegistry()
    request = GedcomInspectRequest(
        source=registry.grant_input(
            source, operation="gedcom.inspect", media_type="text/vnd.gedcom", artifact_type="gedcom"
        )
    )
    directories: list[Path] = []

    def worker(*args: object, **kwargs: object) -> worker_module.WorkerInspection:
        del args, kwargs
        directory = tmp_path / f"worker-{len(directories)}"
        directory.mkdir(mode=0o700)
        directories.append(directory)
        path = directory / "roots.sqlite3"
        candidate = RootCandidate(
            person_ref="person:" + "a" * 32,
            reason_code="individual-record",
            display_name="Fictional Ada",
            source_identifier="@I1@",
        )
        build_root_index(path, (candidate,), max_bytes=1_000_000)
        result = GedcomInspectResult(
            summary=GedcomSourceSummary(
                source=registry.describe_input(request.source, operation="gedcom.inspect"),
                gedcom_version="5.5.5",
                individual_count=1,
                family_count=0,
                other_record_count=2,
            ),
            findings=(),
            root_candidates=(),
            finding_count=0,
            root_candidate_count=1,
        )
        return worker_module.WorkerInspection(result=result, index_path=path, directory=directory)

    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker.run_inspection", worker, raising=False
    )
    return GedcomService(artifacts=registry, root_index_ttl_seconds=ttl), request, directories


def test_result_retains_only_summary_and_opaque_source_bound_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, request, directories = _inspection(tmp_path, monkeypatch)
    try:
        result = service.execute_inspect(request)
        assert result.root_candidates == ()
        assert result.root_index_ref is not None
        assert result.root_index_ref.startswith("root-index:")
        assert result.summary_result().root_candidate_count == 1
        assert "Fictional Ada" not in result.to_json()
        assert str(tmp_path) not in result.to_json()
        page = service.read_inspection_roots(result, query="", offset=0, limit=1)
        assert page.candidates[0].source_identifier == "@I1@"
        altered = replace(
            result,
            summary=replace(result.summary, source=replace(result.summary.source, sha256="b" * 64)),
        )
        with pytest.raises(DomainFailure) as failure:
            service.read_inspection_roots(altered, query="", offset=0, limit=1)
        assert failure.value.code is DomainFailureCode.NOT_FOUND
    finally:
        service.close()
    assert not directories[0].exists()


def test_release_is_idempotent_and_revokes_paging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, request, directories = _inspection(tmp_path, monkeypatch)
    result = service.execute_inspect(request)
    service.release_inspection(result)
    service.release_inspection(result)
    assert not directories[0].exists()
    with pytest.raises(DomainFailure) as failure:
        service.read_inspection_roots(result, query="", offset=0, limit=1)
    assert failure.value.code is DomainFailureCode.NOT_FOUND
    service.close()


def test_expiry_erases_projection_without_another_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    erased = threading.Event()
    original = shutil.rmtree

    def erase(path: Path, **kwargs: object) -> None:
        original(path, **kwargs)
        erased.set()

    monkeypatch.setattr(service_module, "_remove_inspection_directory", erase)
    service, request, directories = _inspection(tmp_path, monkeypatch, ttl=0.05)
    try:
        result = service.execute_inspect(request)
        assert erased.wait(2)
        assert not directories[0].exists()
        with pytest.raises(DomainFailure):
            service.read_inspection_roots(result, query="", offset=0, limit=1)
    finally:
        service.close()


@pytest.mark.parametrize("revocation", ["expiry", "close"])
def test_close_waits_for_a_concurrent_private_index_erasure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revocation: str
) -> None:
    """Close cannot return while a revoked projection is still being erased."""
    service, request, directories = _inspection(tmp_path, monkeypatch)
    result = service.execute_inspect(request)
    erasing = threading.Event()
    resume = threading.Event()
    closing = threading.Event()
    closed = threading.Event()
    original = service_module._remove_inspection_directory

    def paused_erase(path: Path) -> None:
        erasing.set()
        assert resume.wait(2)
        original(path)

    def revoke() -> None:
        if revocation == "expiry":
            service.release_inspection(result)
        else:
            service.close()

    def close() -> None:
        closing.set()
        service.close()
        closed.set()

    monkeypatch.setattr(service_module, "_remove_inspection_directory", paused_erase)
    revoker = threading.Thread(target=revoke)
    closer = threading.Thread(target=close)
    revoker.start()
    try:
        assert erasing.wait(2)
        closer.start()
        assert closing.wait(2)
        assert not closed.wait(0.1)
        assert directories[0].exists()
    finally:
        resume.set()
        revoker.join(2)
        if closer.ident is not None:
            closer.join(2)
        service.close()
    assert not revoker.is_alive()
    assert not closer.is_alive()
    assert closed.is_set()
    assert not directories[0].exists()


def test_close_rejects_late_worker_result_and_cleans_its_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, request, directories = _inspection(tmp_path, monkeypatch)
    entered = threading.Event()
    resume = threading.Event()
    failures: list[DomainFailure] = []
    original = worker_module.run_inspection

    def delayed(*args: object, **kwargs: object) -> object:
        entered.set()
        assert resume.wait(2)
        return original(*args, **kwargs)

    def inspect() -> None:
        try:
            service.execute_inspect(request)
        except DomainFailure as failure:
            failures.append(failure)

    monkeypatch.setattr(worker_module, "run_inspection", delayed)
    thread = threading.Thread(target=inspect)
    thread.start()
    try:
        assert entered.wait(2)
        service.close()
    finally:
        resume.set()
        thread.join(2)
        service.close()
    assert not thread.is_alive()
    assert [failure.code for failure in failures] == [DomainFailureCode.NOT_FOUND]
    assert len(directories) == 1
    assert not directories[0].exists()


def test_timer_start_failure_does_not_retain_an_unusable_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, request, directories = _inspection(tmp_path, monkeypatch)

    def fail_start(_timer: threading.Timer) -> None:
        raise RuntimeError("fictional thread exhaustion")

    monkeypatch.setattr(threading.Timer, "start", fail_start)
    try:
        with pytest.raises(DomainFailure):
            service.execute_inspect(request)
        assert service._inspection_leases == {}
        assert not directories[0].exists()
    finally:
        service.close()


@pytest.mark.parametrize("defect", ["missing", "corrupt", "count-mismatch"])
def test_index_is_validated_before_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    """Reject invalid worker output before starting a timer or retaining private data."""
    service, request, directories = _inspection(tmp_path, monkeypatch)
    original = worker_module.run_inspection
    timer_starts: list[threading.Timer] = []

    def invalid_worker(*args: object, **kwargs: object) -> object:
        inspected = original(*args, **kwargs)
        if defect == "missing":
            inspected.index_path.unlink()
        elif defect == "corrupt":
            inspected.index_path.write_bytes(b"fictional invalid index")
        else:
            inspected = replace(inspected, result=replace(inspected.result, root_candidate_count=2))
        return inspected

    def start_timer(timer: threading.Timer) -> None:
        timer_starts.append(timer)

    monkeypatch.setattr(worker_module, "run_inspection", invalid_worker)
    monkeypatch.setattr(threading.Timer, "start", start_timer)
    try:
        with pytest.raises(DomainFailure) as failure:
            service.execute_inspect(request)
        assert failure.value.code is DomainFailureCode.WORKER_FAILED
        assert service._inspection_leases == {}
        assert timer_starts == []
        assert not directories[0].exists()
    finally:
        service.close()


@pytest.mark.parametrize("stage", ["source-hash", "index-validation", "lease-admission"])
def test_shared_wall_deadline_rejects_late_service_validation_without_a_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """Post-worker validation must retain the original request deadline and erase outputs."""
    service, request, directories = _inspection(tmp_path, monkeypatch)
    original_worker = worker_module.run_inspection
    original_describe = service._require_artifacts().describe_input
    original_count = service_module.root_index_count
    original_token = service_module.secrets.token_hex
    original_check = worker_module.WorkerInspection.check_deadline
    now = [100.0]
    worker_finished = [False]
    timer_starts: list[threading.Timer] = []

    def worker(*args: object, **kwargs: object) -> worker_module.WorkerInspection:
        inspected = original_worker(*args, **kwargs)
        worker_finished[0] = True
        return replace(inspected, deadline=105.0)

    def describe(_registry: _ArtifactRegistry, *args: object, **kwargs: object) -> object:
        source = original_describe(*args, **kwargs)
        if stage == "source-hash" and worker_finished[0]:
            now[0] = 106.0
        return source

    def count(*args: object, **kwargs: object) -> int:
        total = original_count(*args, **kwargs)
        if stage == "index-validation":
            now[0] = 106.0
        return total

    def token(*args: object, **kwargs: object) -> str:
        value = original_token(*args, **kwargs)
        if stage == "lease-admission":
            now[0] = 106.0
        return value

    def check(inspected: worker_module.WorkerInspection, cancellation: object) -> None:
        original_check(inspected, cancellation, clock=lambda: now[0])

    def start_timer(timer: threading.Timer) -> None:
        timer_starts.append(timer)

    monkeypatch.setattr(worker_module, "run_inspection", worker)
    monkeypatch.setattr(worker_module.WorkerInspection, "check_deadline", check)
    monkeypatch.setattr(_ArtifactRegistry, "describe_input", describe)
    monkeypatch.setattr(service_module, "root_index_count", count)
    monkeypatch.setattr(service_module.secrets, "token_hex", token)
    monkeypatch.setattr(threading.Timer, "start", start_timer)
    try:
        with pytest.raises(DomainFailure) as failure:
            service.execute_inspect(request)
        assert failure.value.code is DomainFailureCode.WORKER_TIMEOUT
        assert service._inspection_leases == {}
        assert timer_starts == []
        assert not directories[0].exists()
    finally:
        service.close()
