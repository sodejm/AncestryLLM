/** Renders durable sidecar jobs with bounded progress, cancellation, and recovery controls. */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type {
  AncestryBridge,
  BridgeErrorCode,
  JobEventDelivery,
  JobSnapshot,
  JobState,
} from '../../shared-contract/desktop'
import { Button } from './components/Button'
import { CodedErrorView } from './design-system/CodedErrorView'
import { initialTaskCenterState, taskCenterReducer } from './task-center-state'

const terminalStates: ReadonlySet<JobState> = new Set(['completed', 'failed', 'cancelled'])
const cancellableStates: ReadonlySet<JobState> = new Set(['queued', 'running'])

const stateLabels: Readonly<Record<JobState, string>> = {
  queued: 'Queued',
  running: 'Running',
  cancelling: 'Cancelling',
  'pending-safe-point': 'Waiting for a safe point',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const bridgeFromWindow = (): AncestryBridge => (
  window as unknown as { ancestry: AncestryBridge }
).ancestry

function createSubscriptionId(): string {
  const bytes = new Uint8Array(16)
  globalThis.crypto.getRandomValues(bytes)
  return `sub_${Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')}`
}

function byteLabel(value: number): string {
  if (value < 1_024) return `${value} bytes`
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KiB`
  return `${(value / 1_048_576).toFixed(1)} MiB`
}

function elapsedLabel(snapshot: Readonly<JobSnapshot>, nowMs: number): string {
  const startedMs = Date.parse(snapshot.started_at ?? snapshot.submitted_at)
  const finishedMs = snapshot.finished_at === null ? nowMs : Date.parse(snapshot.finished_at)
  const elapsedSeconds = Math.max(0, Math.floor((finishedMs - startedMs) / 1_000))
  if (elapsedSeconds < 60) {
    return `${elapsedSeconds} ${elapsedSeconds === 1 ? 'second' : 'seconds'}`
  }
  const elapsedMinutes = Math.floor(elapsedSeconds / 60)
  const remainingSeconds = elapsedSeconds % 60
  if (elapsedMinutes < 60) return `${elapsedMinutes}m ${remainingSeconds}s`
  const elapsedHours = Math.floor(elapsedMinutes / 60)
  const remainingMinutes = elapsedMinutes % 60
  return `${elapsedHours}h ${remainingMinutes}m`
}

interface TaskCenterProps {
  readonly bridge?: AncestryBridge
}

export function TaskCenter({ bridge: suppliedBridge }: TaskCenterProps) {
  const bridge = suppliedBridge ?? bridgeFromWindow()
  const [state, dispatch] = useReducer(taskCenterReducer, initialTaskCenterState)
  const [loading, setLoading] = useState(true)
  const [failure, setFailure] = useState<BridgeErrorCode | null>(null)
  const [streamFailures, setStreamFailures] = useState<
    ReadonlyMap<string, BridgeErrorCode>
  >(new Map())
  const [cancellationFailures, setCancellationFailures] = useState<
    ReadonlyMap<string, BridgeErrorCode>
  >(new Map())
  const [cancellationRequests, setCancellationRequests] = useState<ReadonlySet<string>>(new Set())
  const [lifecycleRevision, setLifecycleRevision] = useState(0)
  const [clockMs, setClockMs] = useState(() => Date.now())
  const mountedRef = useRef(false)
  const subscriptionsRef = useRef(new Map<string, string>())
  const blockedSubscriptionsRef = useRef(new Set<string>())
  const refreshingRef = useRef(new Set<string>())
  const streamRecoveryAttemptsRef = useRef(new Map<string, number>())
  const refreshJobRef = useRef<(jobId: string) => Promise<void>>(async () => undefined)

  const setStreamFailure = useCallback(
    (jobId: string, code: BridgeErrorCode | null): void => {
      setStreamFailures((current) => {
        if (code === null && !current.has(jobId)) return current
        if (code !== null && current.get(jobId) === code) return current
        const next = new Map(current)
        if (code === null) next.delete(jobId)
        else next.set(jobId, code)
        return next
      })
    },
    [],
  )

  const setCancellationFailure = useCallback(
    (jobId: string, code: BridgeErrorCode | null): void => {
      setCancellationFailures((current) => {
        if (code === null && !current.has(jobId)) return current
        if (code !== null && current.get(jobId) === code) return current
        const next = new Map(current)
        if (code === null) next.delete(jobId)
        else next.set(jobId, code)
        return next
      })
    },
    [],
  )

  const closeSubscription = useCallback(async (jobId: string): Promise<boolean> => {
    const subscriptionId = subscriptionsRef.current.get(jobId)
    if (subscriptionId === undefined) return false
    subscriptionsRef.current.delete(jobId)
    try {
      await bridge.unsubscribeJobEvents({ schema_version: 1, subscription_id: subscriptionId })
    } catch {
      // The main process also closes all sender-owned streams when the renderer exits.
    }
    return true
  }, [bridge])

  const refreshJob = useCallback(async (jobId: string): Promise<void> => {
    if (refreshingRef.current.has(jobId)) return
    refreshingRef.current.add(jobId)
    await closeSubscription(jobId)
    let refreshed = false
    try {
      const result = await bridge.getJob({ schema_version: 1, job_id: jobId })
      if (!mountedRef.current) return
      if (!result.ok) {
        blockedSubscriptionsRef.current.add(jobId)
        setStreamFailure(jobId, result.error.code)
        return
      }
      blockedSubscriptionsRef.current.delete(jobId)
      setStreamFailure(jobId, null)
      dispatch({ type: 'refreshed', snapshot: result.data })
      refreshed = true
    } catch {
      if (mountedRef.current) {
        blockedSubscriptionsRef.current.add(jobId)
        setStreamFailure(jobId, 'JOB_SERVICE_UNAVAILABLE')
      }
    } finally {
      refreshingRef.current.delete(jobId)
      if (refreshed && mountedRef.current) setLifecycleRevision((value) => value + 1)
    }
  }, [bridge, closeSubscription, setStreamFailure])
  refreshJobRef.current = refreshJob

  const subscribe = useCallback(async (snapshot: Readonly<JobSnapshot>): Promise<void> => {
    if (
      subscriptionsRef.current.has(snapshot.job_id)
      || blockedSubscriptionsRef.current.has(snapshot.job_id)
    ) return

    const subscriptionId = createSubscriptionId()
    subscriptionsRef.current.set(snapshot.job_id, subscriptionId)
    try {
      const result = await bridge.subscribeJobEvents({
        schema_version: 1,
        subscription_id: subscriptionId,
        job_id: snapshot.job_id,
        after: snapshot.sequence,
      })
      if (!mountedRef.current || subscriptionsRef.current.get(snapshot.job_id) !== subscriptionId) {
        if (result.ok) {
          await bridge.unsubscribeJobEvents({ schema_version: 1, subscription_id: subscriptionId })
        }
        return
      }
      if (!result.ok) {
        subscriptionsRef.current.delete(snapshot.job_id)
        if (result.error.code !== 'JOB_SUBSCRIBER_LIMIT') {
          blockedSubscriptionsRef.current.add(snapshot.job_id)
        }
        setStreamFailure(snapshot.job_id, result.error.code)
        return
      }
      setStreamFailure(snapshot.job_id, null)
    } catch {
      if (subscriptionsRef.current.get(snapshot.job_id) === subscriptionId) {
        subscriptionsRef.current.delete(snapshot.job_id)
      }
      if (mountedRef.current) {
        blockedSubscriptionsRef.current.add(snapshot.job_id)
        setStreamFailure(snapshot.job_id, 'JOB_SERVICE_UNAVAILABLE')
      }
    }
  }, [bridge, setStreamFailure])

  const loadJobs = useCallback(async (): Promise<void> => {
    setLoading(true)
    try {
      const result = await bridge.listJobs()
      if (!mountedRef.current) return
      if (!result.ok) {
        setFailure(result.error.code)
        return
      }
      const returnedJobIds = new Set(result.data.jobs.map((job) => job.job_id))
      await Promise.all(
        [...subscriptionsRef.current.keys()]
          .filter((jobId) => !returnedJobIds.has(jobId))
          .map(closeSubscription),
      )
      if (!mountedRef.current) return
      streamRecoveryAttemptsRef.current.clear()
      blockedSubscriptionsRef.current.clear()
      setStreamFailures(new Map())
      setCancellationFailures(new Map())
      setFailure(null)
      dispatch({ type: 'loaded', jobs: result.data.jobs })
    } catch {
      if (mountedRef.current) setFailure('JOB_SERVICE_UNAVAILABLE')
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [bridge, closeSubscription])

  useEffect(() => {
    mountedRef.current = true
    const subscriptions = subscriptionsRef.current
    const removeListener = bridge.onJobEvent((delivery: Readonly<JobEventDelivery>) => {
      if (subscriptions.get(delivery.job_id) !== delivery.subscription_id) return
      if (delivery.kind === 'failure') {
        const attempts = streamRecoveryAttemptsRef.current.get(delivery.job_id) ?? 0
        if (attempts >= 1) {
          blockedSubscriptionsRef.current.add(delivery.job_id)
          setStreamFailure(delivery.job_id, delivery.error.code)
          void closeSubscription(delivery.job_id)
          return
        }
        streamRecoveryAttemptsRef.current.set(delivery.job_id, attempts + 1)
        void refreshJobRef.current(delivery.job_id)
        return
      }
      dispatch({ type: 'event', event: delivery.event })
    })
    void loadJobs()

    return () => {
      mountedRef.current = false
      removeListener()
      const subscriptionIds = [...subscriptions.values()]
      subscriptions.clear()
      for (const subscriptionId of subscriptionIds) {
        void bridge.unsubscribeJobEvents({ schema_version: 1, subscription_id: subscriptionId })
      }
    }
  }, [bridge, closeSubscription, loadJobs, setStreamFailure])

  useEffect(() => {
    for (const jobId of state.order) {
      const snapshot = state.jobs[jobId]
      if (snapshot === undefined) continue
      if (state.needsResync.includes(jobId)) {
        void refreshJob(jobId)
      } else if (terminalStates.has(snapshot.state)) {
        void closeSubscription(jobId).then((closed) => {
          if (closed && mountedRef.current) setLifecycleRevision((value) => value + 1)
        })
      } else {
        void subscribe(snapshot)
      }
    }
  }, [closeSubscription, lifecycleRevision, refreshJob, state, subscribe])

  const hasActiveJobs = state.order.some((jobId) => {
    const snapshot = state.jobs[jobId]
    return snapshot !== undefined && !terminalStates.has(snapshot.state)
  })
  useEffect(() => {
    if (!hasActiveJobs) return undefined
    const interval = window.setInterval(() => setClockMs(Date.now()), 1_000)
    return () => window.clearInterval(interval)
  }, [hasActiveJobs])

  const displayedFailure = failure
    ?? cancellationFailures.values().next().value
    ?? streamFailures.values().next().value
    ?? null

  const requestCancellation = async (snapshot: Readonly<JobSnapshot>): Promise<void> => {
    setCancellationRequests((current) => new Set(current).add(snapshot.job_id))
    try {
      const result = await bridge.cancelJob({ schema_version: 1, job_id: snapshot.job_id })
      if (!mountedRef.current) return
      if (!result.ok) {
        setCancellationFailure(snapshot.job_id, result.error.code)
        return
      }
      setCancellationFailure(snapshot.job_id, null)
      dispatch({ type: 'refreshed', snapshot: result.data })
    } catch {
      if (mountedRef.current) {
        setCancellationFailure(snapshot.job_id, 'JOB_SERVICE_UNAVAILABLE')
      }
    } finally {
      if (mountedRef.current) {
        setCancellationRequests((current) => {
          const next = new Set(current)
          next.delete(snapshot.job_id)
          return next
        })
      }
    }
  }

  return <section className="task-center" aria-labelledby="task-center-heading">
    <header className="task-center-header">
      <div>
        <p className="eyebrow">Local task activity</p>
        <h2 id="task-center-heading">Task activity</h2>
        <p>Track local work, request safe cancellation, and review artifact availability.</p>
      </div>
      <Button variant="quiet" onClick={() => void loadJobs()} disabled={loading}>
        {loading ? 'Refreshing…' : 'Refresh tasks'}
      </Button>
    </header>

    <p className="sr-only" aria-live="polite" aria-atomic="true">{state.announcement}</p>

    {displayedFailure && <CodedErrorView
      code={displayedFailure}
      title="Task activity is temporarily unavailable."
      recovery="Retry the local service or restart AncestryLLM."
      actionLabel="Refresh tasks"
      onAction={() => void loadJobs()}
    />}

    {!loading && state.order.length === 0 && !displayedFailure && <div className="empty-state">
      <h3>No tasks yet</h3>
      <p>Long-running local operations will appear here after they start.</p>
    </div>}

    {state.order.length > 0 && <div className="task-list" role="list" aria-label="Task activity">
      {state.order.map((jobId, index) => {
        const snapshot = state.jobs[jobId]
        if (snapshot === undefined) return null
        const completed = snapshot.progress?.completed
        const total = snapshot.progress?.total
        const determinate = typeof completed === 'number'
          && typeof total === 'number'
          && total > 0
          && completed >= 0
          && completed <= total
        const cancellationPending = cancellationRequests.has(snapshot.job_id)
        const headingId = `task-heading-${index + 1}`

        return <article
          key={snapshot.job_id}
          role="listitem"
          className="task-card"
          aria-labelledby={headingId}
        >
          <header className="task-card-header">
            <div>
              <h3 id={headingId}>{snapshot.name}</h3>
              <p className={`job-status job-status-${snapshot.state}`}>{stateLabels[snapshot.state]}</p>
            </div>
            {cancellableStates.has(snapshot.state) && <Button
              variant="quiet"
              disabled={cancellationPending}
              aria-label={`Cancel ${snapshot.name}`}
              onClick={() => void requestCancellation(snapshot)}
            >
              {cancellationPending ? 'Requesting cancellation…' : 'Cancel task'}
            </Button>}
          </header>

          <p className="task-elapsed">Elapsed: {elapsedLabel(snapshot, clockMs)}</p>

          {snapshot.progress && <div className="task-progress">
            <p>{snapshot.progress.operation}</p>
            {determinate && <>
              <progress value={completed} max={total} aria-label={`${snapshot.name} progress`} />
              <p>{completed} of {total}</p>
            </>}
          </div>}

          {snapshot.state === 'pending-safe-point' && <p className="task-safe-point">
            Cancellation will happen after the current safe operation completes.
          </p>}

          {snapshot.state === 'failed' && <CodedErrorView
            code={snapshot.error_code ?? 'TASK_FAILED'}
            title={snapshot.error_message ?? 'This task did not complete.'}
            recovery={snapshot.error_remediation
              ?? 'Review local service diagnostics, then retry the operation.'}
          />}

          {snapshot.artifact && <dl className="task-artifact">
            <div><dt>Artifact</dt><dd>{snapshot.artifact.artifact_type}</dd></div>
            <div><dt>Format</dt><dd>{snapshot.artifact.media_type}</dd></div>
            <div><dt>Size</dt><dd>{byteLabel(snapshot.artifact.size_bytes)}</dd></div>
            <div><dt>Status</dt><dd>{snapshot.artifact.status}</dd></div>
            <div className="task-artifact-note">
              <dt>Access</dt>
              <dd>Available through a grant-mediated product action.</dd>
            </div>
          </dl>}
        </article>
      })}
    </div>}
  </section>
}
