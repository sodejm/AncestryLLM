/** Parses and validates desktop bridge requests and results at the renderer and preload runtime boundary. */
import {
  DESKTOP_PROTOCOL_VERSION,
  type AppInfo,
  type BridgeErrorCode,
  type BridgeResult,
  type CapabilityManifest,
  type DesktopColorScheme,
  type LocalPreferences,
  type PreferenceUpdate,
  type StartupDiagnostics,
} from './desktop'

type Parser<T> = (value: unknown) => T

const colorSchemes: readonly DesktopColorScheme[] = ['system', 'light', 'dark']
const bridgeErrorCodes: readonly BridgeErrorCode[] = [
  'INVALID_REQUEST',
  'UNAUTHORIZED_SENDER',
  'INVALID_RESPONSE',
  'SIDECAR_UNAVAILABLE',
  'SIDECAR_REQUEST_FAILED',
  'PREFERENCES_UNAVAILABLE',
  'PREFERENCES_CONFLICT',
  'INTERNAL_ERROR',
]
const startupStates = ['starting', 'ready', 'degraded', 'stopped'] as const
const startupFailures = ['startup_failed', 'startup_timeout', 'incompatible_build', 'crash_loop'] as const
const identifierPattern = /^[A-Za-z0-9._:-]+$/
const dispatchKeyPattern = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]): boolean =>
  Object.keys(value).length === keys.length && keys.every((key) => key in value)
const onlyKeys = (value: Record<string, unknown>, keys: readonly string[]): boolean =>
  Object.keys(value).every((key) => keys.includes(key))
const bounded = (value: unknown, minimum: number, maximum: number): value is string =>
  typeof value === 'string' && value.length >= minimum && value.length <= maximum
const integer = (value: unknown, minimum: number, maximum: number): value is number =>
  Number.isInteger(value) && (value as number) >= minimum && (value as number) <= maximum

function invalidResponse(): never { throw new Error('Invalid bridge response') }

function deepFreeze<T>(value: T): Readonly<T> {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    for (const item of Object.values(value)) deepFreeze(item)
  }
  return value
}

export function parseColorScheme(value: unknown): DesktopColorScheme {
  if (typeof value !== 'string' || !colorSchemes.includes(value as DesktopColorScheme)) {
    throw new Error('Invalid color scheme')
  }
  return value as DesktopColorScheme
}

export function parsePreferenceUpdate(value: unknown): PreferenceUpdate {
  const keys = ['expectedRevision', 'colorScheme', 'reducedMotion', 'onboardingCompleted'] as const
  if (!record(value) || !('expectedRevision' in value) || Object.keys(value).length < 2 || !onlyKeys(value, keys)) {
    throw new Error('Invalid preference update')
  }
  try {
    if (!integer(value.expectedRevision, 0, Number.MAX_SAFE_INTEGER)) throw new Error()
    if ('colorScheme' in value) parseColorScheme(value.colorScheme)
    if ('reducedMotion' in value && typeof value.reducedMotion !== 'boolean') throw new Error()
    if ('onboardingCompleted' in value && typeof value.onboardingCompleted !== 'boolean') throw new Error()
  } catch {
    throw new Error('Invalid preference update')
  }
  return deepFreeze({ ...value }) as PreferenceUpdate
}

function parseAppInfo(value: unknown): AppInfo {
  if (!record(value) || !exactKeys(value, ['applicationName', 'appVersion', 'buildChannel'])
    || value.applicationName !== 'AncestryLLM' || !bounded(value.appVersion, 1, 128)
    || (value.buildChannel !== 'development' && value.buildChannel !== 'packaged')) invalidResponse()
  return value as unknown as AppInfo
}

function parseStartupDiagnostics(value: unknown): StartupDiagnostics {
  if (!record(value) || !exactKeys(value, ['state', 'failure', 'automaticRestartsRemaining', 'manualRetriesRemaining'])
    || !startupStates.includes(value.state as typeof startupStates[number])
    || (value.failure !== null && !startupFailures.includes(value.failure as typeof startupFailures[number]))
    || !integer(value.automaticRestartsRemaining, 0, 100)
    || !integer(value.manualRetriesRemaining, 0, 100)) invalidResponse()
  return value as unknown as StartupDiagnostics
}

function parseCapabilityManifest(value: unknown): CapabilityManifest {
  if (!record(value) || !exactKeys(value, ['api', 'modules', 'request_policy', 'pagination'])) invalidResponse()
  const api = value.api
  if (!record(api) || !exactKeys(api, ['namespace', 'contract', 'application_contract'])
    || api.namespace !== '/api/v1' || api.contract !== 'ancestryllm.internal-api/1'
    || api.application_contract !== 'ancestryllm.application/0.3') invalidResponse()

  if (!Array.isArray(value.modules) || value.modules.length > 128) invalidResponse()
  for (const module of value.modules) {
    if (!record(module) || !exactKeys(module, ['module_id', 'name', 'summary', 'actions'])
      || !bounded(module.module_id, 1, 96) || !identifierPattern.test(module.module_id)
      || !bounded(module.name, 1, 128) || !bounded(module.summary, 1, 512)
      || !Array.isArray(module.actions) || module.actions.length < 1 || module.actions.length > 128) invalidResponse()
    for (const action of module.actions) {
      if (!record(action) || !exactKeys(action, ['dispatch_key', 'name', 'summary'])
        || !bounded(action.dispatch_key, 3, 193) || !dispatchKeyPattern.test(action.dispatch_key)
        || !bounded(action.name, 1, 96) || !identifierPattern.test(action.name)
        || !bounded(action.summary, 1, 512)) invalidResponse()
    }
  }

  const requestPolicy = value.request_policy
  if (!record(requestPolicy)
    || !exactKeys(requestPolicy, ['max_body_bytes', 'max_json_depth', 'max_collection_items', 'max_string_characters'])
    || !integer(requestPolicy.max_body_bytes, 1, 1_048_576)
    || !integer(requestPolicy.max_json_depth, 1, 64)
    || !integer(requestPolicy.max_collection_items, 1, 10_000)
    || !integer(requestPolicy.max_string_characters, 1, 1_048_576)) invalidResponse()

  const pagination = value.pagination
  if (!record(pagination)
    || !exactKeys(pagination, ['default_limit', 'maximum_limit', 'maximum_cursor_characters'])
    || !integer(pagination.default_limit, 1, 100)
    || !integer(pagination.maximum_limit, 1, 100)
    || !integer(pagination.maximum_cursor_characters, 32, 1_024)
    || (pagination.default_limit as number) > (pagination.maximum_limit as number)) invalidResponse()
  return value as unknown as CapabilityManifest
}

function parsePreferences(value: unknown): LocalPreferences {
  if (!record(value) || !exactKeys(value, ['colorScheme', 'reducedMotion', 'onboardingCompleted', 'schemaVersion', 'revision'])) invalidResponse()
  try { parseColorScheme(value.colorScheme) } catch { invalidResponse() }
  if (typeof value.reducedMotion !== 'boolean' || typeof value.onboardingCompleted !== 'boolean'
    || value.schemaVersion !== 1 || !integer(value.revision, 0, Number.MAX_SAFE_INTEGER)) invalidResponse()
  return value as unknown as LocalPreferences
}

function parseBridgeResult<T>(value: unknown, parseData: Parser<T>): BridgeResult<T> {
  if (!record(value) || value.protocolVersion !== DESKTOP_PROTOCOL_VERSION || typeof value.ok !== 'boolean') invalidResponse()
  if (value.ok) {
    if (!exactKeys(value, ['ok', 'protocolVersion', 'data'])) invalidResponse()
    const data = parseData(value.data)
    return deepFreeze({ ok: true, protocolVersion: DESKTOP_PROTOCOL_VERSION, data })
  }
  if (!exactKeys(value, ['ok', 'protocolVersion', 'error']) || !record(value.error)
    || !exactKeys(value.error, ['code', 'message', 'remediation'])
    || !bridgeErrorCodes.includes(value.error.code as BridgeErrorCode)
    || !bounded(value.error.message, 1, 240) || !bounded(value.error.remediation, 1, 240)) invalidResponse()
  return deepFreeze(value as unknown as BridgeResult<T>)
}

export const parseAppInfoResult = (value: unknown): BridgeResult<AppInfo> => parseBridgeResult(value, parseAppInfo)
export const parseStartupDiagnosticsResult = (value: unknown): BridgeResult<StartupDiagnostics> => parseBridgeResult(value, parseStartupDiagnostics)
export const parseCapabilitiesResult = (value: unknown): BridgeResult<CapabilityManifest> => parseBridgeResult(value, parseCapabilityManifest)
export const parsePreferencesResult = (value: unknown): BridgeResult<LocalPreferences> => parseBridgeResult(value, parsePreferences)
