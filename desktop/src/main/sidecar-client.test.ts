import { createServer, type ServerResponse } from 'node:http'
import { describe, expect, it, vi } from 'vitest'
import type { JobSnapshot } from '../shared-contract/desktop'
import type { AuthenticatedSidecarSession } from './sidecar-supervisor'
import { SidecarClientError, createSidecarCapabilitiesClient } from './sidecar-client'

const session: Readonly<AuthenticatedSidecarSession> = Object.freeze({
  host: '127.0.0.1', port: 43123, contract: 'ancestryllm.internal-api/1',
  appBuild: '0.5.0-dev', sidecarBuild: '0.5.0-dev', bearerToken: 'private-test-token',
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
