"""Rich Live rendering for structured background-job progress."""

from __future__ import annotations

import threading

from rich.console import Console, Group
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from ancestryllm.core.jobs import JobSnapshot, JobState

_ACTIVE_STATES = frozenset({JobState.QUEUED, JobState.RUNNING})
_REFRESHES_PER_SECOND = 8


class JobProgressDisplay:
    """Keep active jobs visible without owning job execution or service state."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._lock = threading.RLock()
        self._active: dict[str, JobSnapshot] = {}
        self._live: Live | None = None

    @property
    def active(self) -> bool:
        return self._live is not None

    @property
    def renderable(self) -> Table:
        return self._render()

    def handle(self, snapshot: JobSnapshot) -> None:
        with self._lock:
            if snapshot.state in _ACTIVE_STATES:
                self._active[snapshot.job_id] = snapshot
                if self._live is None:
                    live = Live(
                        self._render(),
                        console=self.console,
                        auto_refresh=True,
                        refresh_per_second=_REFRESHES_PER_SECOND,
                        transient=True,
                        redirect_stdout=False,
                        redirect_stderr=False,
                    )
                    live.start(refresh=True)
                    self._live = live
                else:
                    self._live.update(self._render(), refresh=True)
                return

            self._active.pop(snapshot.job_id, None)
            if self._live is not None and self._active:
                self._live.update(self._render(), refresh=True)
            elif self._live is not None:
                self._stop_live()
            self.console.print(self._summary(snapshot))

    def _render(self) -> Table:
        table = Table(title="Background jobs", box=None, show_header=True)
        table.add_column("Job")
        table.add_column("Operation")
        table.add_column("Progress")
        for snapshot in sorted(self._active.values(), key=lambda item: item.job_id):
            operation = snapshot.progress.operation if snapshot.progress else snapshot.name
            if snapshot.state is JobState.QUEUED:
                indicator: Spinner | Group = Spinner("dots", text="queued")
            elif snapshot.progress and snapshot.progress.total is not None:
                completed = snapshot.progress.completed or 0
                total = snapshot.progress.total
                indicator = Group(
                    ProgressBar(total=total, completed=completed, width=20),
                    Text(f"{completed}/{total}"),
                )
            else:
                indicator = Spinner("dots", text="working")
            table.add_row(snapshot.job_id, operation, indicator)
        return table

    @staticmethod
    def _summary(snapshot: JobSnapshot) -> Text:
        if snapshot.state is JobState.COMPLETED:
            return Text(f"{snapshot.job_id} completed: {snapshot.name}", style="green")
        if snapshot.state is JobState.CANCELLED:
            return Text(f"{snapshot.job_id} cancelled: {snapshot.name}", style="yellow")
        return Text(
            f"{snapshot.job_id} failed ({snapshot.error_code or 'JOB_FAILED'}): {snapshot.name}",
            style="bold red",
        )

    def close(self) -> None:
        with self._lock:
            try:
                self._stop_live()
            finally:
                self._active.clear()

    def _stop_live(self) -> None:
        live = self._live
        self._live = None
        if live is not None:
            live.stop()
