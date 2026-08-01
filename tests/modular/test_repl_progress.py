from __future__ import annotations

import pytest
from rich.console import Console

import ancestryllm.console.progress as progress_module
from ancestryllm.console.progress import JobProgressDisplay
from ancestryllm.core.jobs import JobSnapshot, JobState, ProgressEvent


def _snapshot(
    state: JobState,
    *,
    job_id: str = "j000001",
    name: str = "fictional operation",
    progress: ProgressEvent | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    error_remediation: str | None = None,
    outcome_summary: str | None = None,
    next_action: str | None = None,
    cancellation_requested_at: str | None = None,
    cancellation_pending: bool = False,
    cancellation_deferred_by: str | None = None,
) -> JobSnapshot:
    return JobSnapshot(
        job_id=job_id,
        name=name,
        state=state,
        submitted_at="2026-07-22T00:00:00+00:00",
        started_at="2026-07-22T00:00:01+00:00" if state is not JobState.QUEUED else None,
        finished_at=(
            "2026-07-22T00:00:02+00:00"
            if state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
            else None
        ),
        resource_keys=(),
        error_code=error_code,
        error_message=error_message,
        error_remediation=error_remediation,
        outcome_summary=outcome_summary,
        next_action=next_action,
        progress=progress,
        cancellation_requested_at=cancellation_requested_at,
        cancellation_pending=cancellation_pending,
        cancellation_deferred_by=cancellation_deferred_by,
    )


def test_live_display_renders_spinner_determinate_progress_and_clean_success() -> None:
    console = Console(record=True, force_terminal=False, width=100)
    display = JobProgressDisplay(console)

    display.handle(_snapshot(JobState.QUEUED))
    assert display.active
    console.print(display.renderable)
    display.handle(
        _snapshot(
            JobState.RUNNING,
            progress=ProgressEvent(
                "Scanning records",
                "2026-07-22T00:00:01+00:00",
                completed=2,
                total=5,
            ),
        )
    )
    assert display.active
    console.print(display.renderable)
    display.handle(
        _snapshot(
            JobState.COMPLETED,
            outcome_summary="Saved one command result.",
            next_action="Run jobs show j000001 to inspect the saved result.",
        )
    )

    rendered = console.export_text()
    assert "queued" in rendered
    assert "Scanning records" in rendered
    assert "2/5" in rendered
    assert "j000001 completed" in rendered
    assert "Outcome: Saved one command result." in rendered
    assert "Next: Run jobs show j000001 to inspect the saved result." in rendered
    assert not display.active


def test_live_display_stops_for_failure_and_cancellation() -> None:
    console = Console(record=True, force_terminal=False, width=100)
    display = JobProgressDisplay(console)

    display.handle(
        _snapshot(
            JobState.RUNNING,
            progress=ProgressEvent("Contacting provider", "2026-07-22T00:00:01+00:00"),
        )
    )
    console.print(display.renderable)
    display.handle(
        _snapshot(
            JobState.FAILED,
            error_code="FICTIONAL_FAILURE",
            error_message="The provider request timed out.",
            error_remediation="Check connectivity, then retry manually.",
        )
    )
    display.handle(_snapshot(JobState.RUNNING))
    display.handle(_snapshot(JobState.CANCELLED))

    rendered = console.export_text()
    assert "indeterminate" in rendered
    assert "failed (FICTIONAL_FAILURE)" in rendered
    assert "Outcome: The provider request timed out." in rendered
    assert "Next: Check connectivity, then retry manually." in rendered
    assert "Inspect: Run jobs show j000001 for retained job state." in rendered
    assert "cancelled" in rendered
    assert not display.active


def test_live_display_distinguishes_requested_and_deferred_cancellation() -> None:
    console = Console(record=True, force_terminal=False, width=100)
    display = JobProgressDisplay(console)
    requested_at = "2026-07-22T00:00:02+00:00"

    display.handle(
        _snapshot(
            JobState.RUNNING,
            cancellation_requested_at=requested_at,
        )
    )
    console.print(display.renderable)
    display.handle(
        _snapshot(
            JobState.RUNNING,
            cancellation_requested_at=requested_at,
            cancellation_pending=True,
            cancellation_deferred_by="publishing GEDCOM bundle",
        )
    )
    console.print(display.renderable)
    display.handle(_snapshot(JobState.CANCELLED))

    rendered = console.export_text()
    assert "cancelling" in rendered
    assert "cancellation pending: publishing GEDCOM bundle" in rendered


def test_live_display_configures_animation_without_double_stdout_patching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options: dict[str, object] = {}
    calls: list[str] = []

    class FakeLive:
        def __init__(self, _renderable: object, **kwargs: object) -> None:
            options.update(kwargs)

        def start(self, *, refresh: bool) -> None:
            assert refresh
            calls.append("start")

        def update(self, _renderable: object, *, refresh: bool) -> None:
            assert refresh
            calls.append("update")

        def stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr(progress_module, "Live", FakeLive)
    display = JobProgressDisplay(Console(record=True, force_terminal=False))

    display.handle(_snapshot(JobState.RUNNING))
    display.handle(
        _snapshot(
            JobState.RUNNING,
            progress=ProgressEvent("Working", "2026-07-22T00:00:01+00:00"),
        )
    )
    display.close()
    display.close()

    assert options["auto_refresh"] is True
    assert options["refresh_per_second"] == 8
    assert options["transient"] is True
    assert options["redirect_stdout"] is False
    assert options["redirect_stderr"] is False
    assert calls == ["start", "update", "stop"]


def test_live_display_keeps_concurrent_jobs_ordered_until_each_finishes() -> None:
    console = Console(record=True, force_terminal=False, width=100)
    display = JobProgressDisplay(console)

    display.handle(_snapshot(JobState.RUNNING, job_id="j000002", name="second"))
    display.handle(_snapshot(JobState.QUEUED, job_id="j000001", name="first"))
    console.print(display.renderable)

    rendered = console.export_text()
    assert rendered.index("j000001") < rendered.index("j000002")

    display.handle(_snapshot(JobState.COMPLETED, job_id="j000001", name="first"))
    assert display.active
    with console.capture() as capture:
        console.print(display.renderable)
    assert "j000001" not in capture.get()
    assert "j000002" in capture.get()

    display.handle(_snapshot(JobState.CANCELLED, job_id="j000002", name="second"))
    assert not display.active
