/** Owns bounded, acknowledged renderer chat streams in the Electron main process. */

import {
  CHAT_STREAM_BATCH_MAX_BYTES,
  CHAT_STREAM_BATCH_WINDOW_MS,
  CHAT_STREAM_MAX_UNACKNOWLEDGED_BYTES,
  DESKTOP_PROTOCOL_VERSION,
  type BridgeErrorCode,
  type BridgeResult,
  type ChatEvent,
  type ChatEventDelivery,
  type ChatStreamAcknowledgement,
  type ChatStreamAckRequest,
  type ChatStreamCancelRequest,
  type ChatStreamRun,
} from '../shared-contract/desktop'
import { parseChatEventDelivery } from '../shared-contract/runtime'
import {
  SidecarClientError,
  type ChatEventFlowControl,
  type ChatEventStreamRequest,
} from './sidecar-client'

const DEFAULT_BACKPRESSURE_TIMEOUT_MS = 15_000
const DEFAULT_RECONNECT_LIMIT = 1
const DEFAULT_ACTIVE_STREAM_LIMIT = 4

type Timeout = ReturnType<typeof setTimeout>

export interface ChatStreamBridge {
  cancelChatStream(
    request: ChatStreamCancelRequest,
    signal?: AbortSignal,
  ): Promise<BridgeResult<ChatStreamRun>>
  streamChatEvents(
    request: ChatEventStreamRequest,
    listener: (event: Readonly<ChatEvent>, flow: Readonly<ChatEventFlowControl>) => void,
    signal?: AbortSignal,
  ): Promise<void>
}

export interface ChatStreamControllerOptions {
  readonly deliver: (delivery: Readonly<ChatEventDelivery>) => void
  readonly isOwnerActive: () => boolean
  readonly batchWindowMs?: number
  readonly batchMaxBytes?: number
  readonly maxUnacknowledgedBytes?: number
  readonly backpressureTimeoutMs?: number
  readonly reconnectLimit?: number
  readonly activeStreamLimit?: number
}

interface Debt {
  readonly throughSequence: number
  readonly bytes: number
}

interface StreamState {
  readonly run: Readonly<ChatStreamRun>
  readonly controller: AbortController
  readonly queued: Readonly<ChatEvent>[]
  readonly debt: Debt[]
  receivedSequence: number
  deliveredSequence: number
  acknowledgedSequence: number
  unacknowledgedBytes: number
  reconnects: number
  flow?: Readonly<ChatEventFlowControl>
  batchTimer?: Timeout
  backpressureTimer?: Timeout
  paused: boolean
  terminalSeen: boolean
  terminalDelivered: boolean
  closed: boolean
}

function success<T>(data: Readonly<T>): BridgeResult<T> {
  return Object.freeze({ ok: true, protocolVersion: DESKTOP_PROTOCOL_VERSION, data })
}

function failure<T>(
  code: BridgeErrorCode,
  message: string,
  remediation: string,
): BridgeResult<T> {
  return Object.freeze({
    ok: false,
    protocolVersion: DESKTOP_PROTOCOL_VERSION,
    error: Object.freeze({ code, message, remediation }),
  })
}

function encodedBytes(events: readonly Readonly<ChatEvent>[]): number {
  return Buffer.byteLength(JSON.stringify(events), 'utf8')
}

function terminal(event: Readonly<ChatEvent>): boolean {
  return event.type === 'completed' || event.type === 'interrupted' || event.type === 'failed'
}

function streamKey(sessionId: string, runId: string): string {
  return `${sessionId}:${runId}`
}

function acknowledgement(
  request: Readonly<ChatStreamAckRequest>,
): BridgeResult<ChatStreamAcknowledgement> {
  return success(Object.freeze({
    schema_version: 1,
    session_id: request.session_id,
    run_id: request.run_id,
    through_sequence: request.through_sequence,
    acknowledged: true,
  }))
}

function mapStreamFailure(cause: unknown): Readonly<{
  code: BridgeErrorCode
  message: string
  remediation: string
}> {
  const reason = cause instanceof SidecarClientError ? cause.reason : null
  if (reason === 'chat_stream_replay_expired') {
    return Object.freeze({
      code: 'CHAT_STREAM_REPLAY_EXPIRED',
      message: 'Earlier chat output is no longer available.',
      remediation: 'Start a new response; the original provider request was not retried.',
    })
  }
  if (reason === 'chat_stream_cursor_invalid') {
    return Object.freeze({
      code: 'CHAT_STREAM_CURSOR_INVALID',
      message: 'Chat output could not resume from that point.',
      remediation: 'Start a new response from the current conversation.',
    })
  }
  if (reason === 'chat_stream_not_found' || reason === 'chat_session_not_found') {
    return Object.freeze({
      code: 'CHAT_STREAM_NOT_FOUND',
      message: 'The selected chat response is no longer available.',
      remediation: 'Start a new response from the current conversation.',
    })
  }
  if (reason === 'chat_stream_limit') {
    return Object.freeze({
      code: 'CHAT_STREAM_LIMIT',
      message: 'The chat streaming limit has been reached.',
      remediation: 'Cancel or finish another response before trying again.',
    })
  }
  if (reason === 'chat_stream_service_unavailable' || reason === 'unavailable') {
    return Object.freeze({
      code: 'CHAT_STREAM_SERVICE_UNAVAILABLE',
      message: 'Chat streaming is unavailable.',
      remediation: 'Retry the private service or restart AncestryLLM.',
    })
  }
  if (reason === 'chat_event_stream_failed' || reason === 'invalid_response') {
    return Object.freeze({
      code: 'CHAT_STREAM_EVENT_INVALID',
      message: 'Chat output failed strict stream validation.',
      remediation: 'Start a new response; the invalid stream was cancelled.',
    })
  }
  return Object.freeze({
    code: 'CHAT_STREAM_STALLED',
    message: 'Chat output ended before a terminal event arrived.',
    remediation: 'Start a new response; the original provider request was not retried.',
  })
}

/** Owns one renderer's bounded, acknowledged chat streams in the Electron main process. */
export class ChatStreamController {
  private readonly streams = new Map<string, StreamState>()
  private readonly batchWindowMs: number
  private readonly batchMaxBytes: number
  private readonly maxUnacknowledgedBytes: number
  private readonly backpressureTimeoutMs: number
  private readonly reconnectLimit: number
  private readonly activeStreamLimit: number

  constructor(
    private readonly bridge: Readonly<ChatStreamBridge>,
    private readonly options: Readonly<ChatStreamControllerOptions>,
  ) {
    this.batchWindowMs = options.batchWindowMs ?? CHAT_STREAM_BATCH_WINDOW_MS
    this.batchMaxBytes = options.batchMaxBytes ?? CHAT_STREAM_BATCH_MAX_BYTES
    this.maxUnacknowledgedBytes = options.maxUnacknowledgedBytes
      ?? CHAT_STREAM_MAX_UNACKNOWLEDGED_BYTES
    this.backpressureTimeoutMs = options.backpressureTimeoutMs
      ?? DEFAULT_BACKPRESSURE_TIMEOUT_MS
    this.reconnectLimit = options.reconnectLimit ?? DEFAULT_RECONNECT_LIMIT
    this.activeStreamLimit = options.activeStreamLimit ?? DEFAULT_ACTIVE_STREAM_LIMIT
    if (!Number.isFinite(this.batchWindowMs) || this.batchWindowMs <= 0
      || !Number.isSafeInteger(this.batchMaxBytes) || this.batchMaxBytes <= 0
      || this.batchMaxBytes > CHAT_STREAM_BATCH_MAX_BYTES
      || !Number.isSafeInteger(this.maxUnacknowledgedBytes)
      || this.maxUnacknowledgedBytes < this.batchMaxBytes
      || this.maxUnacknowledgedBytes > CHAT_STREAM_MAX_UNACKNOWLEDGED_BYTES
      || !Number.isFinite(this.backpressureTimeoutMs) || this.backpressureTimeoutMs <= 0
      || !Number.isSafeInteger(this.reconnectLimit) || this.reconnectLimit < 0
      || !Number.isSafeInteger(this.activeStreamLimit) || this.activeStreamLimit <= 0) {
      throw new Error('Chat stream controller limits are invalid.')
    }
  }

  attach(run: Readonly<ChatStreamRun>): BridgeResult<ChatStreamRun> {
    const key = streamKey(run.session_id, run.run_id)
    if (this.streams.has(key)) return success(run)
    if (run.terminal) return success(run)
    if (this.streams.size >= this.activeStreamLimit) {
      return failure(
        'CHAT_STREAM_LIMIT',
        'The renderer chat streaming limit has been reached.',
        'Cancel or finish another response before trying again.',
      )
    }
    const state: StreamState = {
      run,
      controller: new AbortController(),
      queued: [],
      debt: [],
      receivedSequence: 0,
      deliveredSequence: 0,
      acknowledgedSequence: 0,
      unacknowledgedBytes: 0,
      reconnects: 0,
      paused: false,
      terminalSeen: false,
      terminalDelivered: false,
      closed: false,
    }
    this.streams.set(key, state)
    void this.connect(state)
    return success(run)
  }

  acknowledge(
    request: Readonly<ChatStreamAckRequest>,
  ): BridgeResult<ChatStreamAcknowledgement> {
    const state = this.streams.get(streamKey(request.session_id, request.run_id))
    if (state === undefined || state.closed) {
      return failure(
        'CHAT_STREAM_NOT_FOUND',
        'The selected chat response is no longer available.',
        'Start a new response from the current conversation.',
      )
    }
    if (request.through_sequence <= state.acknowledgedSequence) {
      return acknowledgement(request)
    }
    const boundary = state.debt.findIndex(
      (entry) => entry.throughSequence === request.through_sequence,
    )
    if (boundary < 0) {
      return failure(
        'CHAT_STREAM_CURSOR_INVALID',
        'The acknowledgement does not match a delivered chat batch.',
        'Reload the conversation before retrying.',
      )
    }
    const released = state.debt.splice(0, boundary + 1)
    state.unacknowledgedBytes -= released.reduce((total, entry) => total + entry.bytes, 0)
    state.acknowledgedSequence = request.through_sequence
    this.resume(state)
    this.drain(state)
    this.finishIfAcknowledged(state)
    return acknowledgement(request)
  }

  disposeSession(sessionId: string): void {
    for (const state of [...this.streams.values()]) {
      if (state.run.session_id === sessionId) this.close(state, true)
    }
  }

  dispose(): void {
    for (const state of [...this.streams.values()]) this.close(state, true)
  }

  private async connect(state: StreamState): Promise<void> {
    while (!state.closed && !state.controller.signal.aborted && this.options.isOwnerActive()) {
      const request: ChatEventStreamRequest = Object.freeze({
        schema_version: 1,
        session_id: state.run.session_id,
        run_id: state.run.run_id,
        after: state.receivedSequence,
      })
      try {
        await this.bridge.streamChatEvents(
          request,
          (event, flow) => this.accept(state, event, flow),
          state.controller.signal,
        )
        if (state.closed || state.controller.signal.aborted) return
        if (state.terminalSeen) {
          this.drain(state)
          this.finishIfAcknowledged(state)
          return
        }
        throw new SidecarClientError('chat_event_stream_interrupted')
      } catch (cause) {
        if (state.closed || state.controller.signal.aborted) return
        if (cause instanceof SidecarClientError
          && cause.reason === 'chat_event_stream_interrupted'
          && state.reconnects < this.reconnectLimit) {
          state.reconnects += 1
          continue
        }
        this.fail(state, mapStreamFailure(cause))
        return
      }
    }
    if (!state.closed) this.close(state, true)
  }

  private accept(
    state: StreamState,
    event: Readonly<ChatEvent>,
    flow: Readonly<ChatEventFlowControl>,
  ): void {
    if (state.closed || this.streams.get(streamKey(
      state.run.session_id,
      state.run.run_id,
    )) !== state) return
    if (!this.options.isOwnerActive()) {
      this.close(state, true)
      return
    }
    if (event.run_id !== state.run.run_id || event.sequence !== state.receivedSequence + 1
      || state.terminalSeen) {
      this.fail(state, mapStreamFailure(new SidecarClientError('chat_event_stream_failed')))
      return
    }
    state.flow = flow
    state.receivedSequence = event.sequence
    state.terminalSeen = terminal(event)
    state.queued.push(event)
    if (encodedBytes([event]) > this.batchMaxBytes) {
      this.fail(state, mapStreamFailure(new SidecarClientError('chat_event_stream_failed')))
      return
    }
    if (state.terminalSeen || encodedBytes(state.queued) > this.batchMaxBytes) {
      this.clearBatchTimer(state)
      this.drain(state)
      return
    }
    if (state.batchTimer === undefined) {
      state.batchTimer = setTimeout(() => {
        delete state.batchTimer
        this.drain(state)
      }, this.batchWindowMs)
    }
  }

  private drain(state: StreamState): void {
    if (state.closed || !this.options.isOwnerActive()) return
    while (state.queued.length > 0) {
      let count = 1
      let bytes = encodedBytes(state.queued.slice(0, count))
      while (count < state.queued.length) {
        const candidateBytes = encodedBytes(state.queued.slice(0, count + 1))
        if (candidateBytes > this.batchMaxBytes) break
        count += 1
        bytes = candidateBytes
      }
      if (state.unacknowledgedBytes + bytes > this.maxUnacknowledgedBytes) {
        this.pause(state)
        return
      }
      const events = state.queued.splice(0, count)
      const fromSequence = events[0]?.sequence
      const throughSequence = events.at(-1)?.sequence
      if (fromSequence === undefined || throughSequence === undefined) return
      try {
        const delivery = parseChatEventDelivery({
          schema_version: 1,
          kind: 'batch',
          session_id: state.run.session_id,
          run_id: state.run.run_id,
          from_sequence: fromSequence,
          through_sequence: throughSequence,
          encoded_bytes: bytes,
          events,
          error: null,
        })
        this.options.deliver(delivery)
      } catch {
        this.fail(state, mapStreamFailure(new SidecarClientError('chat_event_stream_failed')))
        return
      }
      state.deliveredSequence = throughSequence
      state.unacknowledgedBytes += bytes
      state.debt.push({ throughSequence, bytes })
      if (terminal(events.at(-1)!)) state.terminalDelivered = true
      if (state.unacknowledgedBytes >= this.maxUnacknowledgedBytes) {
        this.pause(state)
        return
      }
    }
    this.finishIfAcknowledged(state)
  }

  private pause(state: StreamState): void {
    if (state.closed || state.paused) return
    state.paused = true
    state.flow?.pause()
    state.backpressureTimer = setTimeout(() => {
      this.fail(state, Object.freeze({
        code: 'CHAT_STREAM_BACKPRESSURE_TIMEOUT',
        message: 'The chat renderer stopped acknowledging output.',
        remediation: 'Reload the conversation and start a new response.',
      }))
    }, this.backpressureTimeoutMs)
  }

  private resume(state: StreamState): void {
    if (!state.paused || state.closed) return
    if (state.backpressureTimer !== undefined) clearTimeout(state.backpressureTimer)
    delete state.backpressureTimer
    state.paused = false
    state.flow?.resume()
  }

  private fail(
    state: StreamState,
    diagnostic: Readonly<{ code: BridgeErrorCode; message: string; remediation: string }>,
  ): void {
    if (state.closed) return
    if (this.options.isOwnerActive()) {
      try {
        this.options.deliver(parseChatEventDelivery({
          schema_version: 1,
          kind: 'failure',
          session_id: state.run.session_id,
          run_id: state.run.run_id,
          from_sequence: null,
          through_sequence: null,
          encoded_bytes: 0,
          events: null,
          error: diagnostic,
        }))
      } catch {
        // Invalid or unavailable renderers are handled by the owner lifecycle.
      }
    }
    this.close(state, true)
  }

  private close(state: StreamState, cancel: boolean): void {
    if (state.closed) return
    state.closed = true
    this.clearBatchTimer(state)
    if (state.backpressureTimer !== undefined) clearTimeout(state.backpressureTimer)
    state.controller.abort()
    this.streams.delete(streamKey(state.run.session_id, state.run.run_id))
    if (cancel && !state.terminalSeen) {
      void this.bridge.cancelChatStream(Object.freeze({
        schema_version: 1,
        session_id: state.run.session_id,
        run_id: state.run.run_id,
      })).catch(() => undefined)
    }
  }

  private clearBatchTimer(state: StreamState): void {
    if (state.batchTimer !== undefined) clearTimeout(state.batchTimer)
    delete state.batchTimer
  }

  private finishIfAcknowledged(state: StreamState): void {
    if (state.terminalDelivered && state.queued.length === 0 && state.debt.length === 0) {
      this.close(state, false)
    }
  }
}
