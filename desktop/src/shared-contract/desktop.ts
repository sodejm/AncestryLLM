export const DESKTOP_PROTOCOL_VERSION = '1' as const

export type DesktopColorScheme = 'system' | 'light' | 'dark'
export type DesktopBuildChannel = 'development' | 'packaged'
export type StartupState = 'starting' | 'ready' | 'degraded' | 'stopped'
export type StartupFailure = 'startup_failed' | 'startup_timeout' | 'incompatible_build' | 'crash_loop' | null

export const startupDiagnosticComponents = Object.freeze([
  'configuration',
  'sqlcipher',
  'keyring',
  'workspace',
] as const)

export type StartupDiagnosticComponentName = typeof startupDiagnosticComponents[number]
export type StartupDiagnosticComponentStatus = 'ready' | 'warning' | 'blocked'
export type StartupDiagnosticReportStatus = 'ready' | 'degraded'
export type StartupOperatingSystem = 'linux' | 'macos' | 'windows' | 'unsupported'
export type StartupArchitecture = 'x64' | 'arm64' | 'unsupported'

export interface StartupDiagnosticComponent {
  component: StartupDiagnosticComponentName
  status: StartupDiagnosticComponentStatus
  code: string
  message: string
  remediation: string | null
  restart_required: boolean
  blocks_mutations: boolean
}

export interface StartupDiagnosticReport {
  schema_version: 1
  status: StartupDiagnosticReportStatus
  platform: Readonly<{
    operating_system: StartupOperatingSystem
    architecture: StartupArchitecture
  }>
  components: readonly StartupDiagnosticComponent[]
}

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
  report: Readonly<StartupDiagnosticReport> | null
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

export const applicationSettingKeys = Object.freeze([
  'providers.default',
  'limits.max_query_rows',
  'limits.max_output_chars',
  'limits.query_timeout_seconds',
  'limits.provider_timeout_seconds',
] as const)

export type ApplicationSettingKey = typeof applicationSettingKeys[number]
export type ApplicationSettingValue = string | number
export type ApplicationSettingType = 'string' | 'integer' | 'number'

export interface ApplicationSettingValidation {
  allowed_values: readonly string[]
  minimum: number | null
  maximum: number | null
}

export interface ApplicationSetting {
  key: ApplicationSettingKey
  label: string
  help: string
  type: ApplicationSettingType
  value: ApplicationSettingValue
  default_value: ApplicationSettingValue
  validation: Readonly<ApplicationSettingValidation>
  restart_required: boolean
  sensitive: false
}

export interface ApplicationSettings {
  schema_version: 1
  revision: number
  fields: readonly ApplicationSetting[]
}

export type ApplicationSettingsPatch = Readonly<{
  schema_version: 1
  expected_revision: number
  changes: Readonly<Partial<Record<ApplicationSettingKey, ApplicationSettingValue>>>
}>

export const secretReferences = Object.freeze([
  'openai.api_key',
  'anthropic.api_key',
  'gemini.api_key',
  'openrouter.api_key',
  'openrouter.management_key',
  'database.master_key',
] as const)

export type SecretReference = typeof secretReferences[number]
export type SecretReferenceRequest = Readonly<{ reference: SecretReference }>
export type SecretSetRequest = Readonly<{ reference: SecretReference; value: string }>
export interface SecretStatus {
  reference: SecretReference
  status: 'present' | 'missing' | 'unavailable'
}

export const providerIds = Object.freeze([
  'ollama',
  'openai',
  'anthropic',
  'gemini',
  'openrouter',
] as const)

export type ProviderId = typeof providerIds[number]

export const providerDataClasses = Object.freeze([
  'public_genealogy',
  'deceased_person',
  'living_person',
  'possibly_living_person',
  'free_text_note',
  'source_transcription',
  'government_identifier',
] as const)

export type ProviderDataClass = typeof providerDataClasses[number]
export type ConsentWarningCode =
  | 'LIVING_PERSON_DATA_INCLUDED'
  | 'REMOTE_PROVIDER_SELECTED'
  | 'REMOTE_RETENTION_ENABLED'

export interface ProviderProfileSummary {
  name: string
  provider_id: ProviderId
  model: string
  endpoint: string
  endpoint_kind: 'loopback' | 'remote'
  secret_reference: SecretReference | null
  enabled: boolean
}

export interface ConsentGrantSummary {
  name: string
  provider_profile_name: string
  provider_id: ProviderId
  modules: readonly string[]
  purposes: readonly string[]
  data_classes: readonly ProviderDataClass[]
  models: readonly string[]
  max_cost_usd: number | null
  retain_payloads: boolean
  active: boolean
}

export interface ProviderConfiguration {
  schema_version: 1
  revision: string
  profiles: readonly ProviderProfileSummary[]
  consents: readonly ConsentGrantSummary[]
}

export type ProviderProfileCreateRequest = Readonly<{
  schema_version: 1
  expected_revision: string
  name: string
  provider_id: ProviderId
  model: string
  endpoint: string
  endpoint_identity_sha256: string
}>

export type ProviderEndpointValidationRequest = Readonly<{
  schema_version: 1
  provider_id: ProviderId
  endpoint: string
}>

export interface ProviderEndpointValidation {
  schema_version: 1
  status: 'reachable'
  endpoint_kind: 'loopback' | 'remote'
  http_status: number
  destination_digest: string
}

export type ConsentPreviewRequest = Readonly<{
  schema_version: 1
  provider_profile_name: string
  modules: readonly string[]
  purposes: readonly string[]
  data_classes: readonly ProviderDataClass[]
  models: readonly string[]
  max_cost_usd: number | null
  retain_payloads: boolean
}>

export interface ConsentPreview {
  schema_version: 1
  provider_profile_name: string
  provider_id: ProviderId
  modules: readonly string[]
  purposes: readonly string[]
  data_classes: readonly ProviderDataClass[]
  models: readonly string[]
  max_cost_usd: number | null
  retain_payloads: boolean
  warning_codes: readonly ConsentWarningCode[]
}

export type ConsentCreateRequest = Readonly<{
  schema_version: 1
  expected_revision: string
  name: string
  preview: Readonly<ConsentPreview>
}>

export type ConsentRevokeRequest = Readonly<{
  schema_version: 1
  expected_revision: string
  name: string
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

/** Maximum renderer delivery debt before the main process pauses the SSE source. */
export const CHAT_STREAM_MAX_UNACKNOWLEDGED_BYTES = 262_144

/** Target encoded batch size used to coalesce high-frequency token events. */
export const CHAT_STREAM_BATCH_MAX_BYTES = 4_096

/** Maximum batching delay before available token events are delivered. */
export const CHAT_STREAM_BATCH_WINDOW_MS = 16

export const chatEventTypes = Object.freeze([
  'active',
  'first-token',
  'delta',
  'cancelling',
  'completed',
  'interrupted',
  'failed',
] as const)

export type ChatEventType = typeof chatEventTypes[number]
export type ChatStreamState = 'active' | 'cancelling' | 'completed' | 'interrupted' | 'failed'

export interface ChatEventPayload {
  text: string | null
  code: string | null
  provider_id: string | null
  model: string | null
  remote: boolean | null
  message_count: number | null
}

/** One ordered, sanitized event from an owner-scoped transient provider run. */
export interface ChatEvent {
  schema_version: 1
  run_id: string
  sequence: number
  type: ChatEventType
  timestamp: string
  payload: Readonly<ChatEventPayload>
}

export type ChatStreamStartRequest = Readonly<{
  schema_version: 1
  session_id: string
  message: string
  max_output_tokens: number
  temperature: number
  timeout_seconds: number
  max_safe_retries: number
}>

export type ChatStreamCancelRequest = Readonly<{
  schema_version: 1
  session_id: string
  run_id: string
}>

export type ChatStreamAckRequest = Readonly<{
  schema_version: 1
  session_id: string
  run_id: string
  through_sequence: number
}>

export interface ChatStreamRun {
  schema_version: 1
  session_id: string
  run_id: string
  state: ChatStreamState
  latest_sequence: number
  terminal: boolean
}

export interface ChatStreamAcknowledgement {
  schema_version: 1
  session_id: string
  run_id: string
  through_sequence: number
  acknowledged: true
}

export type ChatEventDelivery =
  | Readonly<{
    schema_version: 1
    kind: 'batch'
    session_id: string
    run_id: string
    from_sequence: number
    through_sequence: number
    encoded_bytes: number
    events: readonly Readonly<ChatEvent>[]
    error: null
  }>
  | Readonly<{
    schema_version: 1
    kind: 'failure'
    session_id: string
    run_id: string
    from_sequence: null
    through_sequence: null
    encoded_bytes: 0
    events: null
    error: Readonly<BridgeError>
  }>

export const jobStates = Object.freeze([
  'queued',
  'running',
  'cancelling',
  'pending-safe-point',
  'completed',
  'failed',
  'cancelled',
] as const)

export type JobState = typeof jobStates[number]
export type JobEventKind = 'snapshot' | 'progress' | 'cancellation' | 'terminal'

export interface JobProgress {
  schema_version: 1
  operation: string
  timestamp: string
  completed: number | null
  total: number | null
}

/** Opaque artifact metadata. Redemption always requires a separate host grant. */
export interface JobArtifactRef {
  artifact_id: string
  media_type: string
  artifact_type: string
  size_bytes: number
  status: 'pending' | 'ready' | 'failed' | 'revoked'
  sha256: string | null
}

export interface JobSnapshot {
  schema_version: 1
  sequence: number
  job_id: string
  name: string
  state: JobState
  submitted_at: string
  started_at: string | null
  finished_at: string | null
  resource_refs: readonly string[]
  artifact: Readonly<JobArtifactRef> | null
  outcome_summary: string | null
  next_action: string | null
  error_code: string | null
  error_message: string | null
  error_remediation: string | null
  progress: Readonly<JobProgress> | null
  cancellation_requested_at: string | null
  cancellation_deferred_by: string | null
}

export interface JobEvent {
  schema_version: 1
  sequence: number
  kind: JobEventKind
  created_at: string
  snapshot: Readonly<JobSnapshot>
}

export interface JobList {
  schema_version: 1
  jobs: readonly Readonly<JobSnapshot>[]
}

export type JobRequest = Readonly<{
  schema_version: 1
  job_id: string
}>

export type JobEventSubscriptionRequest = Readonly<{
  schema_version: 1
  subscription_id: string
  job_id: string
  after: number
}>

export type JobEventUnsubscriptionRequest = Readonly<{
  schema_version: 1
  subscription_id: string
}>

export interface JobEventSubscription {
  schema_version: 1
  subscription_id: string
  job_id: string
  subscribed: true
}

export interface JobEventUnsubscription {
  schema_version: 1
  subscription_id: string
  unsubscribed: true
}

export type JobEventDelivery =
  | Readonly<{
    schema_version: 1
    kind: 'event'
    subscription_id: string
    job_id: string
    event: Readonly<JobEvent>
    error: null
  }>
  | Readonly<{
    schema_version: 1
    kind: 'failure'
    subscription_id: string
    job_id: string
    event: null
    error: Readonly<BridgeError>
  }>

export const localRuntimeOperations = Object.freeze([
  'setup',
  'start',
  'stop',
  'repair',
  'uninstall-preserve',
  'uninstall-delete',
] as const)

export type LocalRuntimeOperation = typeof localRuntimeOperations[number]
export type LocalRuntimeState = 'not-installed' | 'stopped' | 'ready' | 'unhealthy'

export interface LocalRuntimeRequest {
  schema_version: 1
  operation: LocalRuntimeOperation
  offline: boolean
}

export interface LocalRuntimeApplyRequest extends LocalRuntimeRequest {
  plan_revision: string
  confirmation: string
}

export interface LocalRuntimeStatus {
  schema_version: 1
  state: LocalRuntimeState
  code: string
  supported: boolean
  host: Readonly<{
    operating_system: 'macos' | 'unsupported'
    architecture: string
    macos_major: number
    virtualization: 'available' | 'unavailable'
    free_space: 'sufficient' | 'insufficient'
    existing_docker_contexts: number
  }>
  allocation: Readonly<{
    cpus: number
    memory_gib: number
    disk_gib: number
  }>
  components: readonly Readonly<{
    name: string
    version: string
    installed: boolean
  }>[]
  vm_image: Readonly<{
    version: string
    installed: boolean
  }>
}

export interface LocalRuntimeArtifactReview {
  name: string
  version: string
  repository: string
  asset_name: string
  source_url: string
  sha256: string
  size_bytes: number
  license: 'Apache-2.0' | 'MIT'
  license_url: string
  license_sha256: string
}

export interface LocalRuntimeReview {
  artifacts: readonly Readonly<LocalRuntimeArtifactReview>[]
  vm_image: Readonly<{
    version: string
    repository: 'abiosoft/colima-core'
    asset_name: 'ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz'
    source_url: string
    sha256: string
    size_bytes: number
  }>
  ownership: Readonly<{
    profile: 'ancestryllm-local-arm64'
    context: 'colima-ancestryllm-local-arm64'
  }>
  isolation: Readonly<{
    loopback_only: true
    kubernetes: false
    privileged_containers: false
    renderer_socket_access: false
    container_socket_access: false
    cross_profile_socket_access: false
  }>
}

export interface LocalRuntimePreview {
  schema_version: 1
  operation: LocalRuntimeOperation
  offline: boolean
  actions: readonly Readonly<{ code: string }>[]
  confirmation_phrase: string
  preserves_data: boolean
  deletes_data: boolean
  plan_revision: string
  status: Readonly<LocalRuntimeStatus>
  review: Readonly<LocalRuntimeReview>
}

export interface LocalRuntimeResult {
  schema_version: 1
  operation: LocalRuntimeOperation
  state: LocalRuntimeState
  code: string
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
  | 'STARTUP_MUTATION_BLOCKED'
  | 'PREFERENCES_UNAVAILABLE'
  | 'PREFERENCES_CONFLICT'
  | 'SETTINGS_UNAVAILABLE'
  | 'SETTINGS_CONFLICT'
  | 'SETTINGS_INVALID'
  | 'SECRET_STORE_UNAVAILABLE'
  | 'SECRET_ENVIRONMENT_MANAGED'
  | 'SECRET_INVALID'
  | 'PROVIDER_CONFIGURATION_UNAVAILABLE'
  | 'PROVIDER_CONFIGURATION_CONFLICT'
  | 'PROVIDER_CONFIGURATION_INVALID'
  | 'ENDPOINT_REJECTED'
  | 'CONSENT_INVALID'
  | 'CONSENT_PREVIEW_STALE'
  | 'FILE_SELECTION_INVALID'
  | 'FILE_TOO_LARGE'
  | 'FILE_GRANT_FORBIDDEN'
  | 'FILE_GRANT_REVOKED'
  | 'FILE_GRANT_STALE'
  | 'FILE_GRANT_CONFLICT'
  | 'FILE_DIALOG_FAILED'
  | 'RUNTIME_POLICY_INVALID'
  | 'RUNTIME_POLICY_SCHEMA_UNSUPPORTED'
  | 'RUNTIME_REQUEST_INVALID'
  | 'RUNTIME_HOST_UNSUPPORTED'
  | 'RUNTIME_PLAN_STALE'
  | 'RUNTIME_CONFIRMATION_REQUIRED'
  | 'RUNTIME_OFFLINE_UNAVAILABLE'
  | 'RUNTIME_DOWNLOAD_FAILED'
  | 'RUNTIME_ARTIFACT_INTEGRITY'
  | 'RUNTIME_COMPONENT_INTEGRITY'
  | 'RUNTIME_STORAGE_UNSAFE'
  | 'RUNTIME_NOT_INSTALLED'
  | 'RUNTIME_OWNERSHIP_INVALID'
  | 'RUNTIME_PROCESS_FAILED'
  | 'RUNTIME_HEALTH_FAILED'
  | 'JOB_ID_INVALID'
  | 'JOB_NOT_FOUND'
  | 'JOB_EVENT_CURSOR_INVALID'
  | 'JOB_EVENT_REPLAY_EXPIRED'
  | 'JOB_SERVICE_UNAVAILABLE'
  | 'JOB_SUBSCRIBER_LIMIT'
  | 'JOB_SUBSCRIPTION_CLOSED'
  | 'JOB_SUBSCRIPTION_CONFLICT'
  | 'JOB_EVENT_STREAM_FAILED'
  | 'CHAT_SESSION_NOT_FOUND'
  | 'CHAT_STREAM_NOT_FOUND'
  | 'CHAT_STREAM_CURSOR_INVALID'
  | 'CHAT_STREAM_REPLAY_EXPIRED'
  | 'CHAT_STREAM_SERVICE_UNAVAILABLE'
  | 'CHAT_STREAM_LIMIT'
  | 'CHAT_STREAM_BACKPRESSURE_TIMEOUT'
  | 'CHAT_STREAM_STALLED'
  | 'CHAT_STREAM_EVENT_INVALID'
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
  getSettings(): Promise<BridgeResult<ApplicationSettings>>
  updateSettings(update: ApplicationSettingsPatch): Promise<BridgeResult<ApplicationSettings>>
  getSecretStatus(request: SecretReferenceRequest): Promise<BridgeResult<SecretStatus>>
  setSecret(request: SecretSetRequest): Promise<BridgeResult<SecretStatus>>
  deleteSecret(request: SecretReferenceRequest): Promise<BridgeResult<SecretStatus>>
  getProviderConfiguration(): Promise<BridgeResult<ProviderConfiguration>>
  createProviderProfile(request: ProviderProfileCreateRequest): Promise<BridgeResult<ProviderConfiguration>>
  validateProviderEndpoint(request: ProviderEndpointValidationRequest): Promise<BridgeResult<ProviderEndpointValidation>>
  previewConsent(request: ConsentPreviewRequest): Promise<BridgeResult<ConsentPreview>>
  createConsent(request: ConsentCreateRequest): Promise<BridgeResult<ProviderConfiguration>>
  revokeConsent(request: ConsentRevokeRequest): Promise<BridgeResult<ProviderConfiguration>>
  requestOpenFileGrant(request: OpenFileGrantRequest): Promise<BridgeResult<FileGrant | null>>
  requestSaveFileGrant(request: SaveFileGrantRequest): Promise<BridgeResult<FileGrant | null>>
  revokeFileGrant(grantId: FileGrantId): Promise<BridgeResult<FileGrantRevocation>>
  getLocalRuntimeStatus(): Promise<BridgeResult<LocalRuntimeStatus>>
  previewLocalRuntime(request: LocalRuntimeRequest): Promise<BridgeResult<LocalRuntimePreview>>
  applyLocalRuntime(request: LocalRuntimeApplyRequest): Promise<BridgeResult<LocalRuntimeResult>>
  startChatStream(request: ChatStreamStartRequest): Promise<BridgeResult<ChatStreamRun>>
  cancelChatStream(request: ChatStreamCancelRequest): Promise<BridgeResult<ChatStreamRun>>
  acknowledgeChatStream(request: ChatStreamAckRequest): Promise<BridgeResult<ChatStreamAcknowledgement>>
  onChatEventBatch(listener: (delivery: Readonly<ChatEventDelivery>) => void): () => void
  listJobs(): Promise<BridgeResult<JobList>>
  getJob(request: JobRequest): Promise<BridgeResult<JobSnapshot>>
  cancelJob(request: JobRequest): Promise<BridgeResult<JobSnapshot>>
  subscribeJobEvents(request: JobEventSubscriptionRequest): Promise<BridgeResult<JobEventSubscription>>
  unsubscribeJobEvents(request: JobEventUnsubscriptionRequest): Promise<BridgeResult<JobEventUnsubscription>>
  onJobEvent(listener: (delivery: Readonly<JobEventDelivery>) => void): () => void
}

export const desktopChannels = Object.freeze({
  getAppInfo: 'ancestry:desktop:get-app-info',
  getStartupDiagnostics: 'ancestry:desktop:get-startup-diagnostics',
  getCapabilities: 'ancestry:desktop:get-capabilities',
  retrySidecar: 'ancestry:desktop:retry-sidecar',
  getPreferences: 'ancestry:desktop:get-preferences',
  updatePreferences: 'ancestry:desktop:update-preferences',
  getSettings: 'ancestry:desktop:get-settings',
  updateSettings: 'ancestry:desktop:update-settings',
  getSecretStatus: 'ancestry:desktop:get-secret-status',
  setSecret: 'ancestry:desktop:set-secret',
  deleteSecret: 'ancestry:desktop:delete-secret',
  getProviderConfiguration: 'ancestry:desktop:get-provider-configuration',
  createProviderProfile: 'ancestry:desktop:create-provider-profile',
  validateProviderEndpoint: 'ancestry:desktop:validate-provider-endpoint',
  previewConsent: 'ancestry:desktop:preview-consent',
  createConsent: 'ancestry:desktop:create-consent',
  revokeConsent: 'ancestry:desktop:revoke-consent',
  requestOpenFileGrant: 'ancestry:desktop:request-open-file-grant',
  requestSaveFileGrant: 'ancestry:desktop:request-save-file-grant',
  revokeFileGrant: 'ancestry:desktop:revoke-file-grant',
  getLocalRuntimeStatus: 'ancestry:desktop:get-local-runtime-status',
  previewLocalRuntime: 'ancestry:desktop:preview-local-runtime',
  applyLocalRuntime: 'ancestry:desktop:apply-local-runtime',
  startChatStream: 'ancestry:desktop:start-chat-stream',
  cancelChatStream: 'ancestry:desktop:cancel-chat-stream',
  acknowledgeChatStream: 'ancestry:desktop:acknowledge-chat-stream',
  listJobs: 'ancestry:desktop:list-jobs',
  getJob: 'ancestry:desktop:get-job',
  cancelJob: 'ancestry:desktop:cancel-job',
  subscribeJobEvents: 'ancestry:desktop:subscribe-job-events',
  unsubscribeJobEvents: 'ancestry:desktop:unsubscribe-job-events',
} as const)

export const desktopEventChannels = Object.freeze({
  chatEventBatch: 'ancestry:desktop:chat-event-batch',
  jobEvent: 'ancestry:desktop:job-event',
} as const)
