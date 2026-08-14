/** Verifies exact chat-session contracts and hostile structured-clone rejection. */
import { describe, expect, it } from 'vitest'
import {
  parseChatCapabilityResult,
  parseChatSessionClosureResult,
  parseChatSessionCreateRequest,
  parseChatSessionRequest,
  parseChatSessionResult,
} from './runtime'

const sessionId = `chat_${'a'.repeat(32)}`

const createRequest = Object.freeze({
  schema_version: 1,
  provider_profile_name: 'local-test',
  model: 'fictional-model',
  purpose: 'genealogy_analysis',
  data_classes: ['deceased_person', 'public_genealogy'],
  consent_name: null,
})

const session = Object.freeze({
  schema_version: 1,
  session_id: sessionId,
  provider_profile_name: 'local-test',
  provider_id: 'ollama',
  model: 'fictional-model',
  purpose: 'genealogy_analysis',
  data_classes: ['deceased_person', 'public_genealogy'],
  remote: false,
  consent_name: null,
  message_count: 0,
  transient: true,
  payload_retention: false,
})

const capability = Object.freeze({
  schema_version: 1,
  max_active_sessions: 32,
  max_messages: 32,
  max_message_characters: 16_384,
  max_context_characters: 65_536,
  max_output_tokens: 4_096,
  max_temperature: 1,
  max_timeout_seconds: 120,
  max_safe_retries: 1,
  transient: true,
  tools_enabled: false,
  payload_retention: false,
  output_is_evidence: false,
  streaming: true,
  stream_replay_max_bytes: 262_144,
})

const success = (data: unknown) => ({ ok: true, protocolVersion: '1', data })

describe('chat session bridge contracts', () => {
  it('accepts the strict schema-v1 session request and freezes it', () => {
    const parsed = parseChatSessionCreateRequest(createRequest)

    expect(parsed).toEqual(createRequest)
    expect(Object.isFrozen(parsed)).toBe(true)
    expect(Object.isFrozen(parsed.data_classes)).toBe(true)
    expect(parseChatSessionRequest({ schema_version: 1, session_id: sessionId })).toEqual({
      schema_version: 1,
      session_id: sessionId,
    })
  })

  it.each([
    { ...createRequest, schema_version: 2 },
    { ...createRequest, provider_profile_name: ' ' },
    { ...createRequest, model: 'x'.repeat(201) },
    { ...createRequest, purpose: 'unspecified' },
    { ...createRequest, data_classes: [] },
    { ...createRequest, data_classes: ['deceased_person', 'deceased_person'] },
    { ...createRequest, consent_name: '' },
    { ...createRequest, unexpected: true },
  ])('rejects a malformed or expanded creation request', (value) => {
    expect(() => parseChatSessionCreateRequest(value)).toThrow('Invalid chat session request')
  })

  it('accepts exact capability, session, and closure responses', () => {
    expect(parseChatCapabilityResult(success(capability))).toEqual(success(capability))
    expect(parseChatSessionResult(success(session))).toEqual(success(session))
    expect(parseChatSessionClosureResult(success({
      schema_version: 1,
      session_id: sessionId,
      closed: true,
    }))).toEqual(success({ schema_version: 1, session_id: sessionId, closed: true }))
  })

  it.each([
    { ...capability, tools_enabled: true },
    { ...capability, payload_retention: true },
    { ...capability, max_active_sessions: 31 },
    { ...capability, extra: 'field' },
  ])('fails closed when capability invariants drift', (value) => {
    expect(() => parseChatCapabilityResult(success(value))).toThrow('Invalid bridge response')
  })

  it.each([
    { ...session, session_id: 'chat_bad' },
    { ...session, remote: true, consent_name: null },
    { ...session, transient: false },
    { ...session, payload_retention: true },
    { ...session, message_count: 33 },
    { ...session, extra: 'field' },
  ])('fails closed on an unsafe or expanded chat session response', (value) => {
    expect(() => parseChatSessionResult(success(value))).toThrow('Invalid bridge response')
  })
})
