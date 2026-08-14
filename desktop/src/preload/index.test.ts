/** Verifies the preload job bridge validates lifecycle requests and event delivery. */

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
const chatSessionId = `chat_${'a'.repeat(32)}`

const chatCreateRequest = Object.freeze({
  schema_version: 1 as const,
  provider_profile_name: 'local-test',
  model: 'fictional-model',
  purpose: 'genealogy_analysis' as const,
  data_classes: ['deceased_person', 'public_genealogy'] as const,
  consent_name: null,
})

const chatCapability = Object.freeze({
  schema_version: 1 as const,
  max_active_sessions: 32,
  max_messages: 32,
  max_message_characters: 16_384,
  max_context_characters: 65_536,
  max_output_tokens: 4_096,
  max_temperature: 1,
  max_timeout_seconds: 120,
  max_safe_retries: 1,
  transient: true as const,
  tools_enabled: false as const,
  payload_retention: false as const,
  output_is_evidence: false as const,
  streaming: true as const,
  stream_replay_max_bytes: 262_144,
})

const chatSession = Object.freeze({
  schema_version: 1 as const,
  session_id: chatSessionId,
  provider_profile_name: chatCreateRequest.provider_profile_name,
  provider_id: 'ollama',
  model: chatCreateRequest.model,
  purpose: chatCreateRequest.purpose,
  data_classes: chatCreateRequest.data_classes,
  remote: false,
  consent_name: null,
  message_count: 0,
  transient: true as const,
  payload_retention: false as const,
})

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

  it('validates chat session lifecycle requests and responses', async () => {
    electron.invoke.mockImplementation((channel: string) => {
      if (channel === desktopChannels.getChatCapability) {
        return Promise.resolve(success(chatCapability))
      }
      if (channel === desktopChannels.createChatSession) {
        return Promise.resolve(success(chatSession))
      }
      return Promise.resolve(success({
        schema_version: 1,
        session_id: chatSessionId,
        closed: true,
      }))
    })
    const bridge = await loadBridge()
    const closeRequest = { schema_version: 1 as const, session_id: chatSessionId }

    await expect(bridge.getChatCapability()).resolves.toEqual(success(chatCapability))
    await expect(bridge.createChatSession(chatCreateRequest)).resolves.toEqual(success(chatSession))
    await expect(bridge.closeChatSession(closeRequest)).resolves.toEqual(success({
      schema_version: 1,
      session_id: chatSessionId,
      closed: true,
    }))
    expect(electron.invoke).toHaveBeenNthCalledWith(1, desktopChannels.getChatCapability)
    expect(electron.invoke).toHaveBeenNthCalledWith(2, desktopChannels.createChatSession, chatCreateRequest)
    expect(electron.invoke).toHaveBeenNthCalledWith(3, desktopChannels.closeChatSession, closeRequest)

    await expect(bridge.createChatSession({
      ...chatCreateRequest,
      data_classes: [],
    })).rejects.toThrow('Invalid chat session request')
    await expect(bridge.closeChatSession({ schema_version: 1, session_id: '../private' })).rejects.toThrow()
    expect(electron.invoke).toHaveBeenCalledTimes(3)
  })

  it('rejects malformed chat lifecycle responses', async () => {
    electron.invoke.mockResolvedValue(success({ ...chatSession, payload_retention: true }))
    const bridge = await loadBridge()

    await expect(bridge.createChatSession(chatCreateRequest)).rejects.toThrow('Invalid bridge response')
  })

  it('validates confirmed navigation and plain-text copy across the preload boundary', async () => {
    electron.invoke.mockImplementation((channel: string, request: unknown) => {
      if (channel === desktopChannels.openExternalLink) {
        return Promise.resolve(success({
          schema_version: 1,
          destination: (request as { destination: string }).destination,
          status: 'opened' as const,
        }))
      }
      return Promise.resolve(success({ schema_version: 1, copied: true as const }))
    })
    const bridge = await loadBridge()
    const linkRequest = {
      schema_version: 1 as const,
      destination: 'https://EXAMPLE.org/research?q=family',
    }
    const copyRequest = { schema_version: 1 as const, text: 'First line\nSecond line' }

    await expect(bridge.openExternalLink(linkRequest)).resolves.toEqual(success({
      schema_version: 1,
      destination: 'https://example.org/research?q=family',
      status: 'opened',
    }))
    await expect(bridge.copyText(copyRequest)).resolves.toEqual(success({
      schema_version: 1,
      copied: true,
    }))
    expect(electron.invoke).toHaveBeenNthCalledWith(1, desktopChannels.openExternalLink, {
      schema_version: 1,
      destination: 'https://example.org/research?q=family',
    })
    expect(electron.invoke).toHaveBeenNthCalledWith(2, desktopChannels.copyText, copyRequest)

    await expect(bridge.openExternalLink({
      schema_version: 1,
      destination: 'http://example.org/',
    })).rejects.toThrow('Invalid external-link request')
    await expect(bridge.copyText({ schema_version: 1, text: '\u0000private' })).rejects.toThrow(
      'Invalid copy-text request',
    )
    expect(electron.invoke).toHaveBeenCalledTimes(2)
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
    const registration = electron.on.mock.calls[0]
    expect(registration?.[0]).toBe(desktopEventChannels.jobEvent)
    expect(typeof registration?.[1]).toBe('function')
    const ipcListener = registration?.[1] as (...args: unknown[]) => void
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

  it('delivers only validated chat batches and cleans up the exact listener once', async () => {
    const bridge = await loadBridge()
    const listener = vi.fn()
    const cleanup = bridge.onChatEventBatch(listener)
    expect(electron.on).toHaveBeenCalledOnce()
    const registration = electron.on.mock.calls[0]
    expect(registration?.[0]).toBe(desktopEventChannels.chatEventBatch)
    const ipcListener = registration?.[1] as (...args: unknown[]) => void
    const event = {
      schema_version: 1 as const,
      run_id: `run_${'b'.repeat(32)}`,
      sequence: 1,
      type: 'delta' as const,
      timestamp: '2026-08-13T12:00:00Z',
      payload: {
        text: 'Hello',
        code: null,
        provider_id: null,
        model: null,
        remote: null,
        message_count: null,
      },
    }
    const delivery = {
      schema_version: 1 as const,
      kind: 'batch' as const,
      run_id: `run_${'b'.repeat(32)}`,
      session_id: chatSessionId,
      from_sequence: 1,
      through_sequence: 1,
      encoded_bytes: new TextEncoder().encode(JSON.stringify([event])).length,
      events: [event],
      error: null,
    }

    ipcListener({}, delivery)
    ipcListener({}, { ...delivery, session_id: '../private' })

    expect(listener).toHaveBeenCalledOnce()
    expect(listener).toHaveBeenCalledWith(delivery)
    cleanup()
    cleanup()
    expect(electron.removeListener).toHaveBeenCalledOnce()
    expect(electron.removeListener).toHaveBeenCalledWith(desktopEventChannels.chatEventBatch, ipcListener)
  })
})
