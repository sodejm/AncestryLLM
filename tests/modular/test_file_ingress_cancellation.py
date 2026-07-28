"""Cancellation coverage for every bounded public file-ingress loop."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from ancestryllm.core import ingress as ingress_module
from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.ingress import FileIngressPolicy, FileKind
from ancestryllm.core.jobs import JobManager, JobState


@pytest.mark.parametrize(
    "operation",
    ("read_text", "validate_structure", "sha256", "copy_to"),
)
def test_bounded_ingress_loops_observe_cancellation_and_remove_partial_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    source = tmp_path / "fictional-input.ged"
    payload = "".join(f"0 NOTE Fictional bounded line {index:04d}\n" for index in range(256))
    source.write_text(payload, encoding="utf-8")
    destination = tmp_path / "partial-copy.ged"
    policy = FileIngressPolicy()
    expected = policy.fingerprint(source, FileKind.GEDCOM)
    monkeypatch.setattr(ingress_module, "_READ_CHUNK_BYTES", 64)
    checkpoint_reached = threading.Event()
    allow_checkpoint = threading.Event()
    checkpoints = 0

    def pause_inside_loop() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints >= 3:
            checkpoint_reached.set()
            assert allow_checkpoint.wait(2)
        cancellation_checkpoint()

    monkeypatch.setattr(ingress_module, "cancellation_checkpoint", pause_inside_loop)
    operations: dict[str, Callable[[], object]] = {
        "read_text": lambda: policy.read_text(source, FileKind.GEDCOM),
        "validate_structure": lambda: policy.validate_structure(
            [{"fictional": [index]} for index in range(256)],
            FileKind.MANIFEST,
        ),
        "sha256": lambda: policy.sha256(source, FileKind.GEDCOM),
        "copy_to": lambda: policy.copy_to(
            source,
            destination,
            FileKind.GEDCOM,
            expected=expected,
        ),
    }
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(f"bounded ingress {operation}", operations[operation])
        assert checkpoint_reached.wait(2)
        manager.cancel(job.job_id)
        allow_checkpoint.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        allow_checkpoint.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.error_code == "JOB_CANCELLED"
    assert source.read_text(encoding="utf-8") == payload
    assert destination.exists() is False
