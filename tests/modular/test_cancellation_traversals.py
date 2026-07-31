"""Cancellation coverage for genealogy graph traversals."""

from __future__ import annotations

import threading

import pytest

import ancestryllm.gedcom.graph as graph_module
import ancestryllm.rootsmagic.exporter as exporter_module
from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.jobs import JobManager, JobState
from ancestryllm.gedcom.identity import IndividualRecord
from ancestryllm.gedcom.parser import GedcomRecord
from ancestryllm.rootsmagic.exporter import RootsMagicExporter


def _pause_at_checkpoint(
    started: threading.Event,
    release: threading.Event,
) -> None:
    started.set()
    assert release.wait(2)
    cancellation_checkpoint()


def test_gedcom_graph_traversal_observes_job_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(
        graph_module,
        "cancellation_checkpoint",
        lambda: _pause_at_checkpoint(started, release),
    )
    records = [
        GedcomRecord(
            ["0 @F1@ FAM", "1 HUSB @I2@", "1 WIFE @I3@", "1 CHIL @I1@"],
            "fictional.ged",
            0,
        )
    ]
    people = [IndividualRecord("@I1@"), IndividualRecord("@I2@"), IndividualRecord("@I3@")]
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "GEDCOM graph",
            lambda: graph_module.scoped_tree_pointers(
                "@I1@",
                people,
                records,
                scope="ancestors",
            ),
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


def test_rootsmagic_selection_traversal_observes_job_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(
        exporter_module,
        "cancellation_checkpoint",
        lambda: _pause_at_checkpoint(started, release),
    )
    families = [{"FamilyID": 1, "FatherID": 1, "MotherID": 2}]
    children = [{"FamilyID": 1, "ChildID": 3}]
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "RootsMagic selection",
            lambda: RootsMagicExporter._scope_people(
                "3",
                "ancestors",
                None,
                families,
                children,
            ),
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
