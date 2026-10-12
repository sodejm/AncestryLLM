# Background job result cleanup

`JobManager.submit()` and `JobManager.submit_with_progress()` accept an optional
`result_cleanup` callback for results that hold private or otherwise managed
resources. The manager retains a successful non-`None` result in its `JobSnapshot`
until the consumer calls `discard_result()` or the manager shuts down. Either action
removes the result from the snapshot and transfers it to the callback exactly once.

The same callback receives a result that finishes after it was discarded, after
shutdown began, or after cancellation was accepted. Cancellation is checked once
immediately after the callable returns, so a result produced at that boundary is
released without being published. Results without a cleanup callback keep their
existing retention behavior across shutdown.

Admission rechecks shutdown under the record lock after reserving queue capacity.
A submission paused at that boundary fails with `JOB_MANAGER_CLOSED` and returns
its capacity without creating a job or resource lock. Every admitted result is
therefore visible to shutdown's cleanup scan.

Callbacks run outside the manager lock. They should complete promptly because they
run synchronously on the thread that discards the result, shuts down the manager, or
finishes the job. A callback exception cannot change job state or stop shutdown; the
manager logs only the exception class to avoid exposing private result details. A
failed callback is not retried because ownership has already left the job record.

`None` is the snapshot's absence marker and is never passed to the callback. Cleanup
does not erase lifecycle metadata, progress, or redacted outcome fields.
