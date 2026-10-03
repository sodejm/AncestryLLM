/** Accessible transient-chat workspace contracts. */

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type {
  AncestryBridge,
  BridgeResult,
  ChatCapability,
  ChatEvent,
  ChatEventDelivery,
  ChatSession,
  ChatStreamRun,
  ProviderConfiguration,
} from '../../shared-contract/desktop'
import { createMockAncestryBridge } from '../../mock-bridge/desktop'
import { ChatWorkspace } from './ChatWorkspace'

const success = <T extends object>(data: T): BridgeResult<T> => ({
  ok: true,
  protocolVersion: '1',
  data,
})

const capability: ChatCapability = {
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
}

const localConfiguration: ProviderConfiguration = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  profiles: [{
    name: 'Local Ollama',
    provider_id: 'ollama',
    model: 'fictional-local-model',
    endpoint: 'http://127.0.0.1:11434',
    endpoint_kind: 'loopback',
    secret_reference: null,
    enabled: true,
  }],
  consents: [],
}

const remoteConfiguration: ProviderConfiguration = {
  schema_version: 1,
  revision: 'b'.repeat(64),
  profiles: [{
    name: 'Reviewed OpenAI',
    provider_id: 'openai',
    model: 'fictional-remote-model',
    endpoint: 'https://api.openai.com/v1',
    endpoint_kind: 'remote',
    secret_reference: 'openai.api_key',
    enabled: true,
  }],
  consents: [{
    name: 'Public research only',
    provider_profile_name: 'Reviewed OpenAI',
    provider_id: 'openai',
    modules: ['chat'],
    purposes: ['genealogy_analysis'],
    data_classes: ['public_genealogy'],
    models: ['fictional-remote-model'],
    max_cost_usd: 0.25,
    retain_payloads: false,
    active: true,
  }],
}

const sessionId = `chat_${'a'.repeat(32)}`
const runId = `run_${'b'.repeat(32)}`

function session(remote = false): ChatSession {
  return {
    schema_version: 1,
    session_id: sessionId,
    provider_profile_name: remote ? 'Reviewed OpenAI' : 'Local Ollama',
    provider_id: remote ? 'openai' : 'ollama',
    model: remote ? 'fictional-remote-model' : 'fictional-local-model',
    purpose: 'genealogy_analysis',
    data_classes: ['public_genealogy'],
    remote,
    consent_name: remote ? 'Public research only' : null,
    message_count: 0,
    transient: true,
    payload_retention: false,
  }
}

function streamRun(id = runId): ChatStreamRun {
  return {
    schema_version: 1,
    session_id: sessionId,
    run_id: id,
    state: 'active',
    latest_sequence: 0,
    terminal: false,
  }
}

function event(
  sequence: number,
  type: ChatEvent['type'],
  payload: Partial<ChatEvent['payload']> = {},
  id = runId,
): ChatEvent {
  return {
    schema_version: 1,
    run_id: id,
    sequence,
    type,
    timestamp: `2026-08-13T12:00:00.${String(sequence).padStart(6, '0')}Z`,
    payload: {
      text: null,
      code: null,
      provider_id: null,
      model: null,
      remote: null,
      message_count: null,
      ...payload,
    },
  }
}

function batch(events: readonly ChatEvent[], id = runId): ChatEventDelivery {
  return {
    schema_version: 1,
    kind: 'batch',
    session_id: sessionId,
    run_id: id,
    from_sequence: events[0]!.sequence,
    through_sequence: events.at(-1)!.sequence,
    encoded_bytes: 256,
    events,
    error: null,
  }
}

function bridgeForChat(configuration: ProviderConfiguration, remote = false) {
  const base = createMockAncestryBridge('success')
  let listener: ((delivery: Readonly<ChatEventDelivery>) => void) | undefined
  const bridge: AncestryBridge = {
    ...base,
    getProviderConfiguration: vi.fn().mockResolvedValue(success(configuration)),
    getChatCapability: vi.fn().mockResolvedValue(success(capability)),
    createChatSession: vi.fn().mockResolvedValue(success(session(remote))),
    closeChatSession: vi.fn().mockResolvedValue(success({
      schema_version: 1 as const,
      session_id: sessionId,
      closed: true as const,
    })),
    startChatStream: vi.fn().mockResolvedValue(success(streamRun())),
    cancelChatStream: vi.fn().mockResolvedValue(success({
      ...streamRun(),
      state: 'cancelling' as const,
    })),
    acknowledgeChatStream: vi.fn(async (request) => success({
      schema_version: 1 as const,
      session_id: request.session_id,
      run_id: request.run_id,
      through_sequence: request.through_sequence,
      acknowledged: true as const,
    })),
    onChatEventBatch: vi.fn((next) => {
      listener = next
      return () => { listener = undefined }
    }),
    copyText: vi.fn().mockResolvedValue(success({ schema_version: 1 as const, copied: true as const })),
    openExternalLink: vi.fn(async (request) => success({
      schema_version: 1 as const,
      destination: request.destination,
      status: 'opened' as const,
    })),
  }
  return {
    bridge,
    deliver: (delivery: Readonly<ChatEventDelivery>) => {
      if (listener === undefined) throw new Error('Chat event listener is not registered')
      listener(delivery)
    },
  }
}

describe('Chat workspace', () => {
  it('submits from the keyboard, streams ordered Markdown, acknowledges it, and copies plain text', async () => {
    const { bridge, deliver } = bridgeForChat(localConfiguration)
    const user = userEvent.setup()
    render(<ChatWorkspace bridge={bridge} />)

    expect(await screen.findByRole('option', { name: 'Local Ollama' })).toBeVisible()
    expect(screen.getByText(/stays on this device/i)).toBeVisible()
    expect(screen.getByText(/payload retention is disabled/i)).toBeVisible()

    const composer = screen.getByRole('textbox', { name: 'Message' })
    await user.type(composer, 'Compare two fictional records.')
    await user.keyboard('{Control>}{Enter}{/Control}')

    await waitFor(() => expect(bridge.createChatSession).toHaveBeenCalledWith({
      schema_version: 1,
      provider_profile_name: 'Local Ollama',
      model: 'fictional-local-model',
      purpose: 'genealogy_analysis',
      data_classes: ['public_genealogy'],
      consent_name: null,
    }))
    expect(bridge.startChatStream).toHaveBeenCalledWith({
      schema_version: 1,
      session_id: sessionId,
      message: 'Compare two fictional records.',
      max_output_tokens: 4_096,
      temperature: 0,
      timeout_seconds: 120,
      max_safe_retries: 1,
    })

    act(() => deliver(batch([
      event(1, 'active', {
        provider_id: 'ollama',
        model: 'fictional-local-model',
        remote: false,
      }),
      event(2, 'first-token', { text: '**Likely** ' }),
      event(3, 'delta', { text: 'a match.' }),
      event(4, 'completed', { message_count: 2 }),
    ])))

    expect(await screen.findByText('Likely')).toBeVisible()
    expect(screen.getByText('a match.')).toBeVisible()
    expect(screen.getByText('Tokens: unavailable · Cost: unavailable')).toBeVisible()
    await waitFor(() => expect(bridge.acknowledgeChatStream).toHaveBeenCalledWith({
      schema_version: 1,
      session_id: sessionId,
      run_id: runId,
      through_sequence: 4,
    }))

    await user.click(screen.getByRole('button', { name: 'Copy response as plain text' }))
    expect(bridge.copyText).toHaveBeenCalledWith({
      schema_version: 1,
      text: '**Likely** a match.',
    })
    expect(await screen.findByRole('status', { name: 'Chat activity' })).toHaveTextContent('Response copied')
  })

  it('shows the remote destination and sends only under the selected compatible consent', async () => {
    const { bridge } = bridgeForChat(remoteConfiguration, true)
    const user = userEvent.setup()
    render(<ChatWorkspace bridge={bridge} />)

    const privacy = await screen.findByRole('note', { name: 'Chat privacy and retention' })
    expect(privacy).toHaveTextContent('leaves this device')
    expect(privacy).toHaveTextContent('OpenAI')
    expect(privacy).toHaveTextContent('https://api.openai.com/v1')
    expect(within(privacy).getByText(/provider retention is not permitted/i)).toBeVisible()
    expect(screen.getByRole('combobox', { name: 'Consent' })).toHaveValue('Public research only')

    await user.type(screen.getByRole('textbox', { name: 'Message' }), 'Summarize public evidence.')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => expect(bridge.createChatSession).toHaveBeenCalledWith(expect.objectContaining({
      provider_profile_name: 'Reviewed OpenAI',
      model: 'fictional-remote-model',
      purpose: 'genealogy_analysis',
      data_classes: ['public_genealogy'],
      consent_name: 'Public research only',
    })))
  })

  it('exposes stop, interruption, and regeneration without hiding provider exit status', async () => {
    const { bridge, deliver } = bridgeForChat(localConfiguration)
    const user = userEvent.setup()
    render(<ChatWorkspace bridge={bridge} />)

    await user.type(await screen.findByRole('textbox', { name: 'Message' }), 'A fictional question.')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await screen.findByRole('button', { name: 'Stop response' })

    await user.click(screen.getByRole('button', { name: 'Stop response' }))
    expect(bridge.cancelChatStream).toHaveBeenCalledWith({
      schema_version: 1,
      session_id: sessionId,
      run_id: runId,
    })

    act(() => deliver(batch([
      event(1, 'active'),
      event(2, 'interrupted', { code: 'USER_CANCELLED' }),
    ])))
    expect(await screen.findByRole('status', { name: 'Chat activity' })).toHaveTextContent('Response interrupted')
    expect(screen.getByText('Code: USER_CANCELLED')).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Regenerate last response' }))
    await waitFor(() => expect(bridge.startChatStream).toHaveBeenCalledTimes(2))
    expect(bridge.startChatStream).toHaveBeenLastCalledWith(expect.objectContaining({
      message: 'A fictional question.',
    }))
  })
})
