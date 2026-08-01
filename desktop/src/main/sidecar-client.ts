import { request as httpRequest } from 'node:http'
import { DESKTOP_PROTOCOL_VERSION, type CapabilityManifest } from '../shared-contract/desktop'
import { parseCapabilitiesResult } from '../shared-contract/runtime'
import type { AuthenticatedSidecarSession } from './sidecar-supervisor'

const CAPABILITIES_PATH = '/api/v1/capabilities'
const MAX_RESPONSE_BYTES = 1_048_576
const REQUEST_TIMEOUT_MS = 3_000

export type SidecarClientFailure = 'unavailable' | 'request_failed' | 'invalid_response'

export class SidecarClientError extends Error {
  constructor(readonly reason: SidecarClientFailure) {
    super(reason)
    this.name = 'SidecarClientError'
  }
}

export interface SidecarHttpResponse {
  statusCode: number
  contentType: string
  body: string
}

export type SidecarRequest = (
  session: Readonly<AuthenticatedSidecarSession>,
  path: typeof CAPABILITIES_PATH,
) => Promise<SidecarHttpResponse>

function requestFixedRoute(
  sidecar: Readonly<AuthenticatedSidecarSession>,
  path: typeof CAPABILITIES_PATH,
): Promise<SidecarHttpResponse> {
  return new Promise((resolve, reject) => {
    const request = httpRequest({
      hostname: sidecar.host,
      port: sidecar.port,
      path,
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${sidecar.bearerToken}`,
        Connection: 'close',
        Host: `${sidecar.host}:${sidecar.port}`,
        'X-Ancestry-API-Version': sidecar.contract,
        'X-Ancestry-App-Build': sidecar.appBuild,
      },
      timeout: REQUEST_TIMEOUT_MS,
    }, (response) => {
      const chunks: Buffer[] = []
      let bytes = 0
      response.on('error', reject)
      response.on('data', (chunk: Buffer | string) => {
        const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
        bytes += buffer.length
        if (bytes > MAX_RESPONSE_BYTES) {
          response.destroy(new SidecarClientError('invalid_response'))
          return
        }
        chunks.push(buffer)
      })
      response.on('end', () => {
        resolve({
          statusCode: response.statusCode ?? 0,
          contentType: Array.isArray(response.headers['content-type'])
            ? (response.headers['content-type'][0] ?? '')
            : (response.headers['content-type'] ?? ''),
          body: Buffer.concat(chunks).toString('utf8'),
        })
      })
    })
    request.on('timeout', () => request.destroy(new SidecarClientError('request_failed')))
    request.on('error', reject)
    request.end()
  })
}

export function createSidecarCapabilitiesClient(dependencies: Readonly<{
  session(): Readonly<AuthenticatedSidecarSession> | undefined
  request?: SidecarRequest
}>): Readonly<{ getCapabilities(): Promise<CapabilityManifest> }> {
  const request = dependencies.request ?? requestFixedRoute
  return Object.freeze({
    async getCapabilities() {
      const session = dependencies.session()
      if (!session) throw new SidecarClientError('unavailable')
      let response: SidecarHttpResponse
      try {
        response = await request(session, CAPABILITIES_PATH)
      } catch (error) {
        if (error instanceof SidecarClientError) throw error
        throw new SidecarClientError('request_failed')
      }
      if (response.statusCode !== 200 || !/^application\/json(?:\s*;|$)/i.test(response.contentType)
        || Buffer.byteLength(response.body, 'utf8') > MAX_RESPONSE_BYTES) {
        throw new SidecarClientError('invalid_response')
      }
      try {
        const result = parseCapabilitiesResult({
          ok: true,
          protocolVersion: DESKTOP_PROTOCOL_VERSION,
          data: JSON.parse(response.body) as unknown,
        })
        if (!result.ok) throw new SidecarClientError('invalid_response')
        return result.data
      } catch {
        throw new SidecarClientError('invalid_response')
      }
    },
  })
}
