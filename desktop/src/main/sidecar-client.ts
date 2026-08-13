import { request as httpRequest, type IncomingMessage } from 'node:http'
import { StringDecoder } from 'node:string_decoder'
import {
  DESKTOP_PROTOCOL_VERSION,
  type ApplicationSettings,
  type ApplicationSettingsPatch,
  type CapabilityManifest,
  type ConsentCreateRequest,
  type ConsentPreview,
  type ConsentPreviewRequest,
  type ConsentRevokeRequest,
  type JobEvent,
  type JobEventSubscriptionRequest,
  type JobList,
  type JobRequest,
  type JobSnapshot,
  type ProviderConfiguration,
  type ProviderEndpointValidation,
  type ProviderEndpointValidationRequest,
  type ProviderProfileCreateRequest,
  type SecretReference,
  type SecretReferenceRequest,
  type SecretSetRequest,
  type SecretStatus,
  type StartupDiagnosticReport,
} from '../shared-contract/desktop'
import {
  parseCapabilitiesResult,
  parseConsentPreviewResult,
  parseJobEventResult,
  parseJobListResult,
  parseJobSnapshotResult,
  parseProviderConfigurationResult,
  parseProviderEndpointValidationResult,
  parseSecretStatusResult,
  parseSettingsResult,
  parseStartupDiagnosticReport,
} from '../shared-contract/runtime'
import type { AuthenticatedSidecarSession } from './sidecar-supervisor'

const CAPABILITIES_PATH = '/api/v1/capabilities' as const
const STARTUP_DIAGNOSTICS_PATH = '/api/v1/startup-diagnostics' as const
const SETTINGS_PATH = '/api/v1/settings' as const
const PROVIDER_CONFIGURATION_PATH = '/api/v1/provider-configuration' as const
const PROVIDER_PROFILES_PATH = '/api/v1/provider-profiles' as const
const PROVIDER_ENDPOINT_VALIDATION_PATH = '/api/v1/provider-endpoints/validate' as const
const CONSENT_PREVIEW_PATH = '/api/v1/consents/preview' as const
const CONSENTS_PATH = '/api/v1/consents' as const
const JOBS_PATH = '/api/v1/jobs' as const
const JOB_SHUTDOWN_PATH = '/api/v1/jobs/shutdown' as const
const MAX_RESPONSE_BYTES = 1_048_576
const MAX_REQUEST_BYTES = 65_600
const REQUEST_TIMEOUT_MS = 3_000
const JOB_EVENT_INACTIVITY_TIMEOUT_MS = 45_000

type SecretOperation = 'status' | 'set' | 'delete'
type SidecarPath =
  | typeof CAPABILITIES_PATH
  | typeof STARTUP_DIAGNOSTICS_PATH
  | typeof SETTINGS_PATH
  | typeof PROVIDER_CONFIGURATION_PATH
  | typeof PROVIDER_PROFILES_PATH
  | typeof PROVIDER_ENDPOINT_VALIDATION_PATH
  | typeof CONSENT_PREVIEW_PATH
  | typeof CONSENTS_PATH
  | typeof JOBS_PATH
  | typeof JOB_SHUTDOWN_PATH
  | `/api/v1/jobs/${string}`
  | `/api/v1/jobs/${string}/cancel`
  | `/api/v1/jobs/${string}/events`
  | `/api/v1/consents/${string}/revoke`
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
  | 'provider_configuration_conflict'
  | 'provider_configuration_invalid'
  | 'endpoint_rejected'
  | 'consent_invalid'
  | 'consent_preview_stale'
  | 'job_id_invalid'
  | 'job_not_found'
  | 'job_event_cursor_invalid'
  | 'job_event_replay_expired'
  | 'job_service_unavailable'
  | 'job_subscriber_limit'
  | 'job_subscription_closed'
  | 'job_event_stream_failed'

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
  getProviderConfiguration(signal?: AbortSignal): Promise<ProviderConfiguration>
  createProviderProfile(
    request: ProviderProfileCreateRequest,
    signal?: AbortSignal,
  ): Promise<ProviderConfiguration>
  validateProviderEndpoint(
    request: ProviderEndpointValidationRequest,
    signal?: AbortSignal,
  ): Promise<ProviderEndpointValidation>
  previewConsent(request: ConsentPreviewRequest, signal?: AbortSignal): Promise<ConsentPreview>
  createConsent(request: ConsentCreateRequest, signal?: AbortSignal): Promise<ProviderConfiguration>
  revokeConsent(request: ConsentRevokeRequest, signal?: AbortSignal): Promise<ProviderConfiguration>
  prepareJobShutdown(
    action: JobShutdownAction,
    signal?: AbortSignal,
  ): Promise<JobShutdownAssessment>
  listJobs(signal?: AbortSignal): Promise<Readonly<JobList>>
  getJob(request: JobRequest, signal?: AbortSignal): Promise<Readonly<JobSnapshot>>
  cancelJob(request: JobRequest, signal?: AbortSignal): Promise<Readonly<JobSnapshot>>
  streamJobEvents(
    request: JobEventSubscriptionRequest,
    listener: (event: Readonly<JobEvent>) => void,
    signal?: AbortSignal,
  ): Promise<void>
}

export type JobShutdownAction = 'wait' | 'cancel'

export interface JobShutdownAssessment {
  readonly schema_version: 1
  readonly safe_to_quit: true
  readonly active_jobs: readonly []
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

function consentRevokePath(name: string): SidecarPath {
  if (!/^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$/.test(name)) {
    throw new SidecarClientError('consent_invalid')
  }
  return `/api/v1/consents/${name}/revoke`
}

function jobPath(jobId: string, suffix: '' | '/cancel' | '/events' = ''): SidecarPath {
  if (!/^j[0-9]{6,12}$/.test(jobId)) throw new SidecarClientError('job_id_invalid')
  return `/api/v1/jobs/${jobId}${suffix}`
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

function failureCode(response: Readonly<SidecarHttpResponse>): string | undefined {
  if (!validJsonResponse(response)) return undefined
  try {
    const payload = JSON.parse(response.body) as unknown
    if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return undefined
    const code = (payload as Readonly<Record<string, unknown>>).code
    return typeof code === 'string' && /^[A-Z][A-Z0-9_]{0,95}$/.test(code) ? code : undefined
  } catch {
    return undefined
  }
}

function providerFailure(response: Readonly<SidecarHttpResponse>): SidecarClientError {
  const code = failureCode(response)
  if (code === 'PROVIDER_CONFIGURATION_CONFLICT') {
    return new SidecarClientError('provider_configuration_conflict')
  }
  if (code?.startsWith('ENDPOINT_')) return new SidecarClientError('endpoint_rejected')
  if (code?.startsWith('PROVIDER_') || [400, 404, 409, 422].includes(response.statusCode)) {
    return new SidecarClientError('provider_configuration_invalid')
  }
  return new SidecarClientError('request_failed')
}

function endpointFailure(response: Readonly<SidecarHttpResponse>): SidecarClientError {
  const code = failureCode(response)
  if (code?.startsWith('ENDPOINT_') || [400, 403, 409, 422, 502, 503].includes(response.statusCode)) {
    return new SidecarClientError('endpoint_rejected')
  }
  return new SidecarClientError('request_failed')
}

function consentFailure(response: Readonly<SidecarHttpResponse>): SidecarClientError {
  const code = failureCode(response)
  if (code === 'PROVIDER_CONFIGURATION_CONFLICT') {
    return new SidecarClientError('provider_configuration_conflict')
  }
  if (code === 'CONSENT_PREVIEW_STALE') return new SidecarClientError('consent_preview_stale')
  if (code?.startsWith('CONSENT_') || code?.startsWith('PROVIDER_PROFILE_')
    || [400, 404, 409, 422].includes(response.statusCode)) {
    return new SidecarClientError('consent_invalid')
  }
  return new SidecarClientError('request_failed')
}

function jobFailure(response: Readonly<SidecarHttpResponse>): SidecarClientError {
  const code = failureCode(response)
  if (code === 'JOB_ID_INVALID') return new SidecarClientError('job_id_invalid')
  if (code === 'JOB_NOT_FOUND' || response.statusCode === 404) {
    return new SidecarClientError('job_not_found')
  }
  if (code === 'JOB_EVENT_CURSOR_INVALID') {
    return new SidecarClientError('job_event_cursor_invalid')
  }
  if (code === 'JOB_EVENT_REPLAY_EXPIRED' || response.statusCode === 410) {
    return new SidecarClientError('job_event_replay_expired')
  }
  if (code === 'JOB_SUBSCRIBER_LIMIT' || response.statusCode === 429) {
    return new SidecarClientError('job_subscriber_limit')
  }
  if (code === 'JOB_SUBSCRIPTION_CLOSED') {
    return new SidecarClientError('job_subscription_closed')
  }
  if (code === 'JOB_SERVICE_CLOSED' || code === 'JOB_SERVICE_UNAVAILABLE'
    || response.statusCode === 503) {
    return new SidecarClientError('job_service_unavailable')
  }
  return new SidecarClientError('request_failed')
}

function parseJobStreamFailure(payload: unknown): SidecarClientError {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    return new SidecarClientError('job_event_stream_failed')
  }
  const record = payload as Readonly<Record<string, unknown>>
  const keys = Object.keys(record).sort()
  if (keys.length !== 4
    || keys[0] !== 'code'
    || keys[1] !== 'message'
    || keys[2] !== 'remediation'
    || keys[3] !== 'schema_version'
    || record.schema_version !== 1
    || record.code !== 'JOB_EVENT_REPLAY_EXPIRED'
    || record.message !== 'The bounded job-event replay window is no longer available.'
    || record.remediation !== 'Fetch the current job snapshot, then reconnect from its sequence.') {
    return new SidecarClientError('job_event_stream_failed')
  }
  return new SidecarClientError('job_event_replay_expired')
}

function streamFixedJobEvents(
  sidecar: Readonly<AuthenticatedSidecarSession>,
  subscription: JobEventSubscriptionRequest,
  listener: (event: Readonly<JobEvent>) => void,
  inactivityTimeoutMs: number,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new SidecarClientError('cancelled'))
      return
    }
    if (!/^sub_[a-f0-9]{32}$/.test(subscription.subscription_id)
      || !Number.isInteger(subscription.after)
      || subscription.after < 0
      || subscription.after > 9_999_999_999) {
      reject(new SidecarClientError('job_event_cursor_invalid'))
      return
    }

    const path = jobPath(subscription.job_id, '/events')
    let responseStream: IncomingMessage | undefined
    let settled = false
    let deadline: ReturnType<typeof setTimeout> | undefined
    let buffer = ''
    let lastSequence = subscription.after
    let terminalSeen = false
    const decoder = new StringDecoder('utf8')

    const cleanup = () => {
      if (deadline !== undefined) clearTimeout(deadline)
      signal?.removeEventListener('abort', abort)
    }
    const resolveOnce = () => {
      if (settled) return
      settled = true
      cleanup()
      resolve()
    }
    const rejectOnce = (error: unknown) => {
      if (settled) return
      settled = true
      cleanup()
      reject(error instanceof SidecarClientError
        ? error
        : new SidecarClientError('job_event_stream_failed'))
    }
    const abort = () => {
      const error = new SidecarClientError('cancelled')
      responseStream?.destroy(error)
      request.destroy(error)
      rejectOnce(error)
    }
    const failStream = (error: SidecarClientError) => {
      rejectOnce(error)
      responseStream?.destroy()
      request.destroy()
    }
    const resetDeadline = (timeoutMs: number) => {
      if (deadline !== undefined) clearTimeout(deadline)
      deadline = setTimeout(() => {
        failStream(new SidecarClientError('job_event_stream_failed'))
      }, timeoutMs)
    }
    const resetInactivityDeadline = () => resetDeadline(inactivityTimeoutMs)

    const processFrame = (frame: string) => {
      const lines = frame.split('\n')
      if (lines.every((line) => line.startsWith(':'))) {
        resetInactivityDeadline()
        return
      }
      if (terminalSeen) throw new SidecarClientError('job_event_stream_failed')
      const fields = new Map<string, string>()
      for (const line of lines) {
        const separator = line.indexOf(': ')
        if (separator <= 0) throw new SidecarClientError('job_event_stream_failed')
        const name = line.slice(0, separator)
        if (!['id', 'event', 'data'].includes(name) || fields.has(name)) {
          throw new SidecarClientError('job_event_stream_failed')
        }
        fields.set(name, line.slice(separator + 2))
      }
      const eventName = fields.get('event')
      const data = fields.get('data')
      if (eventName === undefined || data === undefined) {
        throw new SidecarClientError('job_event_stream_failed')
      }
      let payload: unknown
      try {
        payload = JSON.parse(data) as unknown
      } catch {
        throw new SidecarClientError('job_event_stream_failed')
      }
      if (eventName === 'resync-required') {
        if (fields.size !== 2 || fields.has('id')) {
          throw new SidecarClientError('job_event_stream_failed')
        }
        throw parseJobStreamFailure(payload)
      }
      const id = fields.get('id')
      if (fields.size !== 3 || id === undefined || !/^[1-9][0-9]{0,9}$/.test(id)) {
        throw new SidecarClientError('job_event_stream_failed')
      }
      const result = parseJobEventResult({
        ok: true,
        protocolVersion: DESKTOP_PROTOCOL_VERSION,
        data: payload,
      })
      if (!result.ok || result.data === undefined
        || result.data.kind !== eventName
        || result.data.snapshot.job_id !== subscription.job_id
        || result.data.sequence !== Number(id)
        || result.data.sequence <= lastSequence) {
        throw new SidecarClientError('job_event_stream_failed')
      }
      lastSequence = result.data.sequence
      terminalSeen = result.data.kind === 'terminal'
      listener(result.data)
      resetInactivityDeadline()
    }

    const processBuffer = (final = false) => {
      buffer = buffer.replaceAll('\r\n', '\n')
      if (Buffer.byteLength(buffer, 'utf8') > MAX_RESPONSE_BYTES || buffer.includes('\r')) {
        throw new SidecarClientError('job_event_stream_failed')
      }
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        if (frame.length > 0) processFrame(frame)
        boundary = buffer.indexOf('\n\n')
      }
      if (final && buffer.length > 0) throw new SidecarClientError('job_event_stream_failed')
    }

    const headers: Record<string, string> = {
      Accept: 'text/event-stream',
      Authorization: `Bearer ${sidecar.bearerToken}`,
      Connection: 'close',
      Host: `${sidecar.host}:${sidecar.port}`,
      'X-Ancestry-API-Version': sidecar.contract,
      'X-Ancestry-App-Build': sidecar.appBuild,
    }
    if (subscription.after > 0) headers['Last-Event-ID'] = String(subscription.after)
    const request = httpRequest({
      hostname: sidecar.host,
      port: sidecar.port,
      path,
      method: 'GET',
      headers,
    }, (response) => {
      responseStream = response
      const contentType = Array.isArray(response.headers['content-type'])
        ? (response.headers['content-type'][0] ?? '')
        : (response.headers['content-type'] ?? '')
      if (response.statusCode !== 200 || !/^text\/event-stream(?:\s*;|$)/i.test(contentType)) {
        const chunks: Buffer[] = []
        let bytes = 0
        response.on('data', (chunk: Buffer | string) => {
          const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
          bytes += value.length
          if (bytes > MAX_RESPONSE_BYTES) {
            failStream(new SidecarClientError('invalid_response'))
            return
          }
          chunks.push(value)
        })
        response.on('end', () => {
          if (settled) return
          rejectOnce(jobFailure({
            statusCode: response.statusCode ?? 0,
            contentType,
            body: Buffer.concat(chunks).toString('utf8'),
          }))
        })
        response.on('error', rejectOnce)
        return
      }
      resetInactivityDeadline()
      response.on('data', (chunk: Buffer | string) => {
        if (settled) return
        try {
          buffer += decoder.write(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
          processBuffer()
        } catch (error) {
          failStream(error instanceof SidecarClientError
            ? error
            : new SidecarClientError('job_event_stream_failed'))
        }
      })
      response.on('end', () => {
        if (settled) return
        try {
          buffer += decoder.end()
          processBuffer(true)
          resolveOnce()
        } catch (error) {
          rejectOnce(error)
        }
      })
      response.on('error', rejectOnce)
    })
    request.on('error', rejectOnce)
    resetDeadline(REQUEST_TIMEOUT_MS)
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) {
      abort()
      return
    }
    request.end()
  })
}

function parseJobShutdownAssessment(
  response: Readonly<SidecarHttpResponse>,
): Readonly<JobShutdownAssessment> {
  if (!validJsonResponse(response)) throw new SidecarClientError('invalid_response')
  try {
    const payload = JSON.parse(response.body) as unknown
    if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
      throw new SidecarClientError('invalid_response')
    }
    const record = payload as Readonly<Record<string, unknown>>
    const keys = Object.keys(record).sort()
    if (keys.length !== 3
      || keys[0] !== 'active_jobs'
      || keys[1] !== 'safe_to_quit'
      || keys[2] !== 'schema_version'
      || record.schema_version !== 1
      || record.safe_to_quit !== true
      || !Array.isArray(record.active_jobs)
      || record.active_jobs.length !== 0) {
      throw new SidecarClientError('invalid_response')
    }
    return Object.freeze({
      schema_version: 1,
      safe_to_quit: true,
      active_jobs: Object.freeze([] as const),
    })
  } catch (error) {
    if (error instanceof SidecarClientError) throw error
    throw new SidecarClientError('invalid_response')
  }
}

export function createSidecarClient(dependencies: Readonly<{
  session(): Readonly<AuthenticatedSidecarSession> | undefined
  request?: SidecarRequest
  jobEventInactivityTimeoutMs?: number
}>): Readonly<SidecarClient> {
  const jobEventInactivityTimeoutMs = dependencies.jobEventInactivityTimeoutMs
    ?? JOB_EVENT_INACTIVITY_TIMEOUT_MS
  if (!Number.isFinite(jobEventInactivityTimeoutMs) || jobEventInactivityTimeoutMs <= 0) {
    throw new Error('Job event inactivity timeout must be positive.')
  }
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
    async getProviderConfiguration(signal?: AbortSignal) {
      const response = await perform(PROVIDER_CONFIGURATION_PATH, signal)
      if (response.statusCode !== 200) throw providerFailure(response)
      return parseJson(response, parseProviderConfigurationResult)
    },
    async createProviderProfile(profile: ProviderProfileCreateRequest, signal?: AbortSignal) {
      const response = await perform(PROVIDER_PROFILES_PATH, signal, {
        method: 'POST',
        body: JSON.stringify(profile),
      })
      if (response.statusCode !== 200) throw providerFailure(response)
      return parseJson(response, parseProviderConfigurationResult)
    },
    async validateProviderEndpoint(
      endpoint: ProviderEndpointValidationRequest,
      signal?: AbortSignal,
    ) {
      const response = await perform(PROVIDER_ENDPOINT_VALIDATION_PATH, signal, {
        method: 'POST',
        body: JSON.stringify(endpoint),
      })
      if (response.statusCode !== 200) throw endpointFailure(response)
      return parseJson(response, parseProviderEndpointValidationResult)
    },
    async previewConsent(consent: ConsentPreviewRequest, signal?: AbortSignal) {
      const response = await perform(CONSENT_PREVIEW_PATH, signal, {
        method: 'POST',
        body: JSON.stringify(consent),
      })
      if (response.statusCode !== 200) throw consentFailure(response)
      return parseJson(response, parseConsentPreviewResult)
    },
    async createConsent(consent: ConsentCreateRequest, signal?: AbortSignal) {
      const response = await perform(CONSENTS_PATH, signal, {
        method: 'POST',
        body: JSON.stringify(consent),
      })
      if (response.statusCode !== 200) throw consentFailure(response)
      return parseJson(response, parseProviderConfigurationResult)
    },
    async revokeConsent(consent: ConsentRevokeRequest, signal?: AbortSignal) {
      const response = await perform(consentRevokePath(consent.name), signal, {
        method: 'POST',
        body: JSON.stringify({
          schema_version: consent.schema_version,
          expected_revision: consent.expected_revision,
        }),
      })
      if (response.statusCode !== 200) throw consentFailure(response)
      return parseJson(response, parseProviderConfigurationResult)
    },
    async prepareJobShutdown(action: JobShutdownAction, signal?: AbortSignal) {
      const response = await perform(JOB_SHUTDOWN_PATH, signal, {
        method: 'POST',
        body: JSON.stringify({ schema_version: 1, action, timeout_seconds: 2 }),
      })
      if (response.statusCode !== 200) throw new SidecarClientError('request_failed')
      return parseJobShutdownAssessment(response)
    },
    async listJobs(signal?: AbortSignal) {
      const response = await perform(JOBS_PATH, signal)
      if (response.statusCode !== 200) throw jobFailure(response)
      return parseJson(response, parseJobListResult)
    },
    async getJob(job: JobRequest, signal?: AbortSignal) {
      const response = await perform(jobPath(job.job_id), signal)
      if (response.statusCode !== 200) throw jobFailure(response)
      return parseJson(response, parseJobSnapshotResult)
    },
    async cancelJob(job: JobRequest, signal?: AbortSignal) {
      const response = await perform(jobPath(job.job_id, '/cancel'), signal, { method: 'POST' })
      if (response.statusCode !== 200) throw jobFailure(response)
      return parseJson(response, parseJobSnapshotResult)
    },
    async streamJobEvents(
      subscription: JobEventSubscriptionRequest,
      listener: (event: Readonly<JobEvent>) => void,
      signal?: AbortSignal,
    ) {
      if (signal?.aborted) throw new SidecarClientError('cancelled')
      const session = dependencies.session()
      if (!session) throw new SidecarClientError('unavailable')
      try {
        await streamFixedJobEvents(
          session,
          subscription,
          listener,
          jobEventInactivityTimeoutMs,
          signal,
        )
      } catch (error) {
        if (signal?.aborted) throw new SidecarClientError('cancelled')
        if (error instanceof SidecarClientError) throw error
        throw new SidecarClientError('job_event_stream_failed')
      }
    },
  })
}

export function createSidecarCapabilitiesClient(dependencies: Readonly<{
  session(): Readonly<AuthenticatedSidecarSession> | undefined
  request?: SidecarRequest
  jobEventInactivityTimeoutMs?: number
}>): Readonly<SidecarClient> {
  return createSidecarClient(dependencies)
}
