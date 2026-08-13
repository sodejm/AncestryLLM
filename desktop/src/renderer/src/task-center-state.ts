import type { JobEvent, JobSnapshot, JobState } from '../../shared-contract/desktop'

const terminalStates: ReadonlySet<JobState> = new Set(['completed', 'failed', 'cancelled'])

export interface TaskCenterState {
  jobs: Readonly<Record<string, Readonly<JobSnapshot>>>
  order: readonly string[]
  needsResync: readonly string[]
  announcement: string | null
}

export type TaskCenterAction =
  | Readonly<{ type: 'loaded'; jobs: readonly Readonly<JobSnapshot>[] }>
  | Readonly<{ type: 'refreshed'; snapshot: Readonly<JobSnapshot> }>
  | Readonly<{ type: 'event'; event: Readonly<JobEvent> }>

export const initialTaskCenterState: Readonly<TaskCenterState> = Object.freeze({
  jobs: Object.freeze({}),
  order: Object.freeze([]),
  needsResync: Object.freeze([]),
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
        announcement: null,
      }
    }
    if (!authoritative && snapshot.sequence !== current.sequence + 1) {
      const needsResync = addResync(state.needsResync, snapshot.job_id)
      return needsResync === state.needsResync ? state : { ...state, needsResync, announcement: null }
    }
  } else if (!authoritative && snapshot.sequence !== 1) {
    const needsResync = addResync(state.needsResync, snapshot.job_id)
    return needsResync === state.needsResync ? state : { ...state, needsResync, announcement: null }
  }

  const stateChanged = current?.state !== snapshot.state
  return {
    jobs: { ...state.jobs, [snapshot.job_id]: snapshot },
    order: current ? state.order : [...state.order, snapshot.job_id],
    needsResync: removeResync(state.needsResync, snapshot.job_id),
    announcement: stateChanged ? announce(snapshot) : null,
  }
}

export const taskCenterReducer = (
  state: Readonly<TaskCenterState>,
  action: TaskCenterAction,
): Readonly<TaskCenterState> => {
  if (action.type === 'loaded') {
    const jobs: Record<string, Readonly<JobSnapshot>> = {}
    const order: string[] = []
    for (const snapshot of action.jobs) {
      if (!jobs[snapshot.job_id]) order.push(snapshot.job_id)
      const liveSnapshot = preferMonotonicSnapshot(state.jobs[snapshot.job_id], snapshot)
      jobs[snapshot.job_id] = preferMonotonicSnapshot(jobs[snapshot.job_id], liveSnapshot)
    }
    return { jobs, order, needsResync: [], announcement: null }
  }
  if (action.type === 'refreshed') return replaceSnapshot(state, action.snapshot, true)
  return replaceSnapshot(state, action.event.snapshot, false)
}
