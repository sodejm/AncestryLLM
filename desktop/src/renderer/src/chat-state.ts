/** Reduces owner-scoped chat events without weakening ordering or terminal state. */
import type { ChatEvent, ChatEventDelivery } from '../../shared-contract/desktop'

export type ChatResponseStatus =
  | 'starting'
  | 'streaming'
  | 'cancelling'
  | 'completed'
  | 'interrupted'
  | 'failed'

export interface ChatResponseState {
  readonly sessionId: string
  readonly runId: string
  readonly nextSequence: number
  readonly text: string
  readonly status: ChatResponseStatus
  readonly providerId: string | null
  readonly model: string | null
  readonly remote: boolean | null
  readonly messageCount: number | null
  readonly failureCode: string | null
}

export interface AppliedChatDelivery {
  readonly state: Readonly<ChatResponseState>
  readonly acknowledgeThrough: number | null
}

function frozenState(state: ChatResponseState): Readonly<ChatResponseState> {
  return Object.freeze(state)
}

export function createChatResponseState(
  sessionId: string,
  runId: string,
): Readonly<ChatResponseState> {
  return frozenState({
    sessionId,
    runId,
    nextSequence: 1,
    text: '',
    status: 'starting',
    providerId: null,
    model: null,
    remote: null,
    messageCount: null,
    failureCode: null,
  })
}

function interrupted(
  state: Readonly<ChatResponseState>,
  failureCode: string,
): AppliedChatDelivery {
  return Object.freeze({
    state: frozenState({ ...state, status: 'interrupted', failureCode }),
    acknowledgeThrough: null,
  })
}

function validBatchShape(delivery: Extract<ChatEventDelivery, { kind: 'batch' }>): boolean {
  if (delivery.events.length < 1) return false
  if (delivery.events[0]?.sequence !== delivery.from_sequence
    || delivery.events.at(-1)?.sequence !== delivery.through_sequence) return false
  return delivery.events.every((event, index) => (
    event.run_id === delivery.run_id
    && event.sequence === delivery.from_sequence + index
  ))
}

function applyEvent(
  state: ChatResponseState,
  event: Readonly<ChatEvent>,
): ChatResponseState {
  const next = { ...state, nextSequence: event.sequence + 1 }
  switch (event.type) {
    case 'active':
      return {
        ...next,
        status: 'streaming',
        providerId: event.payload.provider_id,
        model: event.payload.model,
        remote: event.payload.remote,
      }
    case 'first-token':
    case 'delta':
      return { ...next, status: 'streaming', text: state.text + (event.payload.text ?? '') }
    case 'cancelling':
      return { ...next, status: 'cancelling' }
    case 'completed':
      return { ...next, status: 'completed', messageCount: event.payload.message_count }
    case 'interrupted':
      return { ...next, status: 'interrupted', failureCode: event.payload.code }
    case 'failed':
      return { ...next, status: 'failed', failureCode: event.payload.code }
  }
}

export function applyChatDelivery(
  state: Readonly<ChatResponseState>,
  delivery: Readonly<ChatEventDelivery>,
): AppliedChatDelivery {
  if (delivery.session_id !== state.sessionId || delivery.run_id !== state.runId) {
    return interrupted(state, 'CHAT_STREAM_OWNER_MISMATCH')
  }
  if (delivery.kind === 'failure') {
    return Object.freeze({
      state: frozenState({ ...state, status: 'failed', failureCode: delivery.error.code }),
      acknowledgeThrough: null,
    })
  }
  if (!validBatchShape(delivery)) return interrupted(state, 'CHAT_STREAM_SEQUENCE_INVALID')
  if (delivery.through_sequence < state.nextSequence) {
    return Object.freeze({ state, acknowledgeThrough: delivery.through_sequence })
  }
  if (delivery.from_sequence > state.nextSequence) {
    return interrupted(state, 'CHAT_STREAM_SEQUENCE_INVALID')
  }

  const pending = delivery.events.filter((event) => event.sequence >= state.nextSequence)
  if (pending[0]?.sequence !== state.nextSequence) {
    return interrupted(state, 'CHAT_STREAM_SEQUENCE_INVALID')
  }
  let next: ChatResponseState = { ...state }
  for (const [index, event] of pending.entries()) {
    if (['completed', 'interrupted', 'failed'].includes(next.status)
      || (['completed', 'interrupted', 'failed'].includes(event.type)
        && index !== pending.length - 1)) {
      return interrupted(state, 'CHAT_STREAM_SEQUENCE_INVALID')
    }
    next = applyEvent(next, event)
  }
  return Object.freeze({
    state: frozenState(next),
    acknowledgeThrough: delivery.through_sequence,
  })
}
