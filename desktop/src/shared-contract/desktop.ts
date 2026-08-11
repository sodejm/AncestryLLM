export const DESKTOP_PROTOCOL_VERSION = '1' as const

export type DesktopColorScheme = 'system' | 'light' | 'dark'
export type DesktopBuildChannel = 'development' | 'packaged'
export type StartupState = 'starting' | 'ready' | 'degraded' | 'stopped'
export type StartupFailure = 'startup_failed' | 'startup_timeout' | 'incompatible_build' | 'crash_loop' | null

export interface AppInfo {
  applicationName: 'AncestryLLM'
  appVersion: string
  buildChannel: DesktopBuildChannel
}

export interface StartupDiagnostics {
  state: StartupState
  failure: StartupFailure
  automaticRestartsRemaining: number
  manualRetriesRemaining: number
}

export interface CapabilityAction {
  dispatch_key: string
  name: string
  summary: string
}

export interface CapabilityModule {
  module_id: string
  name: string
  summary: string
  actions: readonly CapabilityAction[]
}

export interface CapabilityManifest {
  api: Readonly<{
    namespace: '/api/v1'
    contract: 'ancestryllm.internal-api/1'
    application_contract: 'ancestryllm.application/0.3'
  }>
  modules: readonly CapabilityModule[]
  request_policy: Readonly<{
    max_body_bytes: number
    max_json_depth: number
    max_collection_items: number
    max_string_characters: number
  }>
  pagination: Readonly<{
    default_limit: number
    maximum_limit: number
    maximum_cursor_characters: number
  }>
}

export interface LocalPreferences {
  colorScheme: DesktopColorScheme
  reducedMotion: boolean
  onboardingCompleted: boolean
  schemaVersion: 1
  revision: number
}

export type PreferenceUpdate = Readonly<{
  expectedRevision: number
  colorScheme?: DesktopColorScheme
  reducedMotion?: boolean
  onboardingCompleted?: boolean
}>

export type BridgeErrorCode =
  | 'INVALID_REQUEST'
  | 'UNAUTHORIZED_SENDER'
  | 'INVALID_RESPONSE'
  | 'BRIDGE_OVERLOADED'
  | 'REQUEST_CANCELLED'
  | 'REQUEST_TIMEOUT'
  | 'SIDECAR_UNAVAILABLE'
  | 'SIDECAR_REQUEST_FAILED'
  | 'PREFERENCES_UNAVAILABLE'
  | 'PREFERENCES_CONFLICT'
  | 'INTERNAL_ERROR'

export interface BridgeError {
  code: BridgeErrorCode
  message: string
  remediation: string
}

export type BridgeResult<T> =
  | Readonly<{ ok: true; protocolVersion: typeof DESKTOP_PROTOCOL_VERSION; data: Readonly<T> }>
  | Readonly<{ ok: false; protocolVersion: typeof DESKTOP_PROTOCOL_VERSION; error: Readonly<BridgeError> }>

export interface AncestryBridge {
  getAppInfo(): Promise<BridgeResult<AppInfo>>
  getStartupDiagnostics(): Promise<BridgeResult<StartupDiagnostics>>
  getCapabilities(): Promise<BridgeResult<CapabilityManifest>>
  retrySidecar(): Promise<BridgeResult<StartupDiagnostics>>
  getPreferences(): Promise<BridgeResult<LocalPreferences>>
  updatePreferences(update: PreferenceUpdate): Promise<BridgeResult<LocalPreferences>>
}

export const desktopChannels = Object.freeze({
  getAppInfo: 'ancestry:desktop:get-app-info',
  getStartupDiagnostics: 'ancestry:desktop:get-startup-diagnostics',
  getCapabilities: 'ancestry:desktop:get-capabilities',
  retrySidecar: 'ancestry:desktop:retry-sidecar',
  getPreferences: 'ancestry:desktop:get-preferences',
  updatePreferences: 'ancestry:desktop:update-preferences',
} as const)
