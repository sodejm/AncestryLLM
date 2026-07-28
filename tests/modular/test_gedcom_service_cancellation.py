"""Cancellation coverage for public GEDCOM service actions."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import ancestryllm.gedcom.service as service_module
from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.jobs import JobManager, JobState
from ancestryllm.gedcom import engine
from ancestryllm.gedcom.service import GedcomService

GEDCOM_FIXTURE = Path(__file__).parents[1] / "fixtures" / "gedcom_merge" / "quality-source-a.ged"


def _write_person(path: Path, pointer: str, name: str) -> None:
    path.write_text(
        "\n".join(
            (
                "0 HEAD",
                "1 SOUR Fictional Cancellation Test",
                "1 GEDC",
                "2 VERS 5.5.5",
                "1 CHAR UTF-8",
                f"0 {pointer} INDI",
                f"1 NAME {name} /Example/",
                "0 TRLR",
                "",
            )
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("action", ("merge", "subtree", "quality"))
def test_gedcom_service_actions_cancel_before_replacing_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    output = tmp_path / f"{action}.out"
    output.write_text("fictional sentinel\n", encoding="utf-8")
    traversal_started = threading.Event()
    allow_checkpoint = threading.Event()

    def pause_traversal() -> None:
        traversal_started.set()
        assert allow_checkpoint.wait(2)
        cancellation_checkpoint()

    monkeypatch.setattr(engine, "cancellation_checkpoint", pause_traversal)
    service = GedcomService()
    operations = {
        "merge": lambda: service.merge(
            [GEDCOM_FIXTURE, GEDCOM_FIXTURE],
            output,
        ),
        "subtree": lambda: service.subtree(
            GEDCOM_FIXTURE,
            output,
            root_person="Maren Hollow",
            scope="ancestors",
        ),
        "quality": lambda: service.quality(
            GEDCOM_FIXTURE,
            output,
            root_person="Maren Hollow",
        ),
    }
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(f"GEDCOM {action}", operations[action])
        assert traversal_started.wait(2)
        manager.cancel(job.job_id)
        allow_checkpoint.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        allow_checkpoint.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.error_code == "JOB_CANCELLED"
    assert output.read_text(encoding="utf-8") == "fictional sentinel\n"
    assert tuple(tmp_path.iterdir()) == (output,)


def test_merge_publication_failure_wins_cancellation_and_rolls_back_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = (tmp_path / "merged.ged").resolve()
    report = (tmp_path / "quality.md").resolve()
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    _write_person(first, "@I1@", "Ada")
    _write_person(second, "@I2@", "Grace")
    output.write_text("previous fictional GEDCOM\n", encoding="utf-8")
    report.write_text("previous fictional quality report\n", encoding="utf-8")
    service = GedcomService()
    second_artifact_started = threading.Event()
    allow_failure = threading.Event()
    replace = service_module.os.replace
    failed_once = False

    def fail_quality(source: str | Path, destination: str | Path) -> None:
        nonlocal failed_once
        if Path(source) == Path(destination) == report and not failed_once:
            failed_once = True
            second_artifact_started.set()
            assert allow_failure.wait(2)
            raise OSError("simulated quality report failure")
        if Path(source) != Path(destination):
            replace(source, destination)

    monkeypatch.setattr(service_module.os, "replace", fail_quality)
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "GEDCOM merge bundle",
            lambda: service.merge(
                [first, second],
                output,
                root_person="@I1@",
                quality_path=report,
            ),
        )
        assert second_artifact_started.wait(2)
        pending = manager.cancel(job.job_id)
        assert pending.cancellation_pending is True
        allow_failure.set()
        failed = manager.wait(job.job_id, timeout=2)
    finally:
        allow_failure.set()
        manager.shutdown()

    assert failed.state is JobState.FAILED
    assert failed.cancellation_requested_at is not None
    assert output.read_text(encoding="utf-8") == "previous fictional GEDCOM\n"
    assert report.read_text(encoding="utf-8") == "previous fictional quality report\n"
    assert not tuple(tmp_path.glob(".ancestry-publish-*"))
