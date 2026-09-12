/** Checks the authenticated, fixed-route GEDCOM client without exposing paths. */
import { describe, expect, it, vi } from 'vitest'
import { createGedcomIntakeClient } from './sidecar-client'

const session = { host: '127.0.0.1' as const, port: 43123,
  contract: 'ancestryllm.internal-api/1' as const, appBuild: '0.5.0-dev',
  sidecarBuild: '0.5.0-dev', bearerToken: 'private-test-token' }
const page = { schema_version: 1, candidates: [], total_count: 0, next_cursor: null }

describe('native GEDCOM HTTP client', () => {
  it('sends bounded queries only to the fixed authenticated route', async () => {
    const request = vi.fn().mockResolvedValue({ statusCode: 200,
      contentType: 'application/json', body: JSON.stringify(page) })
    const client = createGedcomIntakeClient({ session: () => session, request })
    await expect(client.roots({ schema_version: 1, job_id: 'j000001',
      query: 'Example', limit: 25, cursor: null })).resolves.toEqual(page)
    expect(request).toHaveBeenCalledWith(session, '/api/v1/gedcom/intake/j000001/roots',
      undefined, { method: 'POST', body: JSON.stringify({ query: 'Example', limit: 25, cursor: null }) })
    await expect(client.result('../private')).rejects.toThrow()
    await expect(client.roots({ schema_version: 1, job_id: 'j000001',
      query: 'x'.repeat(129), limit: 25, cursor: null })).rejects.toThrow()
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('rejects unexpected response fields and fails closed without a session', async () => {
    const request = vi.fn().mockResolvedValue({ statusCode: 200,
      contentType: 'application/json', body: JSON.stringify({ schema_version: 1, path: '/private/tree.ged' }) })
    const client = createGedcomIntakeClient({ session: () => session, request })
    await expect(client.discard('j000001')).rejects.toMatchObject({ reason: 'invalid_response' })
    expect(request.mock.calls[0]?.slice(1)).toEqual([
      '/api/v1/gedcom/intake/j000001/discard', undefined, { method: 'POST' },
    ])
    await expect(createGedcomIntakeClient({ session: () => undefined, request })
      .result('j000001')).rejects.toMatchObject({ reason: 'unavailable' })
  })

  it.each([
    'GEDCOM_INTAKE_INVALID',
    'GEDCOM_INTAKE_CAPACITY',
    'GEDCOM_JOB_RESULT_UNAVAILABLE',
    'GEDCOM_ROOT_CURSOR_INVALID',
  ] as const)('preserves the documented GEDCOM error code %s', async (code) => {
    const request = vi.fn().mockResolvedValue({
      statusCode: 409,
      contentType: 'application/json',
      body: JSON.stringify({ code, message: 'bounded', remediation: 'retry', details: [] }),
    })
    const client = createGedcomIntakeClient({ session: () => session, request })
    await expect(client.result('j000001')).rejects.toMatchObject({ reason: code })
  })

  it('maps undocumented or malformed GEDCOM errors to the generic failure', async () => {
    const request = vi.fn().mockResolvedValue({
      statusCode: 503,
      contentType: 'application/json',
      body: JSON.stringify({ code: 'INTERNAL_HOST_PATH', message: '/private/tree.ged' }),
    })
    const client = createGedcomIntakeClient({ session: () => session, request })
    await expect(client.result('j000001')).rejects.toMatchObject({ reason: 'request_failed' })
  })
})
