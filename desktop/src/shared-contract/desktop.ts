/** Defines the versioned, serializable contract shared by all desktop processes. */
/**
 * Identifies the cross-process protocol compatibility revision required by both sides of the bridge.
 */
export const DESKTOP_PROTOCOL_VERSION = '1' as const

/**
 * Constrains desktop color scheme values exchanged for the versioned desktop IPC protocol.
 */
export type DesktopColorScheme = 'system' | 'light' | 'dark'
/**
 * Maps the versioned desktop IPC protocol operations to stable Electron IPC channel names.
 */
export type DesktopBuildChannel = 'development' | 'packaged'
/**
 * Constrains startup state values exchanged for desktop startup readiness and diagnostics.
 */
export type StartupState = 'starting' | 'ready' | 'degraded' | 'stopped'
/**
 * Constrains the stable startup failure taxonomy exposed for desktop startup readiness and diagnostics.
 */
export type StartupFailure = 'startup_failed' | 'startup_timeout' | 'incompatible_build' | 'crash_loop' | null

/**
 * Lists the allowlisted startup diagnostic components accepted by desktop startup readiness and diagnostics.
 */
export const startupDiagnosticComponents = Object.freeze([
  'configuration',
  'sqlcipher',
  'keyring',
  'workspace',
] as const)

/**
 * Defines the serializable startup diagnostic component name snapshot exposed for desktop startup readiness and diagnostics.
 */
export type StartupDiagnosticComponentName = typeof startupDiagnosticComponents[number]
/**
 * Defines the serializable startup diagnostic component status snapshot exposed for desktop startup readiness and diagnostics.
 */
export type StartupDiagnosticComponentStatus = 'ready' | 'warning' | 'blocked'
/**
 * Defines the serializable startup diagnostic report status snapshot exposed for desktop startup readiness and diagnostics.
 */
export type StartupDiagnosticReportStatus = 'ready' | 'degraded'
/**
 * Constrains startup operating system values exchanged for desktop startup readiness and diagnostics.
 */
export type StartupOperatingSystem = 'linux' | 'macos' | 'windows' | 'unsupported'
/**
 * Constrains startup architecture values exchanged for desktop startup readiness and diagnostics.
 */
export type StartupArchitecture = 'x64' | 'arm64' | 'unsupported'

/**
 * Defines the serializable startup diagnostic component snapshot exposed for desktop startup readiness and diagnostics.
 */
export interface StartupDiagnosticComponent {
  component: StartupDiagnosticComponentName
  status: StartupDiagnosticComponentStatus
  code: string
  message: string
  remediation: string | null
  restart_required: boolean
  blocks_mutations: boolean
}

/**
 * Defines the serializable startup diagnostic report snapshot exposed for desktop startup readiness and diagnostics.
 */
export interface StartupDiagnosticReport {
  schema_version: 1
  status: StartupDiagnosticReportStatus
  platform: Readonly<{
    operating_system: StartupOperatingSystem
    architecture: StartupArchitecture
  }>
  components: readonly StartupDiagnosticComponent[]
}

/**
 * Constrains app info values exchanged for the versioned desktop IPC protocol.
 */
export interface AppInfo {
  applicationName: 'AncestryLLM'
  appVersion: string
  buildChannel: DesktopBuildChannel
}

/**
 * Defines the serializable startup diagnostics snapshot exposed for desktop startup readiness and diagnostics.
 */
export interface StartupDiagnostics {
  state: StartupState
  failure: StartupFailure
  automaticRestartsRemaining: number
  manualRetriesRemaining: number
  report: Readonly<StartupDiagnosticReport> | null
}

/**
 * Constrains capability action values exchanged for advertised application capabilities.
 */
export interface CapabilityAction {
  dispatch_key: string
  name: string
  summary: string
}

/**
 * Constrains capability module values exchanged for advertised application capabilities.
 */
export interface CapabilityModule {
  module_id: string
  name: string
  summary: string
  actions: readonly CapabilityAction[]
}

/**
 * Defines the serializable capability manifest snapshot exposed for advertised application capabilities.
 */
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

/**
 * Lists the allowlisted local preferences accepted by local desktop preferences.
 */
export interface LocalPreferences {
  colorScheme: DesktopColorScheme
  reducedMotion: boolean
  onboardingCompleted: boolean
  schemaVersion: 1
  revision: number
}

/**
 * Constrains preference update values exchanged for local desktop preferences.
 */
export type PreferenceUpdate = Readonly<{
  expectedRevision: number
  colorScheme?: DesktopColorScheme
  reducedMotion?: boolean
  onboardingCompleted?: boolean
}>

/**
 * Lists the allowlisted application setting keys accepted by validated application settings.
 */
export const applicationSettingKeys = Object.freeze([
  'providers.default',
  'limits.max_query_rows',
  'limits.max_output_chars',
  'limits.query_timeout_seconds',
  'limits.provider_timeout_seconds',
] as const)

/**
 * Constrains application setting key values exchanged for validated application settings.
 */
export type ApplicationSettingKey = typeof applicationSettingKeys[number]
/**
 * Constrains application setting value values exchanged for validated application settings.
 */
export type ApplicationSettingValue = string | number
/**
 * Constrains application setting type values exchanged for validated application settings.
 */
export type ApplicationSettingType = 'string' | 'integer' | 'number'

/**
 * Constrains application setting validation values exchanged for validated application settings.
 */
export interface ApplicationSettingValidation {
  allowed_values: readonly string[]
  minimum: number | null
  maximum: number | null
}

/**
 * Constrains application setting values exchanged for validated application settings.
 */
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

/**
 * Defines the serializable application settings snapshot exposed for validated application settings.
 */
export interface ApplicationSettings {
  schema_version: 1
  revision: number
  fields: readonly ApplicationSetting[]
}

/**
 * Defines the serializable application settings patch snapshot exposed for validated application settings.
 */
export type ApplicationSettingsPatch = Readonly<{
  schema_version: 1
  expected_revision: number
  changes: Readonly<Partial<Record<ApplicationSettingKey, ApplicationSettingValue>>>
}>

/**
 * Lists the allowlisted secret references accepted by keyring-backed secret references without exposing secret values.
 */
export const secretReferences = Object.freeze([
  'openai.api_key',
  'anthropic.api_key',
  'gemini.api_key',
  'openrouter.api_key',
  'openrouter.management_key',
  'database.master_key',
] as const)

/**
 * Constrains secret reference values exchanged for keyring-backed secret references without exposing secret values.
 */
export type SecretReference = typeof secretReferences[number]
/**
 * Defines the validated secret reference request payload accepted for keyring-backed secret references without exposing secret values.
 */
export type SecretReferenceRequest = Readonly<{ reference: SecretReference }>
/**
 * Defines the validated secret set request payload accepted for keyring-backed secret references without exposing secret values.
 */
export type SecretSetRequest = Readonly<{ reference: SecretReference; value: string }>
/**
 * Defines the serializable secret status snapshot exposed for keyring-backed secret references without exposing secret values.
 */
export interface SecretStatus {
  reference: SecretReference
  status: 'present' | 'missing' | 'unavailable'
}

/**
 * Lists the allowlisted provider ids accepted by provider configuration and explicit cloud selection.
 */
export const providerIds = Object.freeze([
  'ollama',
  'openai',
  'anthropic',
  'gemini',
  'openrouter',
] as const)

/**
 * Defines an opaque provider id that may cross the bridge without exposing host paths.
 */
export type ProviderId = typeof providerIds[number]

/**
 * Constrains provider data classes values exchanged for provider configuration and explicit cloud selection.
 */
export const providerDataClasses = Object.freeze([
  'public_genealogy',
  'deceased_person',
  'living_person',
  'possibly_living_person',
  'free_text_note',
  'source_transcription',
  'government_identifier',
] as const)

/**
 * Constrains provider data class values exchanged for provider configuration and explicit cloud selection.
 */
export type ProviderDataClass = typeof providerDataClasses[number]

/**
 * Lists the allowlisted chat purposes accepted by flow-controlled chat sessions and events.
 */
export const chatPurposes = Object.freeze([
  'genealogy_analysis',
  'source_analysis',
  'writing_assistance',
] as const)

/**
 * Constrains chat purpose values exchanged for flow-controlled chat sessions and events.
 */
export type ChatPurpose = typeof chatPurposes[number]
/**
 * Constrains the stable consent warning code taxonomy exposed for explicit cloud-data consent.
 */
export type ConsentWarningCode =
  | 'LIVING_PERSON_DATA_INCLUDED'
  | 'REMOTE_PROVIDER_SELECTED'
  | 'REMOTE_RETENTION_ENABLED'

/**
 * Constrains provider profile summary values exchanged for provider configuration and explicit cloud selection.
 */
export interface ProviderProfileSummary {
  name: string
  provider_id: ProviderId
  model: string
  endpoint: string
  endpoint_kind: 'loopback' | 'remote'
  secret_reference: SecretReference | null
  enabled: boolean
}

/**
 * Constrains consent grant summary values exchanged for explicit cloud-data consent.
 */
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

/**
 * Defines the serializable provider configuration snapshot exposed for provider configuration and explicit cloud selection.
 */
export interface ProviderConfiguration {
  schema_version: 1
  revision: string
  profiles: readonly ProviderProfileSummary[]
  consents: readonly ConsentGrantSummary[]
}

/**
 * Defines the validated provider profile create request payload accepted for provider configuration and explicit cloud selection.
 */
export type ProviderProfileCreateRequest = Readonly<{
  schema_version: 1
  expected_revision: string
  name: string
  provider_id: ProviderId
  model: string
  endpoint: string
  endpoint_identity_sha256: string
}>

/**
 * Defines the validated provider endpoint validation request payload accepted for provider configuration and explicit cloud selection.
 */
export type ProviderEndpointValidationRequest = Readonly<{
  schema_version: 1
  provider_id: ProviderId
  endpoint: string
}>

/**
 * Constrains provider endpoint validation values exchanged for provider configuration and explicit cloud selection.
 */
export interface ProviderEndpointValidation {
  schema_version: 1
  status: 'reachable'
  endpoint_kind: 'loopback' | 'remote'
  http_status: number
  destination_digest: string
}

/**
 * Defines the validated consent preview request payload accepted for explicit cloud-data consent.
 */
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

/**
 * Defines the serializable consent preview snapshot exposed for explicit cloud-data consent.
 */
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

/**
 * Defines the validated consent create request payload accepted for explicit cloud-data consent.
 */
export type ConsentCreateRequest = Readonly<{
  schema_version: 1
  expected_revision: string
  name: string
  preview: Readonly<ConsentPreview>
}>

/**
 * Defines the validated consent revoke request payload accepted for explicit cloud-data consent.
 */
export type ConsentRevokeRequest = Readonly<{
  schema_version: 1
  expected_revision: string
  name: string
}>

/**
 * Lists the allowlisted file grant purposes accepted by capability-scoped local file access.
 */
export const fileGrantPurposes = Object.freeze([
  'gedcom-read',
  'rootsmagic-read',
  'gedcom-write',
  'json-write',
  'markdown-write',
] as const)

/**
 * Constrains file grant purpose values exchanged for capability-scoped local file access.
 */
export type FileGrantPurpose = typeof fileGrantPurposes[number]
/**
 * Constrains file read purpose values exchanged for validated local file metadata and formats.
 */
export type FileReadPurpose = Extract<FileGrantPurpose, 'gedcom-read' | 'rootsmagic-read'>
/**
 * Constrains file write purpose values exchanged for validated local file metadata and formats.
 */
export type FileWritePurpose = Exclude<FileGrantPurpose, FileReadPurpose>
/**
 * Constrains file grant access values exchanged for capability-scoped local file access.
 */
export type FileGrantAccess = 'read' | 'write'
/**
 * Constrains file format values exchanged for validated local file metadata and formats.
 */
export type FileFormat = 'gedcom' | 'rootsmagic' | 'json' | 'markdown'
/**
 * Constrains file validation values exchanged for validated local file metadata and formats.
 */
export type FileValidation = 'validated-input' | 'new-output' | 'replacement-confirmed'
/**
 * Defines an opaque file grant id that may cross the bridge without exposing host paths.
 */
export type FileGrantId = `grt_${string}`

/**
 * Constrains file metadata values exchanged for validated local file metadata and formats.
 */
export interface FileMetadata {
  displayName: string
  format: FileFormat
  sizeBytes: number
  validation: FileValidation
}

/**
 * Constrains file grant scope values exchanged for capability-scoped local file access.
 */
export interface FileGrantScope {
  originatingWindow: 'requesting-window'
  lifetime: 'app-session'
  redemption: 'single-use'
}

/**
 * Constrains file grant values exchanged for capability-scoped local file access.
 */
export interface FileGrant {
  grantId: FileGrantId
  purpose: FileGrantPurpose
  access: FileGrantAccess
  scope: Readonly<FileGrantScope>
  metadata: Readonly<FileMetadata>
}

/**
 * Defines the validated open file grant request payload accepted for capability-scoped local file access.
 */
export interface OpenFileGrantRequest { purpose: FileReadPurpose }
/**
 * Defines the validated save file grant request payload accepted for capability-scoped local file access.
 */
export interface SaveFileGrantRequest { purpose: FileWritePurpose; suggestedName: string }
/**
 * Constrains file grant revocation values exchanged for capability-scoped local file access.
 */
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

/** Fail-closed capabilities exposed by the transient chat service. */
export interface ChatCapability {
  schema_version: 1
  max_active_sessions: 32
  max_messages: 32
  max_message_characters: 16_384
  max_context_characters: 65_536
  max_output_tokens: 4_096
  max_temperature: 1
  max_timeout_seconds: 120
  max_safe_retries: 1
  transient: true
  tools_enabled: false
  payload_retention: false
  output_is_evidence: false
  streaming: true
  stream_replay_max_bytes: 262_144
}

/**
 * Defines the validated chat session create request payload accepted for flow-controlled chat sessions and events.
 */
export type ChatSessionCreateRequest = Readonly<{
  schema_version: 1
  provider_profile_name: string
  model: string
  purpose: ChatPurpose
  data_classes: readonly ProviderDataClass[]
  consent_name: string | null
}>

/**
 * Defines the validated chat session request payload accepted for flow-controlled chat sessions and events.
 */
export type ChatSessionRequest = Readonly<{
  schema_version: 1
  session_id: string
}>

/**
 * Constrains chat session values exchanged for flow-controlled chat sessions and events.
 */
export interface ChatSession {
  schema_version: 1
  session_id: string
  provider_profile_name: string
  provider_id: ProviderId
  model: string
  purpose: ChatPurpose
  data_classes: readonly ProviderDataClass[]
  remote: boolean
  consent_name: string | null
  message_count: number
  transient: true
  payload_retention: false
}

/**
 * Constrains chat session closure values exchanged for flow-controlled chat sessions and events.
 */
export interface ChatSessionClosure {
  schema_version: 1
  session_id: string
  closed: true
}

/**
 * Lists the allowlisted chat event types accepted by flow-controlled chat sessions and events.
 */
export const chatEventTypes = Object.freeze([
  'active',
  'first-token',
  'delta',
  'cancelling',
  'completed',
  'interrupted',
  'failed',
] as const)

/**
 * Constrains chat event type values exchanged for flow-controlled chat sessions and events.
 */
export type ChatEventType = typeof chatEventTypes[number]
/**
 * Constrains chat stream state values exchanged for flow-controlled chat sessions and events.
 */
export type ChatStreamState = 'active' | 'cancelling' | 'completed' | 'interrupted' | 'failed'

/**
 * Constrains chat event payload values exchanged for flow-controlled chat sessions and events.
 */
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

/**
 * Defines the validated chat stream start request payload accepted for flow-controlled chat sessions and events.
 */
export type ChatStreamStartRequest = Readonly<{
  schema_version: 1
  session_id: string
  message: string
  max_output_tokens: number
  temperature: number
  timeout_seconds: number
  max_safe_retries: number
}>

/**
 * Defines the validated chat stream cancel request payload accepted for flow-controlled chat sessions and events.
 */
export type ChatStreamCancelRequest = Readonly<{
  schema_version: 1
  session_id: string
  run_id: string
}>

/**
 * Defines the validated chat stream ack request payload accepted for flow-controlled chat sessions and events.
 */
export type ChatStreamAckRequest = Readonly<{
  schema_version: 1
  session_id: string
  run_id: string
  through_sequence: number
}>

/**
 * Constrains chat stream run values exchanged for flow-controlled chat sessions and events.
 */
export interface ChatStreamRun {
  schema_version: 1
  session_id: string
  run_id: string
  state: ChatStreamState
  latest_sequence: number
  terminal: boolean
}

/**
 * Defines an ordered chat stream acknowledgement message used for flow-controlled chat sessions and events.
 */
export interface ChatStreamAcknowledgement {
  schema_version: 1
  session_id: string
  run_id: string
  through_sequence: number
  acknowledged: true
}

/**
 * Defines an ordered chat event delivery message used for flow-controlled chat sessions and events.
 */
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

/**
 * Lists the allowlisted job states accepted by long-running job progress and cancellation.
 */
export const jobStates = Object.freeze([
  'queued',
  'running',
  'cancelling',
  'pending-safe-point',
  'completed',
  'failed',
  'cancelled',
] as const)

/**
 * Constrains job state values exchanged for long-running job progress and cancellation.
 */
export type JobState = typeof jobStates[number]
/**
 * Constrains job event kind values exchanged for long-running job progress and cancellation.
 */
export type JobEventKind = 'snapshot' | 'progress' | 'cancellation' | 'terminal'

/**
 * Constrains job progress values exchanged for long-running job progress and cancellation.
 */
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

/**
 * Constrains job snapshot values exchanged for long-running job progress and cancellation.
 */
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

/**
 * Defines an ordered job event message used for long-running job progress and cancellation.
 */
export interface JobEvent {
  schema_version: 1
  sequence: number
  kind: JobEventKind
  created_at: string
  snapshot: Readonly<JobSnapshot>
}

/**
 * Constrains job list values exchanged for long-running job progress and cancellation.
 */
export interface JobList {
  schema_version: 1
  jobs: readonly Readonly<JobSnapshot>[]
}

/**
 * Defines the validated job request payload accepted for long-running job progress and cancellation.
 */
export type JobRequest = Readonly<{
  schema_version: 1
  job_id: string
}>

/**
 * Defines the validated job event subscription request payload accepted for long-running job progress and cancellation.
 */
export type JobEventSubscriptionRequest = Readonly<{
  schema_version: 1
  subscription_id: string
  job_id: string
  after: number
}>

/**
 * Defines the validated job event unsubscription request payload accepted for long-running job progress and cancellation.
 */
export type JobEventUnsubscriptionRequest = Readonly<{
  schema_version: 1
  subscription_id: string
}>

/**
 * Constrains job event subscription values exchanged for long-running job progress and cancellation.
 */
export interface JobEventSubscription {
  schema_version: 1
  subscription_id: string
  job_id: string
  subscribed: true
}

/**
 * Constrains job event unsubscription values exchanged for long-running job progress and cancellation.
 */
export interface JobEventUnsubscription {
  schema_version: 1
  subscription_id: string
  unsubscribed: true
}

/**
 * Defines an ordered job event delivery message used for long-running job progress and cancellation.
 */
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

/**
 * Lists the allowlisted local runtime operations accepted by reviewed local runtime lifecycle operations.
 */
export const localRuntimeOperations = Object.freeze([
  'setup',
  'start',
  'stop',
  'repair',
  'uninstall-preserve',
  'uninstall-delete',
] as const)

/**
 * Constrains local runtime operation values exchanged for reviewed local runtime lifecycle operations.
 */
export type LocalRuntimeOperation = typeof localRuntimeOperations[number]
/**
 * Constrains local runtime state values exchanged for reviewed local runtime lifecycle operations.
 */
export type LocalRuntimeState = 'not-installed' | 'stopped' | 'ready' | 'unhealthy'

/**
 * Defines the validated local runtime request payload accepted for reviewed local runtime lifecycle operations.
 */
export interface LocalRuntimeRequest {
  schema_version: 1
  operation: LocalRuntimeOperation
  offline: boolean
}

/**
 * Defines the validated local runtime apply request payload accepted for reviewed local runtime lifecycle operations.
 */
export interface LocalRuntimeApplyRequest extends LocalRuntimeRequest {
  plan_revision: string
  confirmation: string
}

/**
 * Defines the serializable local runtime status snapshot exposed for reviewed local runtime lifecycle operations.
 */
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

/**
 * Defines the serializable local runtime artifact review snapshot exposed for reviewed local runtime lifecycle operations.
 */
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

/**
 * Defines the serializable local runtime review snapshot exposed for reviewed local runtime lifecycle operations.
 */
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

/**
 * Defines the serializable local runtime preview snapshot exposed for reviewed local runtime lifecycle operations.
 */
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

/**
 * Defines the serializable local runtime result returned after reviewed local runtime lifecycle operations.
 */
export interface LocalRuntimeResult {
  schema_version: 1
  operation: LocalRuntimeOperation
  state: LocalRuntimeState
  code: string
}

/**
 * Defines the validated open external link request payload accepted for user-confirmed external navigation.
 */
export type OpenExternalLinkRequest = Readonly<{
  schema_version: 1
  destination: string
}>

/**
 * Defines the serializable open external link result returned after user-confirmed external navigation.
 */
export interface OpenExternalLinkResult {
  schema_version: 1
  destination: string
  status: 'opened' | 'cancelled'
}

/**
 * Defines the validated copy text request payload accepted for explicit clipboard writes.
 */
export type CopyTextRequest = Readonly<{
  schema_version: 1
  text: string
}>

/**
 * Defines the serializable copy text result returned after explicit clipboard writes.
 */
export interface CopyTextResult {
  schema_version: 1
  copied: true
}

/** Result returned after opening the one main-process-owned diagnostics directory. */
export interface OpenDiagnosticsDirectoryResult {
  schema_version: 1
  opened: true
}

/** Result returned after clearing the bounded component diagnostic logs. */
export interface ClearDiagnosticsResult {
  schema_version: 1
  cleared: true
}

/**
 * Defines the callable bridge error code surface exposed by the isolated preload boundary.
 */
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
  | 'CHAT_SESSION_INVALID'
  | 'CHAT_SESSION_NOT_FOUND'
  | 'CHAT_SESSION_LIMIT'
  | 'CHAT_SESSION_BUSY'
  | 'CHAT_SESSION_SERVICE_UNAVAILABLE'
  | 'CHAT_STREAM_NOT_FOUND'
  | 'CHAT_STREAM_CURSOR_INVALID'
  | 'CHAT_STREAM_REPLAY_EXPIRED'
  | 'CHAT_STREAM_SERVICE_UNAVAILABLE'
  | 'CHAT_STREAM_LIMIT'
  | 'CHAT_STREAM_BACKPRESSURE_TIMEOUT'
  | 'CHAT_STREAM_STALLED'
  | 'CHAT_STREAM_EVENT_INVALID'
  | 'INTERNAL_ERROR'

/**
 * Constrains the stable bridge error taxonomy exposed for the versioned preload bridge contract.
 */
export interface BridgeError {
  code: BridgeErrorCode
  message: string
  remediation: string
}

/**
 * Defines the serializable bridge result returned after the versioned preload bridge contract.
 */
export type BridgeResult<T> =
  | Readonly<{ ok: true; protocolVersion: typeof DESKTOP_PROTOCOL_VERSION; data: Readonly<T> }>
  | Readonly<{ ok: false; protocolVersion: typeof DESKTOP_PROTOCOL_VERSION; error: Readonly<BridgeError> }>

/**
 * Defines the callable ancestry bridge surface exposed by the isolated preload boundary.
 */
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
  openExternalLink(request: OpenExternalLinkRequest): Promise<BridgeResult<OpenExternalLinkResult>>
  copyText(request: CopyTextRequest): Promise<BridgeResult<CopyTextResult>>
  openDiagnosticsDirectory(): Promise<BridgeResult<OpenDiagnosticsDirectoryResult>>
  clearDiagnostics(): Promise<BridgeResult<ClearDiagnosticsResult>>
  getChatCapability(): Promise<BridgeResult<ChatCapability>>
  createChatSession(request: ChatSessionCreateRequest): Promise<BridgeResult<ChatSession>>
  closeChatSession(request: ChatSessionRequest): Promise<BridgeResult<ChatSessionClosure>>
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

/**
 * Maps the versioned desktop IPC protocol operations to stable Electron IPC channel names.
 */
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
  openExternalLink: 'ancestry:desktop:open-external-link',
  copyText: 'ancestry:desktop:copy-text',
  openDiagnosticsDirectory: 'ancestry:desktop:open-diagnostics-directory',
  clearDiagnostics: 'ancestry:desktop:clear-diagnostics',
  getChatCapability: 'ancestry:desktop:get-chat-capability',
  createChatSession: 'ancestry:desktop:create-chat-session',
  closeChatSession: 'ancestry:desktop:close-chat-session',
  startChatStream: 'ancestry:desktop:start-chat-stream',
  cancelChatStream: 'ancestry:desktop:cancel-chat-stream',
  acknowledgeChatStream: 'ancestry:desktop:acknowledge-chat-stream',
  listJobs: 'ancestry:desktop:list-jobs',
  getJob: 'ancestry:desktop:get-job',
  cancelJob: 'ancestry:desktop:cancel-job',
  subscribeJobEvents: 'ancestry:desktop:subscribe-job-events',
  unsubscribeJobEvents: 'ancestry:desktop:unsubscribe-job-events',
} as const)

/**
 * Maps the versioned desktop IPC protocol operations to stable Electron IPC channel names.
 */
export const desktopEventChannels = Object.freeze({
  chatEventBatch: 'ancestry:desktop:chat-event-batch',
  jobEvent: 'ancestry:desktop:job-event',
} as const)
