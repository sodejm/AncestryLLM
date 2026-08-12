import { request as httpRequest, type IncomingMessage } from 'node:http'
import {
  DESKTOP_PROTOCOL_VERSION,
  type ApplicationSettings,
  type ApplicationSettingsPatch,
  type CapabilityManifest,
  type SecretReference,
  type SecretReferenceRequest,
  type SecretSetRequest,
  type SecretStatus,
  type StartupDiagnosticReport,
} from '../shared-contract/desktop'
import {
  parseCapabilitiesResult,
  parseSecretStatusResult,
  parseSettingsResult,
  parseStartupDiagnosticReport,
} from '../shared-contract/runtime'
import type { AuthenticatedSidecarSession } from './sidecar-supervisor'

const CAPABILITIES_PATH = '/api/v1/capabilities' as const
const STARTUP_DIAGNOSTICS_PATH = '/api/v1/startup-diagnostics' as const
const SETTINGS_PATH = '/api/v1/settings' as const
const MAX_RESPONSE_BYTES = 1_048_576
const MAX_REQUEST_BYTES = 65_600
const REQUEST_TIMEOUT_MS = 3_000

type SecretOperation = 'status' | 'set' | 'delete'
type SidecarPath =
  | typeof CAPABILITIES_PATH
  | typeof STARTUP_DIAGNOSTICS_PATH
  | typeof SETTINGS_PATH
  | `/api/v1/secrets/${SecretReference}/${SecretOperation}`

export type SidecarClientFailure =
  | 'unavailable'
  | 'request_failed'
  | 'invalid_response'
  | 'cancelled'
  | 'settings_conflict'
  | 'settings_invalid'
  | 'secret_store_unavailable'
  | 'secret_environment_managed'
  | 'secret_invalid'

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

export interface SidecarRequestOptions {
  method: 'PATCH' | 'POST'
  body?: string
}

export type SidecarRequest = (
  session: Readonly<AuthenticatedSidecarSession>,
  path: SidecarPath,
  signal?: AbortSignal,
  options?: Readonly<SidecarRequestOptions>,
) => Promise<SidecarHttpResponse>

export interface SidecarClient {
  getStartupDiagnostics(signal?: AbortSignal): Promise<Readonly<StartupDiagnosticReport>>
  getCapabilities(signal?: AbortSignal): Promise<CapabilityManifest>
  getSettings(signal?: AbortSignal): Promise<ApplicationSettings>
  updateSettings(update: ApplicationSettingsPatch, signal?: AbortSignal): Promise<ApplicationSettings>
  getSecretStatus(request: SecretReferenceRequest, signal?: AbortSignal): Promise<SecretStatus>
  setSecret(request: SecretSetRequest, signal?: AbortSignal): Promise<SecretStatus>
  deleteSecret(request: SecretReferenceRequest, signal?: AbortSignal): Promise<SecretStatus>
}

function requestFixedRoute(
  sidecar: Readonly<AuthenticatedSidecarSession>,
  path: SidecarPath,
  signal?: AbortSignal,
  options?: Readonly<SidecarRequestOptions>,
): Promise<SidecarHttpResponse> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new SidecarClientError('cancelled'))
      return
    }
    const body = options?.body
    const bodyBytes = body === undefined ? 0 : Buffer.byteLength(body, 'utf8')
    if (bodyBytes > MAX_REQUEST_BYTES) {
      reject(new SidecarClientError('request_failed'))
      return
    }
    let responseStream: IncomingMessage | undefined
    let settled = false
    const abort = () => {
      const error = new SidecarClientError('cancelled')
      responseStream?.destroy(error)
      request.destroy(error)
      rejectOnce(error)
    }
    const finish = <T>(callback: (value: T) => void, value: T) => {
      if (settled) return
      settled = true
      clearTimeout(deadline)
      signal?.removeEventListener('abort', abort)
      callback(value)
    }
    const resolveOnce = (value: SidecarHttpResponse) => finish(resolve, value)
    const rejectOnce = (error: unknown) => finish(reject, error)
    const headers: Record<string, string | number> = {
      Accept: 'application/json',
      Authorization: `Bearer ${sidecar.bearerToken}`,
      Connection: 'close',
      Host: `${sidecar.host}:${sidecar.port}`,
      'X-Ancestry-API-Version': sidecar.contract,
      'X-Ancestry-App-Build': sidecar.appBuild,
    }
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json'
      headers['Content-Length'] = bodyBytes
    }
    const request = httpRequest({
      hostname: sidecar.host,
      port: sidecar.port,
      path,
      method: options?.method ?? 'GET',
      headers,
      timeout: REQUEST_TIMEOUT_MS,
    }, (response) => {
      responseStream = response
      const chunks: Buffer[] = []
      let bytes = 0
      response.on('error', rejectOnce)
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
        resolveOnce({
          statusCode: response.statusCode ?? 0,
          contentType: Array.isArray(response.headers['content-type'])
            ? (response.headers['content-type'][0] ?? '')
            : (response.headers['content-type'] ?? ''),
          body: Buffer.concat(chunks).toString('utf8'),
        })
      })
    })
    request.on('timeout', () => request.destroy(new SidecarClientError('request_failed')))
    request.on('error', rejectOnce)
    const deadline = setTimeout(() => {
      const error = new SidecarClientError('request_failed')
      responseStream?.destroy(error)
      request.destroy(error)
      rejectOnce(error)
    }, REQUEST_TIMEOUT_MS)
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) {
      abort()
      return
    }
    if (body !== undefined) request.write(body)
    request.end()
  })
}

function secretPath(reference: SecretReference, operation: SecretOperation): SidecarPath {
  return `/api/v1/secrets/${reference}/${operation}`
}

function validJsonResponse(response: Readonly<SidecarHttpResponse>): boolean {
  return /^application\/json(?:\s*;|$)/i.test(response.contentType)
    && Buffer.byteLength(response.body, 'utf8') <= MAX_RESPONSE_BYTES
}

function parseJson<T>(
  response: Readonly<SidecarHttpResponse>,
  parse: (value: unknown) => { ok: boolean; data?: Readonly<T> },
): Readonly<T> {
  if (!validJsonResponse(response)) throw new SidecarClientError('invalid_response')
  try {
    const result = parse({
      ok: true,
      protocolVersion: DESKTOP_PROTOCOL_VERSION,
      data: JSON.parse(response.body) as unknown,
    })
    if (!result.ok || result.data === undefined) throw new SidecarClientError('invalid_response')
    return result.data
  } catch {
    throw new SidecarClientError('invalid_response')
  }
}

function settingsFailure(statusCode: number): SidecarClientError {
  if (statusCode === 409) return new SidecarClientError('settings_conflict')
  if (statusCode === 400 || statusCode === 422) return new SidecarClientError('settings_invalid')
  return new SidecarClientError('request_failed')
}

function secretFailure(statusCode: number): SidecarClientError {
  if (statusCode === 409) return new SidecarClientError('secret_environment_managed')
  if (statusCode === 400 || statusCode === 422) return new SidecarClientError('secret_invalid')
  if (statusCode === 503) return new SidecarClientError('secret_store_unavailable')
  return new SidecarClientError('request_failed')
}

export function createSidecarClient(dependencies: Readonly<{
  session(): Readonly<AuthenticatedSidecarSession> | undefined
  request?: SidecarRequest
}>): Readonly<SidecarClient> {
  const request = dependencies.request ?? requestFixedRoute
  const perform = async (
    path: SidecarPath,
    signal?: AbortSignal,
    options?: Readonly<SidecarRequestOptions>,
  ): Promise<SidecarHttpResponse> => {
    if (signal?.aborted) throw new SidecarClientError('cancelled')
    const session = dependencies.session()
    if (!session) throw new SidecarClientError('unavailable')
    try {
      const response = options === undefined
        ? await request(session, path, signal)
        : await request(session, path, signal, options)
      if (signal?.aborted) throw new SidecarClientError('cancelled')
      return response
    } catch (error) {
      if (signal?.aborted) throw new SidecarClientError('cancelled')
      if (error instanceof SidecarClientError) throw error
      throw new SidecarClientError('request_failed')
    }
  }

  return Object.freeze({
    async getStartupDiagnostics(signal?: AbortSignal) {
      const response = await perform(STARTUP_DIAGNOSTICS_PATH, signal)
      if (response.statusCode !== 200) throw new SidecarClientError('invalid_response')
      if (!validJsonResponse(response)) throw new SidecarClientError('invalid_response')
      try {
        return parseStartupDiagnosticReport(JSON.parse(response.body) as unknown)
      } catch {
        throw new SidecarClientError('invalid_response')
      }
    },
    async getCapabilities(signal?: AbortSignal) {
      const response = await perform(CAPABILITIES_PATH, signal)
      if (response.statusCode !== 200) throw new SidecarClientError('invalid_response')
      return parseJson(response, parseCapabilitiesResult)
    },
    async getSettings(signal?: AbortSignal) {
      const response = await perform(SETTINGS_PATH, signal)
      if (response.statusCode !== 200) throw settingsFailure(response.statusCode)
      return parseJson(response, parseSettingsResult)
    },
    async updateSettings(update: ApplicationSettingsPatch, signal?: AbortSignal) {
      const response = await perform(SETTINGS_PATH, signal, {
        method: 'PATCH',
        body: JSON.stringify(update),
      })
      if (response.statusCode !== 200) throw settingsFailure(response.statusCode)
      return parseJson(response, parseSettingsResult)
    },
    async getSecretStatus(secret: SecretReferenceRequest, signal?: AbortSignal) {
      const response = await perform(secretPath(secret.reference, 'status'), signal)
      if (response.statusCode !== 200) throw secretFailure(response.statusCode)
      return parseJson(response, parseSecretStatusResult)
    },
    async setSecret(secret: SecretSetRequest, signal?: AbortSignal) {
      const response = await perform(secretPath(secret.reference, 'set'), signal, {
        method: 'POST',
        body: JSON.stringify({ value: secret.value }),
      })
      if (response.statusCode !== 200) throw secretFailure(response.statusCode)
      return parseJson(response, parseSecretStatusResult)
    },
    async deleteSecret(secret: SecretReferenceRequest, signal?: AbortSignal) {
      const response = await perform(secretPath(secret.reference, 'delete'), signal, {
        method: 'POST',
      })
      if (response.statusCode !== 200) throw secretFailure(response.statusCode)
      return parseJson(response, parseSecretStatusResult)
    },
  })
}

export function createSidecarCapabilitiesClient(dependencies: Readonly<{
  session(): Readonly<AuthenticatedSidecarSession> | undefined
  request?: SidecarRequest
}>): Readonly<SidecarClient> {
  return createSidecarClient(dependencies)
}
