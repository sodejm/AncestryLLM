/** Exercises task-center rendering, recovery, cancellation, and reload behavior. */

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type {
  AncestryBridge,
  BridgeResult,
  JobEventDelivery,
  JobSnapshot,
} from '../../shared-contract/desktop'
import { createMockAncestryBridge } from '../../mock-bridge/desktop'
import { TaskCenter } from './TaskCenter'

const success = <T extends object>(data: T): BridgeResult<T> => ({
  ok: true,
  protocolVersion: '1',
  data,
})

const snapshot = (overrides: Partial<JobSnapshot> = {}): JobSnapshot => ({
  schema_version: 1,
  sequence: 1,
  job_id: 'j123456',
  name: 'Prepare fictional export',
  state: 'running',
  submitted_at: '2026-08-12T12:00:00+00:00',
  started_at: '2026-08-12T12:00:01+00:00',
  finished_at: null,
  resource_refs: [`resource_${'b'.repeat(64)}`],
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

const completedSnapshot = snapshot({
  sequence: 4,
  job_id: 'j654321',
  name: 'Review fictional matches',
  state: 'completed',
  finished_at: '2026-08-12T12:00:04+00:00',
  progress: {
    schema_version: 1,
    operation: 'Review complete',
    timestamp: '2026-08-12T12:00:04+00:00',
    completed: 4,
    total: 4,
  },
  artifact: {
    artifact_id: `art_${'a'.repeat(32)}`,
    media_type: 'application/json',
    artifact_type: 'match-report',
    size_bytes: 4_096,
    status: 'ready',
    sha256: 'c'.repeat(64),
  },
})

function bridgeFor(jobs: readonly JobSnapshot[]): AncestryBridge {
  const base = createMockAncestryBridge('success')
  return {
    ...base,
    listJobs: vi.fn().mockResolvedValue(success({ schema_version: 1, jobs })),
    getJob: vi.fn(async ({ job_id }) => success(jobs.find((job) => job.job_id === job_id) ?? jobs[0]!)),
    cancelJob: vi.fn(async ({ job_id }) => success(jobs.find((job) => job.job_id === job_id) ?? jobs[0]!)),
    subscribeJobEvents: vi.fn(async (request) => success({
      schema_version: 1 as const,
      subscription_id: request.subscription_id,
      job_id: request.job_id,
      subscribed: true as const,
    })),
    unsubscribeJobEvents: vi.fn(async (request) => success({
      schema_version: 1 as const,
      subscription_id: request.subscription_id,
      unsubscribed: true as const,
    })),
    onJobEvent: vi.fn(() => () => undefined),
  }
}

describe('Task Center', () => {
  it('renders multiple backend snapshots without exposing opaque identifiers or inventing progress', async () => {
    const active = snapshot({
      progress: {
        schema_version: 1,
        operation: 'Preparing export',
        timestamp: '2026-08-12T12:00:02+00:00',
        completed: null,
        total: null,
      },
    })
    const bridge = bridgeFor([active, completedSnapshot])

    render(<TaskCenter bridge={bridge} />)

    const activeCard = (await screen.findByRole('heading', { name: active.name })).closest('article')
    const completedCard = screen.getByRole('heading', { name: completedSnapshot.name }).closest('article')
    expect(activeCard).not.toBeNull()
    expect(completedCard).not.toBeNull()
    expect(within(activeCard!).getByText('Preparing export')).toBeVisible()
    expect(within(activeCard!).queryByRole('progressbar')).not.toBeInTheDocument()
    expect(within(completedCard!).getByRole('progressbar')).toHaveAttribute('value', '4')
    expect(within(completedCard!).getByRole('progressbar')).toHaveAttribute('max', '4')
    expect(within(completedCard!).getByText('Elapsed: 3 seconds')).toBeVisible()
    expect(within(completedCard!).getByText('match-report')).toBeVisible()
    expect(within(completedCard!).getByText('application/json')).toBeVisible()
    expect(within(completedCard!).getByText(/grant-mediated product action/i)).toBeVisible()
    expect(within(completedCard!).queryByRole('button', { name: /open|save|download/i })).not.toBeInTheDocument()

    const visibleText = document.body.textContent ?? ''
    expect(visibleText).not.toContain(active.job_id)
    expect(visibleText).not.toContain(active.resource_refs[0]!)
    expect(visibleText).not.toContain(completedSnapshot.artifact!.artifact_id)
    expect(visibleText).not.toContain(completedSnapshot.artifact!.sha256!)
  })

  it('waits for the backend cancellation result and distinguishes a pending safe point', async () => {
    const active = snapshot()
    let resolveCancellation: ((value: BridgeResult<JobSnapshot>) => void) | undefined
    const cancellation = new Promise<BridgeResult<JobSnapshot>>((resolve) => { resolveCancellation = resolve })
    const bridge = {
      ...bridgeFor([active]),
      cancelJob: vi.fn(() => cancellation),
    }

    render(<TaskCenter bridge={bridge} />)
    const cancel = await screen.findByRole('button', { name: `Cancel ${active.name}` })
    await userEvent.click(cancel)

    expect(cancel).toBeDisabled()
    expect(cancel).toHaveTextContent('Requesting cancellation…')
    expect(screen.queryByText('Waiting for a safe point')).not.toBeInTheDocument()

    resolveCancellation!(success(snapshot({
      sequence: 2,
      state: 'pending-safe-point',
      cancellation_requested_at: '2026-08-12T12:00:03+00:00',
      cancellation_deferred_by: 'fictional private parser details',
    })))

    expect(await screen.findByText('Waiting for a safe point')).toBeVisible()
    expect(screen.getByText(/cancellation will happen after the current safe operation completes/i)).toBeVisible()
    expect(screen.getByText(`Cancellation for ${active.name} is waiting for a safe point.`)).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('fictional private parser details')
  })

  it('refreshes and replaces the subscription after a sequence gap, then cleans up on unmount', async () => {
    const active = snapshot()
    let deliver: ((delivery: Readonly<JobEventDelivery>) => void) | undefined
    const getJob = vi.fn().mockResolvedValue(success(snapshot({ sequence: 3 })))
    const subscribeJobEvents = vi.fn(async (request) => success({
      schema_version: 1 as const,
      subscription_id: request.subscription_id,
      job_id: request.job_id,
      subscribed: true as const,
    }))
    const unsubscribeJobEvents = vi.fn(async (request) => success({
      schema_version: 1 as const,
      subscription_id: request.subscription_id,
      unsubscribed: true as const,
    }))
    const removeListener = vi.fn()
    const bridge: AncestryBridge = {
      ...bridgeFor([active]),
      getJob,
      subscribeJobEvents,
      unsubscribeJobEvents,
      onJobEvent: vi.fn((listener) => {
        deliver = listener
        return removeListener
      }),
    }

    const rendered = render(<TaskCenter bridge={bridge} />)
    await screen.findByRole('heading', { name: active.name })
    await waitFor(() => expect(subscribeJobEvents).toHaveBeenCalledTimes(1))
    const firstSubscription = subscribeJobEvents.mock.calls[0]![0].subscription_id

    act(() => deliver!({
      schema_version: 1,
      kind: 'event',
      subscription_id: firstSubscription,
      job_id: active.job_id,
      event: {
        schema_version: 1,
        sequence: 3,
        kind: 'progress',
        created_at: '2026-08-12T12:00:03+00:00',
        snapshot: snapshot({ sequence: 3 }),
      },
      error: null,
    }))

    await waitFor(() => expect(getJob).toHaveBeenCalledWith({ schema_version: 1, job_id: active.job_id }))
    await waitFor(() => expect(subscribeJobEvents).toHaveBeenCalledTimes(2))
    expect(subscribeJobEvents.mock.calls[1]![0]).toMatchObject({ job_id: active.job_id, after: 3 })
    expect(unsubscribeJobEvents).toHaveBeenCalledWith({ schema_version: 1, subscription_id: firstSubscription })

    rendered.unmount()
    await waitFor(() => expect(removeListener).toHaveBeenCalledOnce())
    await waitFor(() => expect(unsubscribeJobEvents).toHaveBeenCalledTimes(2))
  })

  it('renders only coded fixed recovery when the task snapshot cannot be loaded', async () => {
    const base = bridgeFor([])
    const bridge: AncestryBridge = {
      ...base,
      listJobs: vi.fn().mockResolvedValue({
        ok: false,
        protocolVersion: '1',
        error: {
          code: 'JOB_SERVICE_UNAVAILABLE',
          message: 'token=secret at /Users/person/private-tree.ged',
          remediation: 'Print the request body and retry port 43117.',
        },
      }),
    }

    render(<TaskCenter bridge={bridge} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Code: JOB_SERVICE_UNAVAILABLE')
    expect(alert).toHaveTextContent('Retry the local service or restart AncestryLLM.')
    expect(alert).not.toHaveTextContent(/secret|private-tree|43117|request body/i)
  })
})
