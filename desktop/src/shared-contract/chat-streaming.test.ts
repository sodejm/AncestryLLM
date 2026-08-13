/** Verifies strict chat streaming bridge parsing and encoded-byte accounting. */

import { describe, expect, it } from 'vitest'
import {
  parseChatEventDelivery,
  parseChatStreamAcknowledgementResult,
  parseChatStreamAckRequest,
  parseChatStreamCancelRequest,
  parseChatStreamRunResult,
  parseChatStreamStartRequest,
} from './runtime'

const sessionId = `chat_${'a'.repeat(32)}`
const runId = `run_${'b'.repeat(32)}`

const activeEvent = {
  schema_version: 1,
  run_id: runId,
  sequence: 1,
  type: 'active',
  timestamp: '2026-08-13T12:00:00+00:00',
  payload: {
    text: null,
    code: null,
    provider_id: 'openai',
    model: 'gpt-test',
    remote: true,
    message_count: null,
  },
} as const

const deltaEvent = {
  schema_version: 1,
  run_id: runId,
  sequence: 2,
  type: 'first-token',
  timestamp: '2026-08-13T12:00:00.001000+00:00',
  payload: {
    text: 'Hello',
    code: null,
    provider_id: null,
    model: null,
    remote: null,
    message_count: null,
  },
} as const

const startRequest = {
  schema_version: 1,
  session_id: sessionId,
  message: 'Summarize the fictional family.',
  max_output_tokens: 1024,
  temperature: 0,
  timeout_seconds: 60,
  max_safe_retries: 0,
} as const

const cancelRequest = {
  schema_version: 1,
  session_id: sessionId,
  run_id: runId,
} as const

const ackRequest = {
  schema_version: 1,
  session_id: sessionId,
  run_id: runId,
  through_sequence: 2,
} as const

const encodedBytes = (events: readonly unknown[]): number => (
  new TextEncoder().encode(JSON.stringify(events)).byteLength
)

const runResult = {
  ok: true,
  protocolVersion: '1',
  data: {
    schema_version: 1,
    session_id: sessionId,
    run_id: runId,
    state: 'active',
    latest_sequence: 1,
    terminal: false,
  },
} as const

describe('chat streaming desktop contract', () => {
  it('accepts exact start, cancel, and acknowledgement requests', () => {
    expect(parseChatStreamStartRequest(startRequest)).toEqual(startRequest)
    expect(parseChatStreamCancelRequest(cancelRequest)).toEqual(cancelRequest)
    expect(parseChatStreamAckRequest(ackRequest)).toEqual(ackRequest)
  })

  it('rejects unknown request fields and unsafe numeric values', () => {
    expect(() => parseChatStreamStartRequest({ ...startRequest, token: 'secret' }))
      .toThrow('Invalid chat-stream start request')
    expect(() => parseChatStreamStartRequest({ ...startRequest, temperature: Number.NaN }))
      .toThrow('Invalid chat-stream start request')
    expect(() => parseChatStreamAckRequest({ ...ackRequest, through_sequence: 0 }))
      .toThrow('Invalid chat-stream acknowledgement request')
    expect(() => parseChatStreamCancelRequest({ ...cancelRequest, run_id: `run_${'z'.repeat(32)}` }))
      .toThrow('Invalid chat-stream cancellation request')
  })

  it('accepts an exact run result and enforces terminal-state consistency', () => {
    expect(parseChatStreamRunResult(runResult)).toEqual(runResult)
    expect(() => parseChatStreamRunResult({
      ...runResult,
      data: { ...runResult.data, state: 'completed', terminal: false },
    })).toThrow('Invalid bridge response')
  })

  it('accepts a contiguous owner-matched event batch', () => {
    const delivery = {
      schema_version: 1,
      kind: 'batch',
      session_id: sessionId,
      run_id: runId,
      from_sequence: 1,
      through_sequence: 2,
      encoded_bytes: encodedBytes([activeEvent, deltaEvent]),
      events: [activeEvent, deltaEvent],
      error: null,
    } as const
    expect(parseChatEventDelivery(delivery)).toEqual(delivery)
  })

  it('preserves whitespace-only provider fragments', () => {
    const whitespace = {
      ...deltaEvent,
      payload: { ...deltaEvent.payload, text: ' ' },
    } as const
    expect(parseChatEventDelivery({
      schema_version: 1,
      kind: 'batch',
      session_id: sessionId,
      run_id: runId,
      from_sequence: 2,
      through_sequence: 2,
      encoded_bytes: encodedBytes([whitespace]),
      events: [whitespace],
      error: null,
    }).events?.[0]?.payload.text).toBe(' ')
  })

  it('rejects cross-run, noncontiguous, oversized, and invalid event payloads', () => {
    const delivery = {
      schema_version: 1,
      kind: 'batch',
      session_id: sessionId,
      run_id: runId,
      from_sequence: 1,
      through_sequence: 2,
      encoded_bytes: encodedBytes([activeEvent, deltaEvent]),
      events: [activeEvent, deltaEvent],
      error: null,
    } as const
    expect(() => parseChatEventDelivery({
      ...delivery,
      events: [activeEvent, { ...deltaEvent, run_id: `run_${'c'.repeat(32)}` }],
    })).toThrow('Invalid bridge response')
    expect(() => parseChatEventDelivery({
      ...delivery,
      events: [activeEvent, { ...deltaEvent, sequence: 3 }],
      through_sequence: 3,
    })).toThrow('Invalid bridge response')
    expect(() => parseChatEventDelivery({ ...delivery, encoded_bytes: 262_145 }))
      .toThrow('Invalid bridge response')
    expect(() => parseChatEventDelivery({
      ...delivery,
      events: [{
        ...activeEvent,
        payload: { ...activeEvent.payload, text: 'not permitted' },
      }, deltaEvent],
    })).toThrow('Invalid bridge response')
  })

  it('accepts sanitized failures and rejects paths in diagnostics', () => {
    const failure = {
      schema_version: 1,
      kind: 'failure',
      session_id: sessionId,
      run_id: runId,
      from_sequence: null,
      through_sequence: null,
      encoded_bytes: 0,
      events: null,
      error: {
        code: 'CHAT_STREAM_BACKPRESSURE_TIMEOUT',
        message: 'The renderer stopped acknowledging chat output.',
        remediation: 'Start a new chat request.',
      },
    } as const
    expect(parseChatEventDelivery(failure)).toEqual(failure)
    expect(() => parseChatEventDelivery({
      ...failure,
      error: { ...failure.error, message: '/tmp/secret' },
    })).toThrow('Invalid bridge response')
  })

  it('accepts only owner-matched acknowledgement results', () => {
    const result = {
      ok: true,
      protocolVersion: '1',
      data: {
        schema_version: 1,
        session_id: sessionId,
        run_id: runId,
        through_sequence: 2,
        acknowledged: true,
      },
    } as const
    expect(parseChatStreamAcknowledgementResult(result)).toEqual(result)
    expect(() => parseChatStreamAcknowledgementResult({
      ...result,
      data: { ...result.data, acknowledged: false },
    })).toThrow('Invalid bridge response')
  })
})
