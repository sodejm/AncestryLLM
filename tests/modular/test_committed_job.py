"""A durable publication cannot become cancelled after its linearization point."""

from ancestryllm.core.jobs import JobManager, JobState


def test_committed_result_wins_over_concurrent_cancellation() -> None:
    from ancestryllm.core.cancellation import current_cancellation_token
    from ancestryllm.core.jobs import CommittedJobResult

    def publish() -> CommittedJobResult:
        token = current_cancellation_token()
        assert token is not None
        token.request()
        return CommittedJobResult({"published": True})

    manager = JobManager(max_workers=1)
    try:
        job = manager.submit("fictional-publication", publish)
        snapshot = manager.wait(job.job_id, timeout=5)
        assert snapshot.state is JobState.COMPLETED
        assert snapshot.result == {"published": True}
    finally:
        manager.shutdown()
