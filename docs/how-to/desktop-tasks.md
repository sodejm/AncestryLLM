# Monitor and cancel desktop tasks

The v0.6 **Tasks** workspace presents backend-owned lifecycle state. It does not
admit new work, execute genealogy operations, call a provider, or give the
renderer direct artifact access.

## Inspect task activity

1. Open **Tasks** under **Local task activity**.
2. Choose **Refresh tasks** if the latest backend snapshot has not arrived.
3. If no lifecycle exists, the workspace reports **No tasks yet**.
4. For each task, review its status, progress, stable code, and sanitized
   remediation.

The backend is authoritative. A reload reconstructs task state from the
sidecar; the renderer does not persist jobs. The view may show **Queued**,
**Running**, **Cancelling**, **Waiting for a safe point**, **Completed**,
**Failed**, or **Cancelled**.

A task with a trustworthy total uses determinate progress. Otherwise the
interface says **Progress total unknown.** It never invents a percentage.

## Request cancellation

1. On a **Queued** or **Running** task, choose **Cancel task**.
2. While the request is sent, the control reports
   **Requesting cancellation…**.
3. Wait for the backend to report **Cancelling**, **Waiting for a safe point**,
   or a terminal state.

Cancellation is cooperative. During an atomic write or publication boundary,
the task waits for its declared safe point rather than abandoning partial
output. Do not force-close the application merely because cancellation is not
instantaneous.

If you quit while work is active, the native shutdown prompt offers **Wait**,
**Request cancellation**, and **Stay open**. Choose **Wait** when publication is
near completion; use **Request cancellation** for interruptible work; choose
**Stay open** to abort the quit.

## Interpret completion and failure

A failed task displays a stable code, reviewed message, and bounded
remediation. It never exposes a stack, native path, record, prompt, response,
provider payload, or raw backend error.

Artifact cards contain sanitized type, media type, byte size, and state only.
The Tasks renderer has no direct open or path authority. A **Ready** artifact
can be used only through a separately supported, grant-mediated product action;
**Pending**, **Failed**, and **Revoked** artifacts confer no access.

Refresh and retry when a recoverable view reports `JOB_NOT_FOUND`,
`JOB_EVENT_CURSOR_INVALID`, `JOB_EVENT_REPLAY_EXPIRED`,
`JOB_SERVICE_UNAVAILABLE`, `JOB_SUBSCRIPTION_CLOSED`, or
`JOB_EVENT_STREAM_FAILED`. A replay gap requires a complete refreshed snapshot;
the renderer must not guess at missing events.

See the [Desktop reference](../reference/DESKTOP.md) for the complete task-state
and error lookup.
