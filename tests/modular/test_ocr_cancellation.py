"""Cancellation coverage for OCR normalization."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import ancestryllm.ocr.service as ocr_module
from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.jobs import JobManager, JobState
from ancestryllm.ocr.service import OcrService

if TYPE_CHECKING:
    import pytest


class _NeverCalledLlm:
    def __init__(self) -> None:
        self.called = False

    def generate(self, *_args: Any, **_kwargs: Any) -> Any:
        self.called = True
        raise AssertionError("Cancellation must stop OCR normalization before provider use.")


def test_ocr_normalization_loop_cancels_before_provider_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_reached = threading.Event()
    allow_checkpoint = threading.Event()
    checkpoints = 0

    def pause_inside_normalization() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints >= 3:
            checkpoint_reached.set()
            assert allow_checkpoint.wait(2)
        cancellation_checkpoint()

    monkeypatch.setattr(ocr_module, "cancellation_checkpoint", pause_inside_normalization)
    llm = _NeverCalledLlm()
    service = OcrService(llm)  # type: ignore[arg-type]  # Minimal fake enforces the boundary.
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "OCR normalization",
            lambda: service.extract(
                "\n".join(f"Fictional OCR line {index}" for index in range(100)),
                provider_id="fictional",
                model="fictional-model",
            ),
        )
        assert checkpoint_reached.wait(2)
        manager.cancel(job.job_id)
        allow_checkpoint.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        allow_checkpoint.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.error_code == "JOB_CANCELLED"
    assert llm.called is False
