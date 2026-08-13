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

  it('preserves one cancellation failure when a sibling cancellation succeeds', async () => {
    const first = snapshot()
    const second = snapshot({
      job_id: 'j654321',
      name: 'Review fictional matches',
    })
    let rejectFirst: ((value: BridgeResult<JobSnapshot>) => void) | undefined
    let resolveSecond: ((value: BridgeResult<JobSnapshot>) => void) | undefined
    const firstCancellation = new Promise<BridgeResult<JobSnapshot>>((resolve) => {
      rejectFirst = resolve
    })
    const secondCancellation = new Promise<BridgeResult<JobSnapshot>>((resolve) => {
      resolveSecond = resolve
    })
    const bridge: AncestryBridge = {
      ...bridgeFor([first, second]),
      cancelJob: vi.fn(({ job_id }) => (
        job_id === first.job_id ? firstCancellation : secondCancellation
      )),
    }

    render(<TaskCenter bridge={bridge} />)
    await userEvent.click(await screen.findByRole('button', { name: `Cancel ${first.name}` }))
    await userEvent.click(screen.getByRole('button', { name: `Cancel ${second.name}` }))

    rejectFirst!({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'JOB_NOT_FOUND',
        message: 'The task is no longer available.',
        remediation: 'Refresh task activity.',
      },
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('Code: JOB_NOT_FOUND')

    resolveSecond!(success(second))
    await waitFor(() => expect(
      screen.getByRole('button', { name: `Cancel ${second.name}` }),
    ).toBeEnabled())
    expect(screen.getByRole('alert')).toHaveTextContent('Code: JOB_NOT_FOUND')
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

  it('keeps a failed job subscription visible when a sibling subscription succeeds', async () => {
    const failedJob = snapshot()
    const healthyJob = snapshot({
      job_id: 'j654321',
      name: 'Review fictional matches',
    })
    let releaseHealthySubscription: (() => void) | undefined
    const healthySubscription = new Promise<void>((resolve) => {
      releaseHealthySubscription = resolve
    })
    const subscribeJobEvents = vi.fn(async (request) => {
      if (request.job_id === failedJob.job_id) {
        return {
          ok: false as const,
          protocolVersion: '1' as const,
          error: {
            code: 'JOB_EVENT_STREAM_FAILED' as const,
            message: 'Task updates were interrupted.',
            remediation: 'Refresh task activity.',
          },
        }
      }
      await healthySubscription
      return success({
        schema_version: 1 as const,
        subscription_id: request.subscription_id,
        job_id: request.job_id,
        subscribed: true as const,
      })
    })
    const bridge: AncestryBridge = {
      ...bridgeFor([failedJob, healthyJob]),
      subscribeJobEvents,
    }

    render(<TaskCenter bridge={bridge} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Code: JOB_EVENT_STREAM_FAILED')
    await act(async () => releaseHealthySubscription!())

    expect(screen.getByRole('alert')).toHaveTextContent('Code: JOB_EVENT_STREAM_FAILED')
  })

  it('retries a subscription capped by the main-process limit after a slot becomes free', async () => {
    const jobs = Array.from({ length: 33 }, (_, index) => snapshot({
      job_id: `j${String(index + 1).padStart(6, '0')}`,
      name: `Fictional task ${index + 1}`,
    }))
    const cappedJob = jobs[32]!
    let deliver: ((delivery: Readonly<JobEventDelivery>) => void) | undefined
    let cappedAttempts = 0
    const subscribeJobEvents = vi.fn(async (request) => {
      if (request.job_id === cappedJob.job_id && cappedAttempts++ === 0) {
        return {
          ok: false as const,
          protocolVersion: '1' as const,
          error: {
            code: 'JOB_SUBSCRIBER_LIMIT' as const,
            message: 'The task update subscription limit was reached.',
            remediation: 'Wait for another task stream to finish.',
          },
        }
      }
      return success({
        schema_version: 1 as const,
        subscription_id: request.subscription_id,
        job_id: request.job_id,
        subscribed: true as const,
      })
    })
    const bridge: AncestryBridge = {
      ...bridgeFor(jobs),
      subscribeJobEvents,
      onJobEvent: vi.fn((listener) => {
        deliver = listener
        return () => undefined
      }),
    }

    render(<TaskCenter bridge={bridge} />)
    await waitFor(() => expect(subscribeJobEvents).toHaveBeenCalledTimes(33))
    const firstRequest = subscribeJobEvents.mock.calls.find(
      ([request]) => request.job_id === jobs[0]!.job_id,
    )![0]

    act(() => deliver!({
      schema_version: 1,
      kind: 'event',
      subscription_id: firstRequest.subscription_id,
      job_id: jobs[0]!.job_id,
      event: {
        schema_version: 1,
        sequence: 2,
        kind: 'terminal',
        created_at: '2026-08-12T12:00:04+00:00',
        snapshot: snapshot({
          ...jobs[0],
          sequence: 2,
          state: 'completed',
          finished_at: '2026-08-12T12:00:04+00:00',
        }),
      },
      error: null,
    }))

    await waitFor(() => expect(
      subscribeJobEvents.mock.calls.filter(([request]) => request.job_id === cappedJob.job_id),
    ).toHaveLength(2))
  })

  it('bounds automatic stream replacement until the user explicitly refreshes', async () => {
    const active = snapshot()
    let deliver: ((delivery: Readonly<JobEventDelivery>) => void) | undefined
    const getJob = vi.fn().mockResolvedValue(success(active))
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
    const bridge: AncestryBridge = {
      ...bridgeFor([active]),
      getJob,
      subscribeJobEvents,
      unsubscribeJobEvents,
      onJobEvent: vi.fn((listener) => {
        deliver = listener
        return () => undefined
      }),
    }
    const streamFailure = (subscriptionId: string): JobEventDelivery => ({
      schema_version: 1,
      kind: 'failure',
      subscription_id: subscriptionId,
      job_id: active.job_id,
      event: null,
      error: {
        code: 'JOB_EVENT_STREAM_FAILED',
        message: 'Task updates were interrupted.',
        remediation: 'Refresh task activity.',
      },
    })

    render(<TaskCenter bridge={bridge} />)
    await screen.findByRole('heading', { name: active.name })
    await waitFor(() => expect(subscribeJobEvents).toHaveBeenCalledTimes(1))

    act(() => deliver!(streamFailure(subscribeJobEvents.mock.calls[0]![0].subscription_id)))
    await waitFor(() => expect(getJob).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(subscribeJobEvents).toHaveBeenCalledTimes(2))

    act(() => deliver!(streamFailure(subscribeJobEvents.mock.calls[1]![0].subscription_id)))
    expect(await screen.findByRole('alert')).toHaveTextContent('Code: JOB_EVENT_STREAM_FAILED')
    await waitFor(() => expect(unsubscribeJobEvents).toHaveBeenCalledTimes(2))
    expect(getJob).toHaveBeenCalledTimes(1)
    expect(subscribeJobEvents).toHaveBeenCalledTimes(2)

    await userEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Refresh tasks' }))
    await waitFor(() => expect(subscribeJobEvents).toHaveBeenCalledTimes(3))
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

  it('renders validated task-specific failure guidance from the authoritative snapshot', async () => {
    const failed = snapshot({
      sequence: 3,
      state: 'failed',
      finished_at: '2026-08-12T12:00:04+00:00',
      error_code: 'JOB_INTERRUPTED',
      error_message: 'AncestryLLM restarted before this task finished.',
      error_remediation: 'Review the operation state before retrying manually.',
    })

    render(<TaskCenter bridge={bridgeFor([failed])} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Code: JOB_INTERRUPTED')
    expect(alert).toHaveTextContent('AncestryLLM restarted before this task finished.')
    expect(alert).toHaveTextContent('Review the operation state before retrying manually.')
    expect(alert).not.toHaveTextContent('Review local service diagnostics')
  })
})
