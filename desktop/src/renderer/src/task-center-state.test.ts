/** Verifies monotonic task-center state, gap recovery, and terminal convergence. */

import { describe, expect, it } from 'vitest'
import type { JobEvent, JobSnapshot } from '../../shared-contract/desktop'
import { initialTaskCenterState, taskCenterReducer } from './task-center-state'

const snapshot = (overrides: Partial<JobSnapshot> = {}): JobSnapshot => ({
  schema_version: 1,
  sequence: 1,
  job_id: 'j123456',
  name: 'Export fictional tree',
  state: 'running',
  submitted_at: '2026-08-12T12:00:00+00:00',
  started_at: '2026-08-12T12:00:01+00:00',
  finished_at: null,
  resource_refs: [],
  artifact: null,
  outcome_summary: null,
  next_action: null,
  error_code: null,
  error_message: null,
  error_remediation: null,
  progress: null,
  cancellation_requested_at: null,
  cancellation_deferred_by: null,
  ...overrides,
})

const event = (next: JobSnapshot, kind: JobEvent['kind'] = 'snapshot'): JobEvent => ({
  schema_version: 1,
  sequence: next.sequence,
  kind,
  created_at: '2026-08-12T12:00:02+00:00',
  snapshot: next,
})

describe('task center state', () => {
  it('tracks multiple jobs and ignores duplicate or stale events', () => {
    const first = snapshot()
    const second = snapshot({ job_id: 'j654321', name: 'Analyze fictional tree' })
    const loaded = taskCenterReducer(initialTaskCenterState, { type: 'loaded', jobs: [first, second] })
    const advanced = taskCenterReducer(loaded, {
      type: 'event',
      event: event(snapshot({ sequence: 2, progress: {
        schema_version: 1,
        operation: 'Preparing export',
        timestamp: '2026-08-12T12:00:02+00:00',
        completed: 1,
        total: 4,
      } }), 'progress'),
    })
    const duplicate = taskCenterReducer(advanced, { type: 'event', event: event(first) })

    expect(advanced.order).toEqual(['j123456', 'j654321'])
    expect(advanced.jobs.j123456?.sequence).toBe(2)
    expect(advanced.jobs.j654321?.sequence).toBe(1)
    expect(duplicate).toBe(advanced)
    expect(advanced.announcement).toBeNull()
  })

  it('detects sequence gaps without applying them and converges on an authoritative refresh', () => {
    const loaded = taskCenterReducer(initialTaskCenterState, { type: 'loaded', jobs: [snapshot()] })
    const gap = taskCenterReducer(loaded, {
      type: 'event',
      event: event(snapshot({ sequence: 3, state: 'cancelling', cancellation_requested_at: '2026-08-12T12:00:03+00:00' }), 'cancellation'),
    })
    expect(gap.jobs.j123456?.sequence).toBe(1)
    expect(gap.needsResync).toEqual(['j123456'])

    const refreshed = taskCenterReducer(gap, {
      type: 'refreshed',
      snapshot: snapshot({ sequence: 4, state: 'cancelled', finished_at: '2026-08-12T12:00:04+00:00' }),
    })
    expect(refreshed.jobs.j123456?.state).toBe('cancelled')
    expect(refreshed.needsResync).toEqual([])
    expect(refreshed.announcement).toBe('Export fictional tree was cancelled.')
  })

  it('clears a resync request when an authoritative snapshot confirms the current sequence', () => {
    const loaded = taskCenterReducer(initialTaskCenterState, { type: 'loaded', jobs: [snapshot()] })
    const gap = taskCenterReducer(loaded, {
      type: 'event',
      event: event(snapshot({ sequence: 3 }), 'progress'),
    })

    const refreshed = taskCenterReducer(gap, {
      type: 'refreshed',
      snapshot: snapshot(),
    })

    expect(refreshed.jobs.j123456?.sequence).toBe(1)
    expect(refreshed.needsResync).toEqual([])
  })

  it('keeps terminal state monotonic and announces state changes but not progress churn', () => {
    const terminal = snapshot({ sequence: 4, state: 'completed', finished_at: '2026-08-12T12:00:04+00:00' })
    const loaded = taskCenterReducer(initialTaskCenterState, { type: 'loaded', jobs: [terminal] })
    const staleActive = taskCenterReducer(loaded, {
      type: 'refreshed',
      snapshot: snapshot({ sequence: 5, state: 'running' }),
    })
    const conflictingTerminal = taskCenterReducer(loaded, {
      type: 'refreshed',
      snapshot: snapshot({ sequence: 5, state: 'failed', finished_at: '2026-08-12T12:00:05+00:00' }),
    })
    const second = taskCenterReducer(initialTaskCenterState, { type: 'loaded', jobs: [snapshot()] })
    const cancelling = taskCenterReducer(second, {
      type: 'event',
      event: event(snapshot({ sequence: 2, state: 'cancelling', cancellation_requested_at: '2026-08-12T12:00:03+00:00' }), 'cancellation'),
    })

    expect(staleActive).toBe(loaded)
    expect(conflictingTerminal).toBe(loaded)
    expect(cancelling.announcement).toBe('Cancellation requested for Export fictional tree.')
  })

  it('does not let a delayed list response regress a job advanced by a live event', () => {
    const loaded = taskCenterReducer(initialTaskCenterState, { type: 'loaded', jobs: [snapshot()] })
    const completed = taskCenterReducer(loaded, {
      type: 'event',
      event: event(snapshot({
        sequence: 2,
        state: 'completed',
        finished_at: '2026-08-12T12:00:03+00:00',
      }), 'terminal'),
    })

    const delayedList = taskCenterReducer(completed, { type: 'loaded', jobs: [snapshot()] })

    expect(delayedList.jobs.j123456?.sequence).toBe(2)
    expect(delayedList.jobs.j123456?.state).toBe('completed')
    expect(delayedList.announcement).toBeNull()
  })
})
