/** Reduces job snapshots and events monotonically while requesting authoritative resynchronization. */

import type { JobEvent, JobSnapshot, JobState } from '../../shared-contract/desktop'

const terminalStates: ReadonlySet<JobState> = new Set(['completed', 'failed', 'cancelled'])

/** Immutable job snapshot, selection, and event-subscription state for the task center. */
export interface TaskCenterState {
  jobs: Readonly<Record<string, Readonly<JobSnapshot>>>
  order: readonly string[]
  needsResync: readonly string[]
  resyncTargets: Readonly<Record<string, number>>
  announcement: string | null
}

/** Reducer actions produced by job snapshots, deliveries, selection, and subscription changes. */
export type TaskCenterAction =
  | Readonly<{ type: 'loaded'; jobs: readonly Readonly<JobSnapshot>[] }>
  | Readonly<{ type: 'refreshed'; snapshot: Readonly<JobSnapshot> }>
  | Readonly<{ type: 'event'; event: Readonly<JobEvent> }>

/** Empty task-center state before the first job snapshot is loaded. */
export const initialTaskCenterState: Readonly<TaskCenterState> = Object.freeze({
  jobs: Object.freeze({}),
  order: Object.freeze([]),
  needsResync: Object.freeze([]),
  resyncTargets: Object.freeze({}),
  announcement: null,
})

const announce = (snapshot: Readonly<JobSnapshot>): string | null => {
  switch (snapshot.state) {
    case 'cancelling':
      return `Cancellation requested for ${snapshot.name}.`
    case 'pending-safe-point':
      return `Cancellation for ${snapshot.name} is waiting for a safe point.`
    case 'completed':
      return `${snapshot.name} completed.`
    case 'failed':
      return `${snapshot.name} failed.`
    case 'cancelled':
      return `${snapshot.name} was cancelled.`
    default:
      return null
  }
}

const removeResync = (jobs: readonly string[], jobId: string): readonly string[] => (
  jobs.includes(jobId) ? jobs.filter((id) => id !== jobId) : jobs
)

const addResync = (jobs: readonly string[], jobId: string): readonly string[] => (
  jobs.includes(jobId) ? jobs : [...jobs, jobId]
)

const removeResyncTarget = (
  targets: Readonly<Record<string, number>>,
  jobId: string,
): Readonly<Record<string, number>> => {
  if (targets[jobId] === undefined) return targets
  const next = { ...targets }
  delete next[jobId]
  return next
}

const markResync = (
  state: Readonly<TaskCenterState>,
  jobId: string,
  targetSequence: number,
): Readonly<TaskCenterState> => {
  const needsResync = addResync(state.needsResync, jobId)
  const currentTarget = state.resyncTargets[jobId] ?? 0
  if (needsResync === state.needsResync && currentTarget >= targetSequence) return state
  return {
    ...state,
    needsResync,
    resyncTargets: {
      ...state.resyncTargets,
      [jobId]: Math.max(currentTarget, targetSequence),
    },
  }
}

const preferMonotonicSnapshot = (
  current: Readonly<JobSnapshot> | undefined,
  candidate: Readonly<JobSnapshot>,
): Readonly<JobSnapshot> => {
  if (!current) return candidate
  if (terminalStates.has(current.state) && current.state !== candidate.state) return current
  return candidate.sequence < current.sequence ? current : candidate
}

const replaceSnapshot = (
  state: Readonly<TaskCenterState>,
  snapshot: Readonly<JobSnapshot>,
  authoritative: boolean,
): Readonly<TaskCenterState> => {
  const current = state.jobs[snapshot.job_id]
  if (current) {
    if (terminalStates.has(current.state) && current.state !== snapshot.state) return state
    if (snapshot.sequence < current.sequence) return state
    if (snapshot.sequence === current.sequence) {
      if (!authoritative || !state.needsResync.includes(snapshot.job_id)) return state
      return {
        ...state,
        needsResync: removeResync(state.needsResync, snapshot.job_id),
        resyncTargets: removeResyncTarget(state.resyncTargets, snapshot.job_id),
      }
    }
    if (!authoritative && snapshot.sequence !== current.sequence + 1) {
      return markResync(state, snapshot.job_id, snapshot.sequence)
    }
  } else if (!authoritative && snapshot.sequence !== 1) {
    return markResync(state, snapshot.job_id, snapshot.sequence)
  }

  const stateChanged = current?.state !== snapshot.state
  const targetSequence = state.resyncTargets[snapshot.job_id]
  const resyncResolved = authoritative
    || targetSequence === undefined
    || snapshot.sequence >= targetSequence
  return {
    jobs: { ...state.jobs, [snapshot.job_id]: snapshot },
    order: current ? state.order : [...state.order, snapshot.job_id],
    needsResync: resyncResolved
      ? removeResync(state.needsResync, snapshot.job_id)
      : state.needsResync,
    resyncTargets: resyncResolved
      ? removeResyncTarget(state.resyncTargets, snapshot.job_id)
      : state.resyncTargets,
    announcement: stateChanged ? announce(snapshot) : state.announcement,
  }
}

/**
 * Applies deterministic task center reducer transitions without performing bridge side effects.
 */
export const taskCenterReducer = (
  state: Readonly<TaskCenterState>,
  action: TaskCenterAction,
): Readonly<TaskCenterState> => {
  if (action.type === 'loaded') {
    const jobs: Record<string, Readonly<JobSnapshot>> = {}
    const order: string[] = []
    const listedSequences = new Map<string, number>()
    for (const snapshot of action.jobs) {
      if (!jobs[snapshot.job_id]) order.push(snapshot.job_id)
      const liveSnapshot = preferMonotonicSnapshot(state.jobs[snapshot.job_id], snapshot)
      jobs[snapshot.job_id] = preferMonotonicSnapshot(jobs[snapshot.job_id], liveSnapshot)
      listedSequences.set(
        snapshot.job_id,
        Math.max(listedSequences.get(snapshot.job_id) ?? 0, snapshot.sequence),
      )
    }
    const needsResync = state.needsResync.filter((jobId) => {
      const targetSequence = state.resyncTargets[jobId]
      return targetSequence !== undefined
        && jobs[jobId] !== undefined
        && (listedSequences.get(jobId) ?? 0) < targetSequence
    })
    const resyncTargets = Object.fromEntries(
      needsResync.map((jobId) => [jobId, state.resyncTargets[jobId] as number]),
    )
    return {
      jobs,
      order,
      needsResync,
      resyncTargets,
      announcement: null,
    }
  }
  if (action.type === 'refreshed') return replaceSnapshot(state, action.snapshot, true)
  return replaceSnapshot(state, action.event.snapshot, false)
}
