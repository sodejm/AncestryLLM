/** Verifies bounded authenticated sidecar requests, streaming, and error mapping. */
import { createServer, type ServerResponse } from 'node:http'
import { describe, expect, it, vi } from 'vitest'
import type {
  ChatCapability,
  ChatEvent,
  ChatSession,
  ChatStreamRun,
  JobSnapshot,
} from '../shared-contract/desktop'
import type { AuthenticatedSidecarSession } from './sidecar-supervisor'
import {
  SidecarClientError,
  createSidecarCapabilitiesClient,
  requestSidecarRuntimeShutdown,
  type SidecarClientFailure,
} from './sidecar-client'

const session: Readonly<AuthenticatedSidecarSession> = Object.freeze({
  host: '127.0.0.1', port: 43123, contract: 'ancestryllm.internal-api/1',
  appBuild: '0.5.0-dev', sidecarBuild: '0.5.0-dev', bearerToken: 'private-test-token',
})

const chatSessionId = `chat_${'a'.repeat(32)}`
const chatRunId = `run_${'b'.repeat(32)}`

const chatCapability: Readonly<ChatCapability> = Object.freeze({
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

const chatSession: Readonly<ChatSession> = Object.freeze({
  schema_version: 1,
  session_id: chatSessionId,
  provider_profile_name: 'fictional-local',
  provider_id: 'ollama',
  model: 'fictional-model',
  purpose: 'genealogy_analysis',
  data_classes: Object.freeze(['deceased_person'] as const),
  remote: false,
  consent_name: null,
  message_count: 0,
  transient: true,
  payload_retention: false,
})

const chatStreamRun = (overrides: Partial<ChatStreamRun> = {}): Readonly<ChatStreamRun> => Object.freeze({
  schema_version: 1,
  session_id: chatSessionId,
  run_id: chatRunId,
  state: 'active',
  latest_sequence: 1,
  terminal: false,
  ...overrides,
})

const chatEvent = (
  sequence: number,
  type: ChatEvent['type'],
  payload: ChatEvent['payload'],
  overrides: Partial<ChatEvent> = {},
): Readonly<ChatEvent> => Object.freeze({
  schema_version: 1,
  run_id: chatRunId,
  sequence,
  type,
  timestamp: '2026-08-13T12:00:00+00:00',
  payload,
  ...overrides,
})

const emptyChatPayload = Object.freeze({
  text: null,
  code: null,
  provider_id: null,
  model: null,
  remote: null,
  message_count: null,
})

const jobSnapshot = (overrides: Partial<JobSnapshot> = {}): Readonly<JobSnapshot> => Object.freeze({
  schema_version: 1,
  sequence: 1,
  job_id: 'j123456',
  name: 'Export fictional tree',
  state: 'running',
  submitted_at: '2026-08-12T12:00:00+00:00',
  started_at: '2026-08-12T12:00:01+00:00',
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
    operation: 'Writing records',
    timestamp: '2026-08-12T12:00:02+00:00',
    completed: 1,
    total: 4,
  },
  cancellation_requested_at: null,
  cancellation_deferred_by: null,
  ...overrides,
})

describe('main-only sidecar capabilities client', () => {
  it('uses the fixed authenticated startup-diagnostics request and validates its response', async () => {
    const request = vi.fn().mockResolvedValue({ statusCode: 200, contentType: 'application/json', body: JSON.stringify({
      schema_version: 1,
      status: 'ready',
      platform: { operating_system: 'linux', architecture: 'x64' },
      components: [
        { component: 'configuration', status: 'ready', code: 'CONFIGURATION_READY', message: 'Configuration is ready.', remediation: null, restart_required: false, blocks_mutations: false },
        { component: 'sqlcipher', status: 'ready', code: 'SQLCIPHER_READY', message: 'SQLCipher is ready.', remediation: null, restart_required: false, blocks_mutations: false },
        { component: 'keyring', status: 'ready', code: 'KEYRING_READY', message: 'Credential storage is ready.', remediation: null, restart_required: false, blocks_mutations: false },
        { component: 'workspace', status: 'ready', code: 'DATABASE_DIRECTORY_READY', message: 'Workspace is ready.', remediation: null, restart_required: false, blocks_mutations: false },
      ],
    }) })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })

    await expect(client.getStartupDiagnostics()).resolves.toMatchObject({ status: 'ready', schema_version: 1 })
    expect(request).toHaveBeenCalledWith(session, '/api/v1/startup-diagnostics', undefined)
  })

  it('uses the fixed authenticated capabilities request and validates its response', async () => {
    const request = vi.fn().mockResolvedValue({ statusCode: 200, contentType: 'application/json', body: JSON.stringify({
      api: { namespace: '/api/v1', contract: 'ancestryllm.internal-api/1', application_contract: 'ancestryllm.application/0.3' },
      modules: [],
      request_policy: { max_body_bytes: 1_048_576, max_json_depth: 16, max_collection_items: 1_000, max_string_characters: 65_536 },
      pagination: { default_limit: 25, maximum_limit: 100, maximum_cursor_characters: 256 },
    }) })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })
    await expect(client.getCapabilities()).resolves.toMatchObject({ api: { contract: 'ancestryllm.internal-api/1' } })
    expect(request).toHaveBeenCalledWith(session, '/api/v1/capabilities', undefined)
  })

  it('fails closed when no authenticated session is available', async () => {
    const client = createSidecarCapabilitiesClient({ session: () => undefined, request: vi.fn() })
    await expect(client.getCapabilities()).rejects.toEqual(new SidecarClientError('unavailable'))
  })

  it('deletes through the fixed secret route without serializing a request body', async () => {
    const request = vi.fn().mockResolvedValue({
      statusCode: 200,
      contentType: 'application/json',
      body: JSON.stringify({ reference: 'openai.api_key', status: 'missing' }),
    })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })

    await expect(client.deleteSecret({ reference: 'openai.api_key' })).resolves.toEqual({
      reference: 'openai.api_key',
      status: 'missing',
    })
    expect(request).toHaveBeenCalledWith(
      session,
      '/api/v1/secrets/openai.api_key/delete',
      undefined,
      { method: 'POST' },
    )
  })

  it('prepares bounded job shutdown through the fixed authenticated route', async () => {
    const request = vi.fn().mockResolvedValue({
      statusCode: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: 1,
        safe_to_quit: true,
        active_jobs: [],
      }),
    })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })

    await expect(client.prepareJobShutdown('wait')).resolves.toEqual({
      schema_version: 1,
      safe_to_quit: true,
      active_jobs: [],
    })
    expect(request).toHaveBeenCalledWith(
      session,
      '/api/v1/jobs/shutdown',
      undefined,
      {
        method: 'POST',
        body: JSON.stringify({ schema_version: 1, action: 'wait', timeout_seconds: 2 }),
      },
    )
  })

  it('requests runtime shutdown through the fixed authenticated bodyless route', async () => {
    const request = vi.fn().mockResolvedValue({
      statusCode: 204,
      contentType: '',
      body: '',
    })

    await expect(requestSidecarRuntimeShutdown(session, request)).resolves.toBeUndefined()
    expect(request).toHaveBeenCalledWith(
      session,
      '/api/v1/runtime/shutdown',
      undefined,
      { method: 'POST' },
    )
  })

  it('fails closed when runtime shutdown does not return an empty 204 response', async () => {
    const request = vi.fn().mockResolvedValue({
      statusCode: 200,
      contentType: 'application/json',
      body: '{}',
    })

    await expect(requestSidecarRuntimeShutdown(session, request)).rejects.toEqual(
      new SidecarClientError('invalid_response'),
    )
  })

  it('lists, reads, and cancels jobs through fixed validated routes', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({
        statusCode: 200,
        contentType: 'application/json',
        body: JSON.stringify({ schema_version: 1, jobs: [jobSnapshot()] }),
      })
      .mockResolvedValueOnce({
        statusCode: 200,
        contentType: 'application/json',
        body: JSON.stringify(jobSnapshot()),
      })
      .mockResolvedValueOnce({
        statusCode: 200,
        contentType: 'application/json',
        body: JSON.stringify(jobSnapshot({
          sequence: 2,
          state: 'cancelling',
          cancellation_requested_at: '2026-08-12T12:00:03+00:00',
        })),
      })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })

    await expect(client.listJobs()).resolves.toMatchObject({
      schema_version: 1,
      jobs: [{ job_id: 'j123456' }],
    })
    await expect(client.getJob({ schema_version: 1, job_id: 'j123456' })).resolves.toMatchObject({
      sequence: 1,
    })
    await expect(client.cancelJob({ schema_version: 1, job_id: 'j123456' })).resolves.toMatchObject({
      sequence: 2,
      state: 'cancelling',
    })
    expect(request).toHaveBeenNthCalledWith(1, session, '/api/v1/jobs', undefined)
    expect(request).toHaveBeenNthCalledWith(2, session, '/api/v1/jobs/j123456', undefined)
    expect(request).toHaveBeenNthCalledWith(
      3,
      session,
      '/api/v1/jobs/j123456/cancel',
      undefined,
      { method: 'POST' },
    )
  })

  it('preserves the stable startup mutation code for a rejected job cancellation', async () => {
    const request = vi.fn().mockResolvedValue({
      statusCode: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'STARTUP_MUTATION_BLOCKED',
        message: 'Changes are disabled until startup diagnostics pass.',
        remediation: 'Resolve the blocking startup diagnostic and retry.',
      }),
    })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })

    await expect(client.cancelJob({ schema_version: 1, job_id: 'j123456' })).rejects.toEqual(
      new SidecarClientError('startup_mutation_blocked'),
    )
  })

  it('gets, creates, and closes transient chat sessions through fixed authenticated routes', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({
        statusCode: 200,
        contentType: 'application/json',
        body: JSON.stringify(chatCapability),
      })
      .mockResolvedValueOnce({
        statusCode: 200,
        contentType: 'application/json',
        body: JSON.stringify(chatSession),
      })
      .mockResolvedValueOnce({
        statusCode: 204,
        contentType: '',
        body: '',
      })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })
    const create = {
      schema_version: 1 as const,
      provider_profile_name: 'fictional-local',
      model: 'fictional-model',
      purpose: 'genealogy_analysis' as const,
      data_classes: ['deceased_person'] as const,
      consent_name: null,
    }

    await expect(client.getChatCapability()).resolves.toEqual(chatCapability)
    await expect(client.createChatSession(create)).resolves.toEqual(chatSession)
    await expect(client.closeChatSession({
      schema_version: 1,
      session_id: chatSessionId,
    })).resolves.toEqual({
      schema_version: 1,
      session_id: chatSessionId,
      closed: true,
    })
    expect(request).toHaveBeenNthCalledWith(
      1,
      session,
      '/api/v1/chat/capability',
      undefined,
    )
    expect(request).toHaveBeenNthCalledWith(
      2,
      session,
      '/api/v1/chat/sessions',
      undefined,
      { method: 'POST', body: JSON.stringify(create) },
    )
    expect(request).toHaveBeenNthCalledWith(
      3,
      session,
      `/api/v1/chat/sessions/${chatSessionId}`,
      undefined,
      { method: 'DELETE' },
    )
  })

  it.each([
    [422, 'CHAT_SESSION_INVALID', 'chat_session_invalid'],
    [404, 'CHAT_SESSION_NOT_FOUND', 'chat_session_not_found'],
    [409, 'CHAT_SESSION_BUSY', 'chat_session_busy'],
    [429, 'CHAT_SESSION_LIMIT', 'chat_session_limit'],
    [503, 'CHAT_SESSION_SERVICE_UNAVAILABLE', 'chat_session_service_unavailable'],
  ] as const)(
    'preserves stable chat session failures for status %i',
    async (statusCode, code, reason) => {
      const request = vi.fn().mockResolvedValue({
        statusCode,
        contentType: 'application/json',
        body: JSON.stringify({ code, message: 'Rejected for a fixture reason.' }),
      })
      const client = createSidecarCapabilitiesClient({ session: () => session, request })

      await expect(client.createChatSession({
        schema_version: 1,
        provider_profile_name: 'fictional-local',
        model: 'fictional-model',
        purpose: 'genealogy_analysis',
        data_classes: ['deceased_person'],
        consent_name: null,
      })).rejects.toEqual(new SidecarClientError(reason))
    },
  )

  it('starts and cancels chat streams through owner-scoped fixed routes', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({
        statusCode: 200,
        contentType: 'application/json',
        body: JSON.stringify(chatStreamRun()),
      })
      .mockResolvedValueOnce({
        statusCode: 200,
        contentType: 'application/json',
        body: JSON.stringify(chatStreamRun({
          state: 'cancelling',
          latest_sequence: 2,
        })),
      })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })
    const start = {
      schema_version: 1 as const,
      session_id: chatSessionId,
      message: 'Summarize this fictional family.',
      max_output_tokens: 512,
      temperature: 0.2,
      timeout_seconds: 30,
      max_safe_retries: 1,
    }

    await expect(client.startChatStream(start)).resolves.toEqual(chatStreamRun())
    await expect(client.cancelChatStream({
      schema_version: 1,
      session_id: chatSessionId,
      run_id: chatRunId,
    })).resolves.toEqual(chatStreamRun({
      state: 'cancelling',
      latest_sequence: 2,
    }))
    expect(request).toHaveBeenNthCalledWith(
      1,
      session,
      `/api/v1/chat/sessions/${chatSessionId}/streams`,
      undefined,
      { method: 'POST', body: JSON.stringify({
        schema_version: 1,
        message: start.message,
        max_output_tokens: start.max_output_tokens,
        temperature: start.temperature,
        timeout_seconds: start.timeout_seconds,
        max_safe_retries: start.max_safe_retries,
      }) },
    )
    expect(request).toHaveBeenNthCalledWith(
      2,
      session,
      `/api/v1/chat/sessions/${chatSessionId}/streams/${chatRunId}/cancel`,
      undefined,
      { method: 'POST' },
    )
  })

  it('authenticates, replays, and flow-controls owner-scoped chat events', async () => {
    const requests: Array<Readonly<{
      url: string | undefined
      authorization: string | undefined
      cursor: string | undefined
      apiVersion: string | undefined
      appBuild: string | undefined
    }>> = []
    const server = createServer((request, response) => {
      requests.push({
        url: request.url,
        authorization: request.headers.authorization,
        cursor: request.headers['last-event-id'] as string | undefined,
        apiVersion: request.headers['x-ancestry-api-version'] as string | undefined,
        appBuild: request.headers['x-ancestry-app-build'] as string | undefined,
      })
      response.writeHead(200, {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-store',
      })
      response.write(': keep-alive\n\n')
      response.write(`id: 2\nevent: first-token\ndata: ${JSON.stringify(chatEvent(
        2,
        'first-token',
        { ...emptyChatPayload, text: 'Fictional' },
      ))}\n\n`)
      response.write(`id: 3\nevent: delta\ndata: ${JSON.stringify(chatEvent(
        3,
        'delta',
        { ...emptyChatPayload, text: ' ' },
      ))}\n\n`)
      response.end(`id: 4\nevent: completed\ndata: ${JSON.stringify(chatEvent(
        4,
        'completed',
        { ...emptyChatPayload, message_count: 2 },
      ))}\n\n`)
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const client = createSidecarCapabilitiesClient({
      session: () => ({ ...session, port: address.port }),
    })
    const received: number[] = []
    const pausedAt: number[] = []
    try {
      await client.streamChatEvents({
        schema_version: 1,
        session_id: chatSessionId,
        run_id: chatRunId,
        after: 1,
      }, (event, flow) => {
        received.push(event.sequence)
        if (event.sequence === 2) {
          flow.pause()
          pausedAt.push(event.sequence)
          flow.resume()
        }
      })
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }

    expect(received).toEqual([2, 3, 4])
    expect(pausedAt).toEqual([2])
    expect(requests).toEqual([{
      url: `/api/v1/chat/sessions/${chatSessionId}/streams/${chatRunId}/events`,
      authorization: 'Bearer private-test-token',
      cursor: '1',
      apiVersion: 'ancestryllm.internal-api/1',
      appBuild: '0.5.0-dev',
    }])
  })

  it.each([
    {
      name: 'wrong MIME type',
      status: 200,
      headers: { 'content-type': 'application/json' },
      body: '{}',
      reason: 'chat_event_stream_failed',
    },
    {
      name: 'redirect response',
      status: 302,
      headers: { 'content-type': 'text/plain', location: 'https://example.invalid/stream' },
      body: '',
      reason: 'request_failed',
    },
    {
      name: 'expired replay cursor',
      status: 410,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code: 'CHAT_STREAM_REPLAY_EXPIRED' }),
      reason: 'chat_stream_replay_expired',
    },
  ])('fails closed on a $name', async ({ status, headers, body, reason }) => {
    let requestCount = 0
    const server = createServer((_request, response) => {
      requestCount += 1
      response.writeHead(status, headers)
      response.end(body)
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const client = createSidecarCapabilitiesClient({
      session: () => ({ ...session, port: address.port }),
    })
    try {
      await expect(client.streamChatEvents({
        schema_version: 1,
        session_id: chatSessionId,
        run_id: chatRunId,
        after: 0,
      }, vi.fn())).rejects.toEqual(new SidecarClientError(reason as SidecarClientFailure))
      expect(requestCount).toBe(1)
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })

  it.each([
    {
      name: 'wrong run owner',
      event: chatEvent(2, 'first-token', { ...emptyChatPayload, text: 'x' }, {
        run_id: `run_${'c'.repeat(32)}`,
      }),
      after: 1,
    },
    {
      name: 'nonmonotonic sequence',
      event: chatEvent(3, 'first-token', { ...emptyChatPayload, text: 'x' }),
      after: 1,
    },
    {
      name: 'premature EOF',
      event: chatEvent(2, 'first-token', { ...emptyChatPayload, text: 'x' }),
      after: 1,
    },
  ])('rejects a $name in the chat event stream', async ({ name, event, after }) => {
    const server = createServer((_request, response) => {
      response.writeHead(200, { 'content-type': 'text/event-stream' })
      response.end(`id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`)
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const client = createSidecarCapabilitiesClient({
      session: () => ({ ...session, port: address.port }),
    })
    try {
      await expect(client.streamChatEvents({
        schema_version: 1,
        session_id: chatSessionId,
        run_id: chatRunId,
        after,
      }, vi.fn())).rejects.toEqual(new SidecarClientError(
        name === 'premature EOF'
          ? 'chat_event_stream_interrupted'
          : 'chat_event_stream_failed',
      ))
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })

  it('rejects an invalid chat replay cursor before opening a connection', async () => {
    const client = createSidecarCapabilitiesClient({ session: () => session })

    await expect(client.streamChatEvents({
      schema_version: 1,
      session_id: chatSessionId,
      run_id: chatRunId,
      after: -1,
    }, vi.fn())).rejects.toEqual(new SidecarClientError('chat_stream_cursor_invalid'))
  })

  it('authenticates and validates sequenced job events while ignoring heartbeats', async () => {
    const requests: Array<Readonly<{
      url: string | undefined
      authorization: string | undefined
      cursor: string | undefined
    }>> = []
    const server = createServer((request, response) => {
      requests.push({
        url: request.url,
        authorization: request.headers.authorization,
        cursor: request.headers['last-event-id'] as string | undefined,
      })
      response.writeHead(200, {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-store',
      })
      response.write(': keep-alive\n\n')
      response.write(`id: 2\nevent: progress\ndata: ${JSON.stringify({
        schema_version: 1,
        sequence: 2,
        kind: 'progress',
        created_at: '2026-08-12T12:00:03+00:00',
        snapshot: jobSnapshot({ sequence: 2 }),
      })}\n\n`)
      response.end(`id: 3\nevent: terminal\ndata: ${JSON.stringify({
        schema_version: 1,
        sequence: 3,
        kind: 'terminal',
        created_at: '2026-08-12T12:00:04+00:00',
        snapshot: jobSnapshot({
          sequence: 3,
          state: 'completed',
          finished_at: '2026-08-12T12:00:04+00:00',
          outcome_summary: 'Export completed.',
        }),
      })}\n\n`)
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const client = createSidecarCapabilitiesClient({
      session: () => ({ ...session, port: address.port }),
    })
    const received: number[] = []
    try {
      await client.streamJobEvents({
        schema_version: 1,
        subscription_id: `sub_${'a'.repeat(32)}`,
        job_id: 'j123456',
        after: 1,
      }, (event) => received.push(event.sequence))
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }

    expect(received).toEqual([2, 3])
    expect(requests).toEqual([{
      url: '/api/v1/jobs/j123456/events',
      authorization: 'Bearer private-test-token',
      cursor: '1',
    }])
  })

  it('does not rearm the inactivity deadline after a terminal listener aborts', async () => {
    const controller = new AbortController()
    let timerCallsAtAbort = 0
    const timeoutSpy = vi.spyOn(globalThis, 'setTimeout')
    const server = createServer((_request, response) => {
      response.writeHead(200, { 'content-type': 'text/event-stream' })
      response.write(`id: 2\nevent: terminal\ndata: ${JSON.stringify({
        schema_version: 1,
        sequence: 2,
        kind: 'terminal',
        created_at: '2026-08-12T12:00:04+00:00',
        snapshot: jobSnapshot({
          sequence: 2,
          state: 'completed',
          finished_at: '2026-08-12T12:00:04+00:00',
          outcome_summary: 'Export completed.',
        }),
      })}\n\n`)
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const client = createSidecarCapabilitiesClient({
      session: () => ({ ...session, port: address.port }),
    })
    try {
      await expect(client.streamJobEvents({
        schema_version: 1,
        subscription_id: `sub_${'a'.repeat(32)}`,
        job_id: 'j123456',
        after: 1,
      }, () => {
        timerCallsAtAbort = timeoutSpy.mock.calls.length
        controller.abort()
      }, controller.signal)).rejects.toEqual(new SidecarClientError('cancelled'))

      expect(timerCallsAtAbort).toBeGreaterThan(0)
      expect(timeoutSpy).toHaveBeenCalledTimes(timerCallsAtAbort)
    } finally {
      timeoutSpy.mockRestore()
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })

  it('fails closed when the bounded event replay window has expired', async () => {
    const server = createServer((_request, response) => {
      response.writeHead(200, { 'content-type': 'text/event-stream' })
      response.end(`event: resync-required\ndata: ${JSON.stringify({
        schema_version: 1,
        code: 'JOB_EVENT_REPLAY_EXPIRED',
        message: 'The bounded job-event replay window is no longer available.',
        remediation: 'Fetch the current job snapshot, then reconnect from its sequence.',
      })}\n\n`)
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const client = createSidecarCapabilitiesClient({
      session: () => ({ ...session, port: address.port }),
    })
    try {
      await expect(client.streamJobEvents({
        schema_version: 1,
        subscription_id: `sub_${'a'.repeat(32)}`,
        job_id: 'j123456',
        after: 0,
      }, vi.fn())).rejects.toEqual(new SidecarClientError('job_event_replay_expired'))
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })

  it('destroys a live job-event stream when its bridge subscription is cancelled', async () => {
    let acceptRequest!: () => void
    const accepted = new Promise<void>((resolve) => { acceptRequest = resolve })
    const server = createServer((_request, response) => {
      response.writeHead(200, { 'content-type': 'text/event-stream' })
      response.write(': keep-alive\n\n')
      acceptRequest()
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const controller = new AbortController()
    const client = createSidecarCapabilitiesClient({
      session: () => ({ ...session, port: address.port }),
    })
    const stream = client.streamJobEvents({
      schema_version: 1,
      subscription_id: `sub_${'a'.repeat(32)}`,
      job_id: 'j123456',
      after: 0,
    }, vi.fn(), controller.signal)
    await accepted
    controller.abort()
    try {
      await expect(stream).rejects.toEqual(new SidecarClientError('cancelled'))
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })

  it('retains and refreshes a bounded inactivity deadline after SSE headers arrive', async () => {
    let streamResponse: ServerResponse | undefined
    let acceptRequest!: () => void
    const accepted = new Promise<void>((resolve) => { acceptRequest = resolve })
    const server = createServer((_request, response) => {
      streamResponse = response
      response.writeHead(200, { 'content-type': 'text/event-stream' })
      response.write(': keep-alive\n\n')
      acceptRequest()
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const client = createSidecarCapabilitiesClient({
      session: () => ({ ...session, port: address.port }),
      jobEventInactivityTimeoutMs: 500,
    })
    let failure: unknown
    const stream = client.streamJobEvents({
      schema_version: 1,
      subscription_id: `sub_${'a'.repeat(32)}`,
      job_id: 'j123456',
      after: 0,
    }, vi.fn()).catch((error: unknown) => { failure = error })
    try {
      await accepted
      await new Promise<void>((resolve) => setImmediate(resolve))
      await new Promise<void>((resolve) => setTimeout(resolve, 100))
      expect(failure).toBeUndefined()

      streamResponse?.write(': keep-alive\n\n')
      await new Promise<void>((resolve) => setImmediate(resolve))
      await new Promise<void>((resolve) => setTimeout(resolve, 100))
      expect(failure).toBeUndefined()

      await stream
      expect(failure).toEqual(new SidecarClientError('job_event_stream_failed'))
    } finally {
      streamResponse?.destroy()
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })

  it('fails closed on ambiguous or unsafe job shutdown assessments', async () => {
    const bodies: unknown[] = [
      { schema_version: 1, safe_to_quit: false, active_jobs: [] },
      { schema_version: 1, safe_to_quit: true, active_jobs: [{ job_id: 'j000001' }] },
      { schema_version: 2, safe_to_quit: true, active_jobs: [] },
      { schema_version: 1, safe_to_quit: true, active_jobs: [], ignored: true },
      { schema_version: 1, safe_to_quit: true },
      null,
    ]
    for (const body of bodies) {
      const request = vi.fn().mockResolvedValue({
        statusCode: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
      const client = createSidecarCapabilitiesClient({ session: () => session, request })

      await expect(client.prepareJobShutdown('cancel')).rejects.toEqual(
        new SidecarClientError('invalid_response'),
      )
    }
  })

  it('fails closed when bounded job shutdown is not accepted by the sidecar', async () => {
    const request = vi.fn().mockResolvedValue({
      statusCode: 503,
      contentType: 'application/json',
      body: JSON.stringify({ code: 'JOB_SHUTDOWN_UNSAFE' }),
    })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })

    await expect(client.prepareJobShutdown('cancel')).rejects.toEqual(
      new SidecarClientError('request_failed'),
    )
  })

  it('rejects malformed, oversized, and unsuccessful responses', async () => {
    for (const response of [
      { statusCode: 200, contentType: 'application/json', body: '{' },
      { statusCode: 200, contentType: 'application/json', body: 'x'.repeat(1_048_577) },
      { statusCode: 401, contentType: 'application/json', body: '{}' },
    ]) {
      const client = createSidecarCapabilitiesClient({ session: () => session, request: vi.fn().mockResolvedValue(response) })
      await expect(client.getCapabilities()).rejects.toBeInstanceOf(SidecarClientError)
    }
  })

  it('enforces a wall-clock deadline even while a response trickles data', async () => {
    const server = createServer((_request, response) => {
      response.writeHead(200, { 'content-type': 'application/json' })
      response.write('{')
      const trickle = setInterval(() => response.write(' '), 100)
      const finish = setTimeout(() => response.end('}'), 3_500)
      response.on('close', () => {
        clearInterval(trickle)
        clearTimeout(finish)
      })
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const client = createSidecarCapabilitiesClient({ session: () => ({ ...session, port: address.port }) })
    const startedAt = Date.now()
    try {
      await expect(client.getCapabilities()).rejects.toEqual(new SidecarClientError('request_failed'))
      expect(Date.now() - startedAt).toBeLessThan(3_400)
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  }, 5_000)

  it('destroys an in-flight fixed-route request when its bridge operation is cancelled', async () => {
    let acceptRequest!: () => void
    const accepted = new Promise<void>((resolve) => { acceptRequest = resolve })
    const server = createServer(() => { acceptRequest() })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Expected an IP test listener')
    const controller = new AbortController()
    const client = createSidecarCapabilitiesClient({
      session: () => ({ ...session, port: address.port }),
    })
    const request = client.getCapabilities(controller.signal)
    await accepted
    controller.abort()
    try {
      await expect(request).rejects.toEqual(new SidecarClientError('cancelled'))
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })
})
