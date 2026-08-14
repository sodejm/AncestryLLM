/** Defines immutable fictional desktop bridge fixtures for development and tests. */
import {
  DESKTOP_PROTOCOL_VERSION,
  type AppInfo,
  type ApplicationSettings,
  type BridgeResult,
  type CapabilityManifest,
  type LocalPreferences,
  type StartupDiagnosticReport,
  type StartupDiagnostics,
} from '../shared-contract/desktop'

/** Recursively freezes fixture objects so renderer tests cannot mutate shared state. */
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

/** Successful application identity response used by the offline bridge. */
export const appInfoFixture = success<AppInfo>({
  applicationName: 'AncestryLLM',
  appVersion: '0.5.0-dev',
  buildChannel: 'development',
})

/** Startup report for a ready local sidecar with all required checks passing. */
export const readyStartupReportFixture = deepFreeze<StartupDiagnosticReport>({
  schema_version: 1,
  status: 'ready',
  platform: { operating_system: 'macos', architecture: 'arm64' },
  components: [
    {
      component: 'configuration',
      status: 'ready',
      code: 'CONFIGURATION_READY',
      message: 'Configuration is ready.',
      remediation: null,
      restart_required: false,
      blocks_mutations: false,
    },
    {
      component: 'sqlcipher',
      status: 'ready',
      code: 'SQLCIPHER_READY',
      message: 'SQLCipher is ready.',
      remediation: null,
      restart_required: false,
      blocks_mutations: false,
    },
    {
      component: 'keyring',
      status: 'ready',
      code: 'KEYRING_READY',
      message: 'Credential storage is ready.',
      remediation: null,
      restart_required: false,
      blocks_mutations: false,
    },
    {
      component: 'workspace',
      status: 'ready',
      code: 'DATABASE_DIRECTORY_READY',
      message: 'Workspace is ready.',
      remediation: null,
      restart_required: false,
      blocks_mutations: false,
    },
  ],
})

/** Successful startup-diagnostics response derived from the ready report. */
export const readyDiagnosticsFixture = success<StartupDiagnostics>({
  state: 'ready',
  failure: null,
  automaticRestartsRemaining: 2,
  manualRetriesRemaining: 1,
  report: readyStartupReportFixture,
})

/** Degraded startup-diagnostics response used to exercise recovery UI. */
export const degradedDiagnosticsFixture = success<StartupDiagnostics>({
  state: 'degraded',
  failure: 'startup_failed',
  automaticRestartsRemaining: 0,
  manualRetriesRemaining: 1,
  report: null,
})

/** Stable capability manifest exposed by the offline renderer bridge. */
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

/** Provider and consent settings used by offline settings views. */
export const settingsFixture = success<ApplicationSettings>({
  schema_version: 1,
  revision: 0,
  fields: [
    {
      key: 'providers.default',
      label: 'Default provider',
      help: 'Provider selected when a command does not specify one explicitly.',
      type: 'string',
      value: 'none',
      default_value: 'none',
      validation: {
        allowed_values: ['none', 'ollama', 'openai', 'anthropic', 'gemini', 'openrouter'],
        minimum: null,
        maximum: null,
      },
      restart_required: false,
      sensitive: false,
    },
    {
      key: 'limits.max_query_rows',
      label: 'Maximum query rows',
      help: 'Largest number of database rows returned by one query.',
      type: 'integer',
      value: 100,
      default_value: 100,
      validation: { allowed_values: [], minimum: 1, maximum: 10_000 },
      restart_required: false,
      sensitive: false,
    },
    {
      key: 'limits.max_output_chars',
      label: 'Maximum output characters',
      help: 'Largest rendered output accepted from one operation.',
      type: 'integer',
      value: 100_000,
      default_value: 100_000,
      validation: { allowed_values: [], minimum: 1_000, maximum: 5_000_000 },
      restart_required: false,
      sensitive: false,
    },
    {
      key: 'limits.query_timeout_seconds',
      label: 'Query timeout',
      help: 'Maximum seconds allowed for one local database query.',
      type: 'number',
      value: 10,
      default_value: 10,
      validation: { allowed_values: [], minimum: 0.1, maximum: 300 },
      restart_required: false,
      sensitive: false,
    },
    {
      key: 'limits.provider_timeout_seconds',
      label: 'Provider timeout',
      help: 'Maximum seconds allowed for one explicitly selected provider request.',
      type: 'number',
      value: 60,
      default_value: 60,
      validation: { allowed_values: [], minimum: 1, maximum: 600 },
      restart_required: false,
      sensitive: false,
    },
  ],
})

/** Local user-interface preferences used by offline renderer views. */
export const preferencesFixture = success<LocalPreferences>({
  colorScheme: 'system',
  reducedMotion: false,
  onboardingCompleted: false,
  schemaVersion: 1,
  revision: 0,
})

/** Fail-closed capability response used when the local sidecar is unavailable. */
export const unavailableFixture: BridgeResult<CapabilityManifest> = deepFreeze({
  ok: false,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  error: {
    code: 'SIDECAR_UNAVAILABLE',
    message: 'The private service is unavailable.',
    remediation: 'Retry the service or restart AncestryLLM.',
  },
})
