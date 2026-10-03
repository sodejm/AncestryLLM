/** Verifies chat streaming ownership, batching, replay, cancellation, and backpressure. */

import { afterEach, describe, expect, it, vi } from 'vitest'
import type {
  BridgeResult,
  ChatEvent,
  ChatEventDelivery,
  ChatStreamRun,
} from '../shared-contract/desktop'
import {
  ChatStreamController,
  type ChatStreamBridge,
} from './chat-stream-controller'
import {
  SidecarClientError,
  type ChatEventFlowControl,
  type ChatEventStreamRequest,
} from './sidecar-client'

const sessionId = `chat_${'a'.repeat(32)}`
const runId = `run_${'b'.repeat(32)}`

const run = Object.freeze({
  schema_version: 1,
  session_id: sessionId,
  run_id: runId,
  state: 'active',
  latest_sequence: 1,
  terminal: false,
} satisfies ChatStreamRun)

const flow = (): Readonly<ChatEventFlowControl> => Object.freeze({
  pause: vi.fn(),
  resume: vi.fn(),
})

function event(
  sequence: number,
  type: ChatEvent['type'],
  text: string | null = null,
): Readonly<ChatEvent> {
  const payload = {
    text: type === 'first-token' || type === 'delta' ? text : null,
    code: type === 'interrupted' || type === 'failed' ? 'provider_interrupted' : null,
    provider_id: type === 'active' ? 'openai' : null,
    model: type === 'active' ? 'gpt-test' : null,
    remote: type === 'active' ? true : null,
    message_count: type === 'completed' ? 2 : null,
  }
  return Object.freeze({
    schema_version: 1,
    run_id: runId,
    sequence,
    type,
    timestamp: `2026-08-13T12:00:00.${String(sequence).padStart(6, '0')}+00:00`,
    payload: Object.freeze(payload),
  })
}

function pendingBridge(): Readonly<{
  bridge: ChatStreamBridge
  listener: () => ((
    item: Readonly<ChatEvent>,
    control: Readonly<ChatEventFlowControl>,
  ) => void)
}> {
  let next: ((
    item: Readonly<ChatEvent>,
    control: Readonly<ChatEventFlowControl>,
  ) => void) | undefined
  const bridge: ChatStreamBridge = {
    cancelChatStream: vi.fn().mockResolvedValue({
      ok: true,
      protocolVersion: '1',
      data: { ...run, state: 'interrupted', terminal: true, latest_sequence: 2 },
    } satisfies BridgeResult<ChatStreamRun>),
    streamChatEvents: vi.fn().mockImplementation((_request, listener) => {
      next = listener
      return new Promise<void>(() => undefined)
    }),
  }
  return {
    bridge,
    listener: () => {
      if (next === undefined) throw new Error('Stream listener was not attached.')
      return next
    },
  }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('chat stream controller', () => {
  it('coalesces events for at most 16 milliseconds and acknowledges exact batches', async () => {
    vi.useFakeTimers()
    const source = pendingBridge()
    const deliveries: Readonly<ChatEventDelivery>[] = []
    const controller = new ChatStreamController(source.bridge, {
      deliver: (delivery) => deliveries.push(delivery),
      isOwnerActive: () => true,
    })

    expect(controller.attach(run)).toMatchObject({ ok: true })
    const control = flow()
    source.listener()(event(1, 'active'), control)
    source.listener()(event(2, 'first-token', 'Hello'), control)

    await vi.advanceTimersByTimeAsync(15)
    expect(deliveries).toEqual([])
    await vi.advanceTimersByTimeAsync(1)
    expect(deliveries).toHaveLength(1)
    expect(deliveries[0]).toMatchObject({
      kind: 'batch',
      from_sequence: 1,
      through_sequence: 2,
    })

    expect(controller.acknowledge({
      schema_version: 1,
      session_id: sessionId,
      run_id: runId,
      through_sequence: 1,
    })).toMatchObject({ ok: false, error: { code: 'CHAT_STREAM_CURSOR_INVALID' } })
    expect(controller.acknowledge({
      schema_version: 1,
      session_id: sessionId,
      run_id: runId,
      through_sequence: 2,
    })).toMatchObject({ ok: true, data: { acknowledged: true } })
    expect(controller.acknowledge({
      schema_version: 1,
      session_id: sessionId,
      run_id: runId,
      through_sequence: 2,
    })).toMatchObject({ ok: true })
    controller.dispose()
  })

  it('splits output into independently validated batches no larger than 4 KiB', () => {
    const source = pendingBridge()
    const deliveries: Readonly<ChatEventDelivery>[] = []
    const controller = new ChatStreamController(source.bridge, {
      deliver: (delivery) => deliveries.push(delivery),
      isOwnerActive: () => true,
    })
    controller.attach(run)
    const control = flow()

    source.listener()(event(1, 'first-token', 'a'.repeat(1_900)), control)
    source.listener()(event(2, 'delta', 'b'.repeat(1_900)), control)

    expect(deliveries).toHaveLength(2)
    expect(deliveries.every((delivery) => (
      delivery.kind === 'batch' && delivery.encoded_bytes <= 4_096
    ))).toBe(true)
    expect(deliveries.map((delivery) => delivery.kind === 'batch'
      ? [delivery.from_sequence, delivery.through_sequence]
      : null)).toEqual([[1, 1], [2, 2]])
    controller.dispose()
  })

  it('pauses upstream at the byte-debt cap and resumes only after an exact acknowledgement', async () => {
    vi.useFakeTimers()
    const source = pendingBridge()
    const deliveries: Readonly<ChatEventDelivery>[] = []
    const controller = new ChatStreamController(source.bridge, {
      deliver: (delivery) => deliveries.push(delivery),
      isOwnerActive: () => true,
      batchMaxBytes: 512,
      maxUnacknowledgedBytes: 512,
    })
    controller.attach(run)
    const control = flow()

    source.listener()(event(1, 'first-token', 'a'.repeat(160)), control)
    await vi.advanceTimersByTimeAsync(16)
    source.listener()(event(2, 'delta', 'b'.repeat(160)), control)
    await vi.advanceTimersByTimeAsync(16)

    expect(deliveries).toHaveLength(1)
    expect(control.pause).toHaveBeenCalledOnce()
    expect(control.resume).not.toHaveBeenCalled()

    expect(controller.acknowledge({
      schema_version: 1,
      session_id: sessionId,
      run_id: runId,
      through_sequence: 1,
    })).toMatchObject({ ok: true })
    expect(control.resume).toHaveBeenCalledOnce()
    expect(deliveries).toHaveLength(2)
    controller.dispose()
  })

  it('cancels a stalled stream with a sanitized terminal delivery', async () => {
    vi.useFakeTimers()
    const source = pendingBridge()
    const deliveries: Readonly<ChatEventDelivery>[] = []
    const controller = new ChatStreamController(source.bridge, {
      deliver: (delivery) => deliveries.push(delivery),
      isOwnerActive: () => true,
      batchMaxBytes: 512,
      maxUnacknowledgedBytes: 512,
      backpressureTimeoutMs: 25,
    })
    controller.attach(run)
    const control = flow()
    source.listener()(event(1, 'first-token', 'a'.repeat(160)), control)
    await vi.advanceTimersByTimeAsync(16)
    source.listener()(event(2, 'delta', 'b'.repeat(160)), control)
    await vi.advanceTimersByTimeAsync(16)
    await vi.advanceTimersByTimeAsync(25)

    expect(deliveries.at(-1)).toMatchObject({
      kind: 'failure',
      error: { code: 'CHAT_STREAM_BACKPRESSURE_TIMEOUT' },
    })
    expect(source.bridge.cancelChatStream).toHaveBeenCalledWith({
      schema_version: 1,
      session_id: sessionId,
      run_id: runId,
    })
  })

  it('reconnects once to the same run and cursor without retrying the provider request', async () => {
    const deliveries: Readonly<ChatEventDelivery>[] = []
    const control = flow()
    const streamChatEvents = vi.fn()
      .mockImplementationOnce(async (
        _request: Readonly<ChatEventStreamRequest>,
        listener: (item: Readonly<ChatEvent>, flow: Readonly<ChatEventFlowControl>) => void,
      ) => {
        listener(event(1, 'active'), control)
        throw new SidecarClientError('chat_event_stream_interrupted')
      })
      .mockImplementationOnce(async (
        _request: Readonly<ChatEventStreamRequest>,
        listener: (item: Readonly<ChatEvent>, flow: Readonly<ChatEventFlowControl>) => void,
      ) => {
        listener(event(2, 'first-token', 'Hello'), control)
        listener(event(3, 'completed'), control)
      })
    const bridge: ChatStreamBridge = {
      cancelChatStream: vi.fn(),
      streamChatEvents,
    }
    const controller = new ChatStreamController(bridge, {
      deliver: (delivery) => deliveries.push(delivery),
      isOwnerActive: () => true,
    })

    controller.attach(run)
    await vi.waitFor(() => expect(streamChatEvents).toHaveBeenCalledTimes(2))

    expect(streamChatEvents.mock.calls.map(([request]) => request)).toEqual([
      { schema_version: 1, session_id: sessionId, run_id: runId, after: 0 },
      { schema_version: 1, session_id: sessionId, run_id: runId, after: 1 },
    ])
    expect(deliveries).toHaveLength(1)
    expect(deliveries[0]).toMatchObject({
      kind: 'batch',
      from_sequence: 1,
      through_sequence: 3,
    })
    expect(bridge.cancelChatStream).not.toHaveBeenCalled()
    controller.dispose()
  })

  it('cancels only streams that belong to an explicitly closed session', () => {
    const source = pendingBridge()
    const controller = new ChatStreamController(source.bridge, {
      deliver: vi.fn(),
      isOwnerActive: () => true,
    })
    controller.attach(run)

    controller.disposeSession(sessionId)

    expect(source.bridge.cancelChatStream).toHaveBeenCalledWith({
      schema_version: 1,
      session_id: sessionId,
      run_id: runId,
    })
    expect(controller.acknowledge({
      schema_version: 1,
      session_id: sessionId,
      run_id: runId,
      through_sequence: 1,
    })).toMatchObject({ ok: false, error: { code: 'CHAT_STREAM_NOT_FOUND' } })
  })

  it('fails closed and cancels on nonmonotonic or oversized output', () => {
    const source = pendingBridge()
    const deliveries: Readonly<ChatEventDelivery>[] = []
    const controller = new ChatStreamController(source.bridge, {
      deliver: (delivery) => deliveries.push(delivery),
      isOwnerActive: () => true,
    })
    controller.attach(run)

    source.listener()(event(2, 'delta', 'out of order'), flow())

    expect(deliveries).toEqual([expect.objectContaining({
      kind: 'failure',
      error: expect.objectContaining({ code: 'CHAT_STREAM_EVENT_INVALID' }),
    })])
    expect(source.bridge.cancelChatStream).toHaveBeenCalledOnce()

    const oversized = pendingBridge()
    const oversizedDeliveries: Readonly<ChatEventDelivery>[] = []
    const second = new ChatStreamController(oversized.bridge, {
      deliver: (delivery) => oversizedDeliveries.push(delivery),
      isOwnerActive: () => true,
    })
    second.attach(run)
    oversized.listener()(event(1, 'first-token', 'x'.repeat(5_000)), flow())
    expect(oversizedDeliveries.at(-1)).toMatchObject({
      kind: 'failure',
      error: { code: 'CHAT_STREAM_EVENT_INVALID' },
    })
    expect(oversized.bridge.cancelChatStream).toHaveBeenCalledOnce()
  })
})
