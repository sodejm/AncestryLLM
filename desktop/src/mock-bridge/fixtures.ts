/** Defines deeply frozen fixture payloads and helpers for the mock desktop bridge. */
import {
  DESKTOP_PROTOCOL_VERSION,
  type AppInfo,
  type BridgeResult,
  type CapabilityManifest,
  type LocalPreferences,
  type StartupDiagnostics,
} from '../shared-contract/desktop'

export function deepFreeze<T>(value: T): Readonly<T> {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    for (const item of Object.values(value)) deepFreeze(item)
  }
  return value
}

type BridgeSuccess<T> = Extract<BridgeResult<T>, { ok: true }>

const success = <T>(data: T): BridgeSuccess<T> => deepFreeze({
  ok: true,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  data,
}) as BridgeSuccess<T>

export const appInfoFixture = success<AppInfo>({
  applicationName: 'AncestryLLM',
  appVersion: '0.5.0-dev',
  buildChannel: 'development',
})

export const readyDiagnosticsFixture = success<StartupDiagnostics>({
  state: 'ready',
  failure: null,
  automaticRestartsRemaining: 2,
  manualRetriesRemaining: 1,
})

export const degradedDiagnosticsFixture = success<StartupDiagnostics>({
  state: 'degraded',
  failure: 'startup_failed',
  automaticRestartsRemaining: 0,
  manualRetriesRemaining: 1,
})

export const capabilitiesFixture = success<CapabilityManifest>({
  api: {
    namespace: '/api/v1',
    contract: 'ancestryllm.internal-api/1',
    application_contract: 'ancestryllm.application/0.3',
  },
  modules: [],
  request_policy: {
    max_body_bytes: 1_048_576,
    max_json_depth: 16,
    max_collection_items: 1_000,
    max_string_characters: 65_536,
  },
  pagination: {
    default_limit: 25,
    maximum_limit: 100,
    maximum_cursor_characters: 256,
  },
})

export const preferencesFixture = success<LocalPreferences>({
  colorScheme: 'system',
  reducedMotion: false,
  onboardingCompleted: false,
  schemaVersion: 1,
  revision: 0,
})

export const unavailableFixture: BridgeResult<CapabilityManifest> = deepFreeze({
  ok: false,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  error: {
    code: 'SIDECAR_UNAVAILABLE',
    message: 'The private service is unavailable.',
    remediation: 'Retry the service or restart AncestryLLM.',
  },
})
