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

export const fileGrantPurposes = Object.freeze([
  'gedcom-read',
  'rootsmagic-read',
  'gedcom-write',
  'json-write',
  'markdown-write',
] as const)

export type FileGrantPurpose = typeof fileGrantPurposes[number]
export type FileReadPurpose = Extract<FileGrantPurpose, 'gedcom-read' | 'rootsmagic-read'>
export type FileWritePurpose = Exclude<FileGrantPurpose, FileReadPurpose>
export type FileGrantAccess = 'read' | 'write'
export type FileFormat = 'gedcom' | 'rootsmagic' | 'json' | 'markdown'
export type FileValidation = 'validated-input' | 'new-output' | 'replacement-confirmed'
export type FileGrantId = `grt_${string}`

export interface FileMetadata {
  displayName: string
  format: FileFormat
  sizeBytes: number
  validation: FileValidation
}

export interface FileGrantScope {
  originatingWindow: 'requesting-window'
  lifetime: 'app-session'
  redemption: 'single-use'
}

export interface FileGrant {
  grantId: FileGrantId
  purpose: FileGrantPurpose
  access: FileGrantAccess
  scope: Readonly<FileGrantScope>
  metadata: Readonly<FileMetadata>
}

export interface OpenFileGrantRequest { purpose: FileReadPurpose }
export interface SaveFileGrantRequest { purpose: FileWritePurpose; suggestedName: string }
export interface FileGrantRevocation { revoked: true }

/** Mirrors the transport-neutral Python application artifact contract. */
export interface ArtifactRef {
  artifact_id: string
  artifact_type: string
  media_type: string
  sha256: string
  size_bytes: number
  status: 'staged' | 'published'
}

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
  | 'FILE_SELECTION_INVALID'
  | 'FILE_TOO_LARGE'
  | 'FILE_GRANT_FORBIDDEN'
  | 'FILE_GRANT_REVOKED'
  | 'FILE_GRANT_STALE'
  | 'FILE_GRANT_CONFLICT'
  | 'FILE_DIALOG_FAILED'
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
  requestOpenFileGrant(request: OpenFileGrantRequest): Promise<BridgeResult<FileGrant | null>>
  requestSaveFileGrant(request: SaveFileGrantRequest): Promise<BridgeResult<FileGrant | null>>
  revokeFileGrant(grantId: FileGrantId): Promise<BridgeResult<FileGrantRevocation>>
}

export const desktopChannels = Object.freeze({
  getAppInfo: 'ancestry:desktop:get-app-info',
  getStartupDiagnostics: 'ancestry:desktop:get-startup-diagnostics',
  getCapabilities: 'ancestry:desktop:get-capabilities',
  retrySidecar: 'ancestry:desktop:retry-sidecar',
  getPreferences: 'ancestry:desktop:get-preferences',
  updatePreferences: 'ancestry:desktop:update-preferences',
  requestOpenFileGrant: 'ancestry:desktop:request-open-file-grant',
  requestSaveFileGrant: 'ancestry:desktop:request-save-file-grant',
  revokeFileGrant: 'ancestry:desktop:revoke-file-grant',
} as const)
