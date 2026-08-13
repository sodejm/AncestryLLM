import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  desktopChannels,
  desktopEventChannels,
  type AncestryBridge,
} from '../shared-contract/desktop'

const electron = vi.hoisted(() => ({
  exposeInMainWorld: vi.fn(),
  invoke: vi.fn(),
  on: vi.fn(),
  removeListener: vi.fn(),
}))

vi.mock('electron', () => ({
  contextBridge: { exposeInMainWorld: electron.exposeInMainWorld },
  ipcRenderer: {
    invoke: electron.invoke,
    on: electron.on,
    removeListener: electron.removeListener,
  },
}))

const runningJob = {
  schema_version: 1 as const,
  sequence: 3,
  job_id: 'j123456',
  name: 'Import family tree',
  state: 'running' as const,
  submitted_at: '2026-08-12T12:00:00Z',
  started_at: '2026-08-12T12:00:01Z',
  finished_at: null,
  resource_refs: [],
  artifact: null,
  outcome_summary: null,
  next_action: null,
  error_code: null,
  error_message: null,
  error_remediation: null,
  progress: {
    schema_version: 1 as const,
    operation: 'Reading records',
    timestamp: '2026-08-12T12:00:02Z',
    completed: 4,
    total: 10,
  },
  cancellation_requested_at: null,
  cancellation_deferred_by: null,
} as const

const subscriptionId = 'sub_0123456789abcdef0123456789abcdef'

function success<T extends object>(data: T) {
  return { ok: true as const, protocolVersion: '1' as const, data }
}

async function loadBridge(): Promise<AncestryBridge> {
  await import('./index')
  expect(electron.exposeInMainWorld).toHaveBeenCalledOnce()
  expect(electron.exposeInMainWorld).toHaveBeenCalledWith('ancestry', expect.any(Object))
  return electron.exposeInMainWorld.mock.calls[0]?.[1] as AncestryBridge
}

describe('preload job bridge', () => {
  beforeEach(() => {
    vi.resetModules()
    electron.exposeInMainWorld.mockClear()
    electron.invoke.mockReset()
    electron.on.mockReset()
    electron.removeListener.mockReset()
  })

  it('validates job list, detail, and cancellation requests and responses', async () => {
    electron.invoke.mockImplementation((channel: string) => {
      if (channel === desktopChannels.listJobs) {
        return Promise.resolve(success({ schema_version: 1, jobs: [runningJob] }))
      }
      if (channel === desktopChannels.cancelJob) {
        return Promise.resolve(success({
          ...runningJob,
          sequence: 4,
          state: 'cancelling',
          cancellation_requested_at: '2026-08-12T12:00:03Z',
        }))
      }
      return Promise.resolve(success(runningJob))
    })
    const bridge = await loadBridge()
    const request = { schema_version: 1 as const, job_id: runningJob.job_id }

    await expect(bridge.listJobs()).resolves.toMatchObject({ ok: true, data: { jobs: [runningJob] } })
    await expect(bridge.getJob(request)).resolves.toMatchObject({ ok: true, data: runningJob })
    await expect(bridge.cancelJob(request)).resolves.toMatchObject({ ok: true, data: { state: 'cancelling' } })
    expect(electron.invoke).toHaveBeenNthCalledWith(1, desktopChannels.listJobs)
    expect(electron.invoke).toHaveBeenNthCalledWith(2, desktopChannels.getJob, request)
    expect(electron.invoke).toHaveBeenNthCalledWith(3, desktopChannels.cancelJob, request)

    await expect(bridge.getJob({ schema_version: 1, job_id: '../private' })).rejects.toThrow()
    expect(electron.invoke).toHaveBeenCalledTimes(3)
  })

  it('validates subscription lifecycle calls', async () => {
    electron.invoke.mockImplementation((channel: string) => {
      if (channel === desktopChannels.subscribeJobEvents) {
        return Promise.resolve(success({
          schema_version: 1,
          subscription_id: subscriptionId,
          job_id: runningJob.job_id,
          subscribed: true,
        }))
      }
      return Promise.resolve(success({
        schema_version: 1,
        subscription_id: subscriptionId,
        unsubscribed: true,
      }))
    })
    const bridge = await loadBridge()
    const subscribe = {
      schema_version: 1 as const,
      subscription_id: subscriptionId,
      job_id: runningJob.job_id,
      after: runningJob.sequence,
    }
    const unsubscribe = { schema_version: 1 as const, subscription_id: subscriptionId }

    await expect(bridge.subscribeJobEvents(subscribe)).resolves.toMatchObject({ ok: true, data: { subscribed: true } })
    await expect(bridge.unsubscribeJobEvents(unsubscribe)).resolves.toMatchObject({ ok: true, data: { unsubscribed: true } })
    expect(electron.invoke).toHaveBeenNthCalledWith(1, desktopChannels.subscribeJobEvents, subscribe)
    expect(electron.invoke).toHaveBeenNthCalledWith(2, desktopChannels.unsubscribeJobEvents, unsubscribe)
  })

  it('delivers only validated job events and cleans up the exact listener once', async () => {
    const bridge = await loadBridge()
    const listener = vi.fn()
    const cleanup = bridge.onJobEvent(listener)
    expect(electron.on).toHaveBeenCalledOnce()
    expect(electron.on).toHaveBeenCalledWith(desktopEventChannels.jobEvent, expect.any(Function))
    const ipcListener = electron.on.mock.calls[0]?.[1] as (...args: unknown[]) => void
    const delivery = {
      schema_version: 1 as const,
      kind: 'event' as const,
      subscription_id: subscriptionId,
      job_id: runningJob.job_id,
      event: {
        schema_version: 1 as const,
        sequence: runningJob.sequence,
        kind: 'progress' as const,
        created_at: '2026-08-12T12:00:02Z',
        snapshot: runningJob,
      },
      error: null,
    }

    ipcListener({}, delivery)
    ipcListener({}, { ...delivery, job_id: '../private' })

    expect(listener).toHaveBeenCalledOnce()
    expect(listener).toHaveBeenCalledWith(delivery)
    cleanup()
    cleanup()
    expect(electron.removeListener).toHaveBeenCalledOnce()
    expect(electron.removeListener).toHaveBeenCalledWith(desktopEventChannels.jobEvent, ipcListener)
  })
})
