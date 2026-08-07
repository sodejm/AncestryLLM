/** Tests the main-only authenticated capabilities client for bounded requests, validation, and timeouts. */
import { createServer } from 'node:http'
import { describe, expect, it, vi } from 'vitest'
import type { AuthenticatedSidecarSession } from './sidecar-supervisor'
import { SidecarClientError, createSidecarCapabilitiesClient } from './sidecar-client'

const session: Readonly<AuthenticatedSidecarSession> = Object.freeze({
  host: '127.0.0.1', port: 43123, contract: 'ancestryllm.internal-api/1',
  appBuild: '0.5.0-dev', sidecarBuild: '0.5.0-dev', bearerToken: 'private-test-token',
})

describe('main-only sidecar capabilities client', () => {
  it('uses the fixed authenticated capabilities request and validates its response', async () => {
    const request = vi.fn().mockResolvedValue({ statusCode: 200, contentType: 'application/json', body: JSON.stringify({
      api: { namespace: '/api/v1', contract: 'ancestryllm.internal-api/1', application_contract: 'ancestryllm.application/0.3' },
      modules: [],
      request_policy: { max_body_bytes: 1_048_576, max_json_depth: 16, max_collection_items: 1_000, max_string_characters: 65_536 },
      pagination: { default_limit: 25, maximum_limit: 100, maximum_cursor_characters: 256 },
    }) })
    const client = createSidecarCapabilitiesClient({ session: () => session, request })
    await expect(client.getCapabilities()).resolves.toMatchObject({ api: { contract: 'ancestryllm.internal-api/1' } })
    expect(request).toHaveBeenCalledWith(session, '/api/v1/capabilities')
  })

  it('fails closed when no authenticated session is available', async () => {
    const client = createSidecarCapabilitiesClient({ session: () => undefined, request: vi.fn() })
    await expect(client.getCapabilities()).rejects.toEqual(new SidecarClientError('unavailable'))
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
})
