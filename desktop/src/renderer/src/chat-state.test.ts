/** Verifies ordered chat event reduction, replay, deduplication, and fail-closed gaps. */
import { describe, expect, it } from 'vitest'
import type { ChatEvent, ChatEventDelivery } from '../../shared-contract/desktop'
import { applyChatDelivery, createChatResponseState } from './chat-state'

const sessionId = `chat_${'a'.repeat(32)}`
const runId = `run_${'b'.repeat(32)}`

function event(
  sequence: number,
  type: ChatEvent['type'],
  overrides: Partial<ChatEvent['payload']> = {},
): Readonly<ChatEvent> {
  return Object.freeze({
    schema_version: 1,
    run_id: runId,
    sequence,
    type,
    timestamp: `2026-08-13T12:00:00.${String(sequence).padStart(6, '0')}Z`,
    payload: Object.freeze({
      text: type === 'first-token' || type === 'delta' ? `part-${sequence}` : null,
      code: type === 'interrupted' || type === 'failed' ? 'FICTIONAL_STOP' : null,
      provider_id: type === 'active' ? 'ollama' : null,
      model: type === 'active' ? 'fictional-model' : null,
      remote: type === 'active' ? false : null,
      message_count: type === 'completed' ? 2 : null,
      ...overrides,
    }),
  })
}

function batch(events: readonly Readonly<ChatEvent>[]): Readonly<ChatEventDelivery> {
  const first = events[0]!
  const last = events.at(-1)!
  return Object.freeze({
    schema_version: 1,
    kind: 'batch',
    session_id: sessionId,
    run_id: runId,
    from_sequence: first.sequence,
    through_sequence: last.sequence,
    encoded_bytes: 256,
    events,
    error: null,
  })
}

describe('ordered renderer chat state', () => {
  it('assembles contiguous token batches exactly once and reconciles terminal state', () => {
    let state = createChatResponseState(sessionId, runId)
    state = applyChatDelivery(state, batch([
      event(1, 'active'),
      event(2, 'first-token', { text: 'Hello' }),
      event(3, 'delta', { text: ' world' }),
    ])).state

    expect(state.text).toBe('Hello world')
    expect(state.nextSequence).toBe(4)
    expect(state.status).toBe('streaming')
    expect(state.providerId).toBe('ollama')

    const replay = applyChatDelivery(state, batch([
      event(2, 'first-token', { text: 'Hello' }),
      event(3, 'delta', { text: ' world' }),
    ]))
    expect(replay.state).toBe(state)
    expect(replay.acknowledgeThrough).toBe(3)

    state = applyChatDelivery(state, batch([event(4, 'completed')])).state
    expect(state.text).toBe('Hello world')
    expect(state.status).toBe('completed')
    expect(state.messageCount).toBe(2)
  })

  it('fails closed without appending when a batch has a gap, duplicate sequence, or wrong owner', () => {
    const initial = createChatResponseState(sessionId, runId)
    const gap = applyChatDelivery(initial, batch([event(2, 'first-token')])).state
    expect(gap).toMatchObject({
      text: '',
      status: 'interrupted',
      failureCode: 'CHAT_STREAM_SEQUENCE_INVALID',
    })

    const duplicated = applyChatDelivery(initial, batch([
      event(1, 'active'),
      event(1, 'first-token'),
    ])).state
    expect(duplicated.status).toBe('interrupted')
    expect(duplicated.text).toBe('')

    const wrongOwner = applyChatDelivery(initial, {
      ...batch([event(1, 'active')]),
      session_id: `chat_${'c'.repeat(32)}`,
    }).state
    expect(wrongOwner.status).toBe('interrupted')
    expect(wrongOwner.failureCode).toBe('CHAT_STREAM_OWNER_MISMATCH')
  })

  it('preserves an explicit interrupted or bridge failure outcome for assistive status', () => {
    const interrupted = applyChatDelivery(
      createChatResponseState(sessionId, runId),
      batch([event(1, 'interrupted', { code: 'USER_CANCELLED' })]),
    ).state
    expect(interrupted).toMatchObject({ status: 'interrupted', failureCode: 'USER_CANCELLED' })

    const failed = applyChatDelivery(createChatResponseState(sessionId, runId), {
      schema_version: 1,
      kind: 'failure',
      session_id: sessionId,
      run_id: runId,
      from_sequence: null,
      through_sequence: null,
      encoded_bytes: 0,
      events: null,
      error: {
        code: 'CHAT_STREAM_STALLED',
        message: 'Chat stream stalled.',
        remediation: 'Start a new response.',
      },
    }).state
    expect(failed).toMatchObject({ status: 'failed', failureCode: 'CHAT_STREAM_STALLED' })
  })
})
