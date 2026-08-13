import {
  CHAT_STREAM_BATCH_MAX_BYTES,
  DESKTOP_PROTOCOL_VERSION,
  applicationSettingKeys,
  chatEventTypes,
  jobStates,
  providerDataClasses,
  providerIds,
  secretReferences,
  localRuntimeOperations,
  type AppInfo,
  type ApplicationSetting,
  type ApplicationSettingKey,
  type ApplicationSettings,
  type ApplicationSettingsPatch,
  type BridgeErrorCode,
  type BridgeResult,
  type CapabilityManifest,
  type ChatEvent,
  type ChatEventDelivery,
  type ChatEventPayload,
  type ChatStreamAcknowledgement,
  type ChatStreamAckRequest,
  type ChatStreamCancelRequest,
  type ChatStreamRun,
  type ChatStreamStartRequest,
  type ConsentCreateRequest,
  type ConsentGrantSummary,
  type ConsentPreview,
  type ConsentPreviewRequest,
  type ConsentRevokeRequest,
  type ConsentWarningCode,
  type DesktopColorScheme,
  type ArtifactRef,
  type FileFormat,
  type FileGrant,
  type FileGrantId,
  type FileGrantPurpose,
  type FileGrantRevocation,
  type FileValidation,
  type LocalPreferences,
  type LocalRuntimeApplyRequest,
  type LocalRuntimeOperation,
  type LocalRuntimePreview,
  type LocalRuntimeRequest,
  type LocalRuntimeReview,
  type LocalRuntimeResult,
  type LocalRuntimeStatus,
  type JobArtifactRef,
  type JobEvent,
  type JobEventDelivery,
  type JobEventSubscription,
  type JobEventSubscriptionRequest,
  type JobEventUnsubscription,
  type JobEventUnsubscriptionRequest,
  type JobList,
  type JobProgress,
  type JobRequest,
  type JobSnapshot,
  type OpenFileGrantRequest,
  type PreferenceUpdate,
  type ProviderConfiguration,
  type ProviderDataClass,
  type ProviderEndpointValidation,
  type ProviderEndpointValidationRequest,
  type ProviderId,
  type ProviderProfileCreateRequest,
  type ProviderProfileSummary,
  type SaveFileGrantRequest,
  type SecretReference,
  type SecretReferenceRequest,
  type SecretSetRequest,
  type SecretStatus,
  type StartupDiagnosticComponent,
  type StartupDiagnosticReport,
  type StartupDiagnostics,
  fileGrantPurposes,
  startupDiagnosticComponents,
} from './desktop'

type Parser<T> = (value: unknown) => T

const colorSchemes: readonly DesktopColorScheme[] = ['system', 'light', 'dark']
const bridgeErrorCodes: readonly BridgeErrorCode[] = [
  'INVALID_REQUEST',
  'UNAUTHORIZED_SENDER',
  'INVALID_RESPONSE',
  'BRIDGE_OVERLOADED',
  'REQUEST_CANCELLED',
  'REQUEST_TIMEOUT',
  'SIDECAR_UNAVAILABLE',
  'SIDECAR_REQUEST_FAILED',
  'STARTUP_MUTATION_BLOCKED',
  'PREFERENCES_UNAVAILABLE',
  'PREFERENCES_CONFLICT',
  'SETTINGS_UNAVAILABLE',
  'SETTINGS_CONFLICT',
  'SETTINGS_INVALID',
  'SECRET_STORE_UNAVAILABLE',
  'SECRET_ENVIRONMENT_MANAGED',
  'SECRET_INVALID',
  'PROVIDER_CONFIGURATION_UNAVAILABLE',
  'PROVIDER_CONFIGURATION_CONFLICT',
  'PROVIDER_CONFIGURATION_INVALID',
  'ENDPOINT_REJECTED',
  'CONSENT_INVALID',
  'CONSENT_PREVIEW_STALE',
  'FILE_SELECTION_INVALID',
  'FILE_TOO_LARGE',
  'FILE_GRANT_FORBIDDEN',
  'FILE_GRANT_REVOKED',
  'FILE_GRANT_STALE',
  'FILE_GRANT_CONFLICT',
  'FILE_DIALOG_FAILED',
  'RUNTIME_POLICY_INVALID',
  'RUNTIME_POLICY_SCHEMA_UNSUPPORTED',
  'RUNTIME_REQUEST_INVALID',
  'RUNTIME_HOST_UNSUPPORTED',
  'RUNTIME_PLAN_STALE',
  'RUNTIME_CONFIRMATION_REQUIRED',
  'RUNTIME_OFFLINE_UNAVAILABLE',
  'RUNTIME_DOWNLOAD_FAILED',
  'RUNTIME_ARTIFACT_INTEGRITY',
  'RUNTIME_COMPONENT_INTEGRITY',
  'RUNTIME_STORAGE_UNSAFE',
  'RUNTIME_NOT_INSTALLED',
  'RUNTIME_OWNERSHIP_INVALID',
  'RUNTIME_PROCESS_FAILED',
  'RUNTIME_HEALTH_FAILED',
  'JOB_ID_INVALID',
  'JOB_NOT_FOUND',
  'JOB_EVENT_CURSOR_INVALID',
  'JOB_EVENT_REPLAY_EXPIRED',
  'JOB_SERVICE_UNAVAILABLE',
  'JOB_SUBSCRIBER_LIMIT',
  'JOB_SUBSCRIPTION_CLOSED',
  'JOB_SUBSCRIPTION_CONFLICT',
  'JOB_EVENT_STREAM_FAILED',
  'CHAT_SESSION_NOT_FOUND',
  'CHAT_STREAM_NOT_FOUND',
  'CHAT_STREAM_CURSOR_INVALID',
  'CHAT_STREAM_REPLAY_EXPIRED',
  'CHAT_STREAM_SERVICE_UNAVAILABLE',
  'CHAT_STREAM_LIMIT',
  'CHAT_STREAM_BACKPRESSURE_TIMEOUT',
  'CHAT_STREAM_STALLED',
  'CHAT_STREAM_EVENT_INVALID',
  'INTERNAL_ERROR',
]
const startupStates = ['starting', 'ready', 'degraded', 'stopped'] as const
const startupFailures = ['startup_failed', 'startup_timeout', 'incompatible_build', 'crash_loop'] as const
const startupReportStatuses = ['ready', 'degraded'] as const
const startupComponentStatuses = ['ready', 'warning', 'blocked'] as const
const startupOperatingSystems = ['linux', 'macos', 'windows', 'unsupported'] as const
const startupArchitectures = ['x64', 'arm64', 'unsupported'] as const
const identifierPattern = /^[A-Za-z0-9._:-]+$/
const dispatchKeyPattern = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/
const fileGrantIdPattern = /^grt_[a-f0-9]{64}$/
const artifactIdPattern = /^art_[a-f0-9]{32}$/
const jobArtifactIdPattern = /^art_[A-Za-z0-9._:-]+$/
const jobIdPattern = /^j[0-9]{6,12}$/
const jobResourcePattern = /^resource_[a-f0-9]{64}$/
const jobSubscriptionIdPattern = /^sub_[a-f0-9]{32}$/
const chatSessionIdPattern = /^chat_[a-f0-9]{32}$/
const chatRunIdPattern = /^run_[a-f0-9]{32}$/
const chatEventCodePattern = /^[A-Z][A-Z0-9_]{0,99}$/
const digestPattern = /^[a-f0-9]{64}$/
const timestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/
// Control characters are intentionally rejected from user-visible file names.
// eslint-disable-next-line no-control-regex
const safeDisplayNamePattern = /^[^/\\\u0000-\u001f\u007f]+$/
// Diagnostic prose crosses the renderer trust boundary and must never expose paths.
// eslint-disable-next-line no-control-regex
const safeDiagnosticTextPattern = /^[^/\\\u0000-\u001f\u007f]+$/
const mediaTypePattern = /^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/
const fileFormats: readonly FileFormat[] = ['gedcom', 'rootsmagic', 'json', 'markdown']
const fileValidations: readonly FileValidation[] = ['validated-input', 'new-output', 'replacement-confirmed']
const secretStatuses = ['present', 'missing', 'unavailable'] as const
const providerValues = ['none', 'ollama', 'openai', 'anthropic', 'gemini', 'openrouter'] as const
const localRuntimeStates = ['not-installed', 'stopped', 'ready', 'unhealthy'] as const
const localRuntimeComponentNames = [
  'colima',
  'lima',
  'docker-cli',
  'docker-buildx',
  'docker-compose',
] as const
const localRuntimeComponentRepositories: Readonly<Record<
  typeof localRuntimeComponentNames[number],
  string
>> = {
  colima: 'abiosoft/colima',
  lima: 'lima-vm/lima',
  'docker-cli': 'docker/cli',
  'docker-buildx': 'docker/buildx',
  'docker-compose': 'docker/compose',
}
const localRuntimeAssetPattern = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,159}$/
const localRuntimeConfirmations: Readonly<Record<LocalRuntimeOperation, string>> = {
  setup: 'SET UP LOCAL RUNTIME',
  start: 'START LOCAL RUNTIME',
  stop: 'STOP LOCAL RUNTIME',
  repair: 'REPAIR LOCAL RUNTIME',
  'uninstall-preserve': 'REMOVE LOCAL RUNTIME',
  'uninstall-delete': 'DELETE LOCAL RUNTIME DATA',
}
const consentWarningCodes: readonly ConsentWarningCode[] = [
  'LIVING_PERSON_DATA_INCLUDED',
  'REMOTE_PROVIDER_SELECTED',
  'REMOTE_RETENTION_ENABLED',
]
const profileNamePattern = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$/
// Endpoint paths must not contain ASCII control characters or spaces.
// eslint-disable-next-line no-control-regex
const endpointPattern = /^(https?):\/\/(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::([0-9]{1,5}))?(\/[^?#\u0000-\u0020\u007f]*)?$/

const record = (value: unknown): value is Record<string, unknown> => {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}
const owns = (value: Record<string, unknown>, key: string): boolean =>
  Object.prototype.hasOwnProperty.call(value, key)
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]): boolean =>
  Object.keys(value).length === keys.length && keys.every((key) => owns(value, key))
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
  if (!record(value) || !owns(value, 'expectedRevision') || Object.keys(value).length < 2 || !onlyKeys(value, keys)) {
    throw new Error('Invalid preference update')
  }
  try {
    if (!integer(value.expectedRevision, 0, Number.MAX_SAFE_INTEGER)) throw new Error()
    if (owns(value, 'colorScheme')) parseColorScheme(value.colorScheme)
    if (owns(value, 'reducedMotion') && typeof value.reducedMotion !== 'boolean') throw new Error()
    if (owns(value, 'onboardingCompleted') && typeof value.onboardingCompleted !== 'boolean') throw new Error()
  } catch {
    throw new Error('Invalid preference update')
  }
  return deepFreeze({ ...value }) as PreferenceUpdate
}

function validSettingValue(key: ApplicationSettingKey, value: unknown): boolean {
  if (key === 'providers.default') {
    return typeof value === 'string' && providerValues.includes(value as typeof providerValues[number])
  }
  if (typeof value !== 'number' || !Number.isFinite(value)) return false
  if (key === 'limits.max_query_rows') return Number.isInteger(value) && value >= 1 && value <= 10_000
  if (key === 'limits.max_output_chars') return Number.isInteger(value) && value >= 1_000 && value <= 5_000_000
  if (key === 'limits.query_timeout_seconds') return value >= 0.1 && value <= 300
  return value >= 1 && value <= 600
}

export function parseSettingsPatch(value: unknown): ApplicationSettingsPatch {
  if (!record(value) || !exactKeys(value, ['schema_version', 'expected_revision', 'changes'])
    || value.schema_version !== 1 || !integer(value.expected_revision, 0, Number.MAX_SAFE_INTEGER)
    || !record(value.changes) || Object.keys(value.changes).length < 1
    || Object.keys(value.changes).length > applicationSettingKeys.length) {
    throw new Error('Invalid settings patch')
  }
  for (const [key, item] of Object.entries(value.changes)) {
    if (!applicationSettingKeys.includes(key as ApplicationSettingKey)
      || !validSettingValue(key as ApplicationSettingKey, item)) {
      throw new Error('Invalid settings patch')
    }
  }
  return deepFreeze({
    schema_version: 1,
    expected_revision: value.expected_revision,
    changes: { ...value.changes },
  }) as ApplicationSettingsPatch
}

function parseSecretReference(value: unknown): SecretReference {
  if (typeof value !== 'string' || !secretReferences.includes(value as SecretReference)) {
    throw new Error('Invalid secret reference request')
  }
  return value as SecretReference
}

export function parseSecretReferenceRequest(value: unknown): SecretReferenceRequest {
  if (!record(value) || !exactKeys(value, ['reference'])) throw new Error('Invalid secret reference request')
  return deepFreeze({ reference: parseSecretReference(value.reference) })
}

export function parseSecretSetRequest(value: unknown): SecretSetRequest {
  if (!record(value) || !exactKeys(value, ['reference', 'value'])) throw new Error('Invalid secret set request')
  let reference: SecretReference
  try { reference = parseSecretReference(value.reference) } catch { throw new Error('Invalid secret set request') }
  if (!bounded(value.value, 1, 65_536)) throw new Error('Invalid secret set request')
  return deepFreeze({ reference, value: value.value })
}

function parseProviderId(value: unknown, message: string): ProviderId {
  if (typeof value !== 'string' || !providerIds.includes(value as ProviderId)) throw new Error(message)
  return value as ProviderId
}

function parseRevision(value: unknown, message: string): string {
  if (typeof value !== 'string' || !digestPattern.test(value)) throw new Error(message)
  return value
}

function parseProfileName(value: unknown, message: string): string {
  if (!bounded(value, 1, 200) || value.trim() !== value || !profileNamePattern.test(value)) {
    throw new Error(message)
  }
  return value
}

function parseModel(value: unknown, message: string): string {
  if (!bounded(value, 1, 200) || value.trim() !== value || hasControlCharacter(value)) {
    throw new Error(message)
  }
  return value
}

function hasControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0)
    return codePoint !== undefined && (codePoint < 32 || codePoint === 127)
  })
}

function parseEndpoint(value: unknown, message: string): string {
  if (!bounded(value, 1, 2_048) || value.trim() !== value) throw new Error(message)
  const match = endpointPattern.exec(value)
  if (!match || match[2]?.includes('..')) throw new Error(message)
  if (match[3] !== undefined && !integer(Number(match[3]), 1, 65_535)) throw new Error(message)
  return value
}

function endpointHostname(value: string): string | undefined {
  const match = endpointPattern.exec(value)
  return match?.[2]?.toLowerCase().replace(/^\[|\]$/g, '')
}

function parseSafeCodes(value: unknown, message: string): readonly string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 128
    || value.some((item) => !bounded(item, 1, 96) || !identifierPattern.test(item))) {
    throw new Error(message)
  }
  return value.slice() as string[]
}

function parseModels(value: unknown, message: string): readonly string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 128
    || value.some((item) => !bounded(item, 1, 200)
      || item.trim() !== item || hasControlCharacter(item))) {
    throw new Error(message)
  }
  return value.slice() as string[]
}

function parseDataClasses(value: unknown, message: string): readonly ProviderDataClass[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > providerDataClasses.length
    || value.some((item) => typeof item !== 'string'
      || !providerDataClasses.includes(item as ProviderDataClass))) throw new Error(message)
  return value.slice() as ProviderDataClass[]
}

function parseCost(value: unknown, message: string): number | null {
  if (value === null) return null
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error(message)
  return value
}

export function parseProviderProfileCreateRequest(value: unknown): ProviderProfileCreateRequest {
  const message = 'Invalid provider profile request'
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'expected_revision', 'name', 'provider_id', 'model', 'endpoint',
    'endpoint_identity_sha256',
  ]) || value.schema_version !== 1) throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    expected_revision: parseRevision(value.expected_revision, message),
    name: parseProfileName(value.name, message),
    provider_id: parseProviderId(value.provider_id, message),
    model: parseModel(value.model, message),
    endpoint: parseEndpoint(value.endpoint, message),
    endpoint_identity_sha256: parseRevision(value.endpoint_identity_sha256, message),
  })
}

export function parseProviderEndpointValidationRequest(value: unknown): ProviderEndpointValidationRequest {
  const message = 'Invalid provider endpoint request'
  if (!record(value) || !exactKeys(value, ['schema_version', 'provider_id', 'endpoint'])
    || value.schema_version !== 1) throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    provider_id: parseProviderId(value.provider_id, message),
    endpoint: parseEndpoint(value.endpoint, message),
  })
}

export function parseConsentPreviewRequest(value: unknown): ConsentPreviewRequest {
  const message = 'Invalid consent preview request'
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'provider_profile_name', 'modules', 'purposes', 'data_classes',
    'models', 'max_cost_usd', 'retain_payloads',
  ]) || value.schema_version !== 1 || typeof value.retain_payloads !== 'boolean') throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    provider_profile_name: parseProfileName(value.provider_profile_name, message),
    modules: parseSafeCodes(value.modules, message),
    purposes: parseSafeCodes(value.purposes, message),
    data_classes: parseDataClasses(value.data_classes, message),
    models: parseModels(value.models, message),
    max_cost_usd: parseCost(value.max_cost_usd, message),
    retain_payloads: value.retain_payloads,
  })
}

function parseConsentPreviewPayload(value: unknown, message: string): Readonly<ConsentPreview> {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'provider_profile_name', 'provider_id', 'modules', 'purposes',
    'data_classes', 'models', 'max_cost_usd', 'retain_payloads', 'warning_codes',
  ]) || value.schema_version !== 1 || typeof value.retain_payloads !== 'boolean'
    || !Array.isArray(value.warning_codes) || value.warning_codes.length > consentWarningCodes.length
    || value.warning_codes.some((item) => typeof item !== 'string'
      || !consentWarningCodes.includes(item as ConsentWarningCode))) throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    provider_profile_name: parseProfileName(value.provider_profile_name, message),
    provider_id: parseProviderId(value.provider_id, message),
    modules: parseSafeCodes(value.modules, message),
    purposes: parseSafeCodes(value.purposes, message),
    data_classes: parseDataClasses(value.data_classes, message),
    models: parseModels(value.models, message),
    max_cost_usd: parseCost(value.max_cost_usd, message),
    retain_payloads: value.retain_payloads,
    warning_codes: value.warning_codes.slice() as ConsentWarningCode[],
  })
}

export function parseConsentCreateRequest(value: unknown): ConsentCreateRequest {
  const message = 'Invalid consent creation request'
  if (!record(value) || !exactKeys(value, ['schema_version', 'expected_revision', 'name', 'preview'])
    || value.schema_version !== 1) throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    expected_revision: parseRevision(value.expected_revision, message),
    name: parseProfileName(value.name, message),
    preview: parseConsentPreviewPayload(value.preview, message),
  })
}

export function parseConsentRevokeRequest(value: unknown): ConsentRevokeRequest {
  const message = 'Invalid consent revocation request'
  if (!record(value) || !exactKeys(value, ['schema_version', 'expected_revision', 'name'])
    || value.schema_version !== 1) throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    expected_revision: parseRevision(value.expected_revision, message),
    name: parseProfileName(value.name, message),
  })
}

export function parseOpenFileGrantRequest(value: unknown): OpenFileGrantRequest {
  if (!record(value) || !exactKeys(value, ['purpose'])
    || (value.purpose !== 'gedcom-read' && value.purpose !== 'rootsmagic-read')) {
    throw new Error('Invalid open-file grant request')
  }
  return deepFreeze({ purpose: value.purpose })
}

export function parseSaveFileGrantRequest(value: unknown): SaveFileGrantRequest {
  if (!record(value) || !exactKeys(value, ['purpose', 'suggestedName'])
    || (value.purpose !== 'gedcom-write' && value.purpose !== 'json-write' && value.purpose !== 'markdown-write')
    || !bounded(value.suggestedName, 1, 255) || !safeDisplayNamePattern.test(value.suggestedName)
    || value.suggestedName === '.' || value.suggestedName === '..') {
    throw new Error('Invalid save-file grant request')
  }
  return deepFreeze({ purpose: value.purpose, suggestedName: value.suggestedName })
}

function parseLocalRuntimeOperation(value: unknown): LocalRuntimeOperation {
  if (
    typeof value !== 'string'
    || !localRuntimeOperations.includes(value as LocalRuntimeOperation)
  ) throw new Error('Invalid local runtime request')
  return value as LocalRuntimeOperation
}

export function parseLocalRuntimeRequest(value: unknown): LocalRuntimeRequest {
  if (
    !record(value)
    || !exactKeys(value, ['schema_version', 'operation', 'offline'])
    || value.schema_version !== 1
    || typeof value.offline !== 'boolean'
  ) throw new Error('Invalid local runtime request')
  return deepFreeze({
    schema_version: 1,
    operation: parseLocalRuntimeOperation(value.operation),
    offline: value.offline,
  })
}

export function parseLocalRuntimeApplyRequest(value: unknown): LocalRuntimeApplyRequest {
  if (
    !record(value)
    || !exactKeys(value, [
      'schema_version',
      'operation',
      'offline',
      'plan_revision',
      'confirmation',
    ])
    || value.schema_version !== 1
    || typeof value.offline !== 'boolean'
    || typeof value.plan_revision !== 'string'
    || !digestPattern.test(value.plan_revision)
    || !bounded(value.confirmation, 0, 64)
  ) throw new Error('Invalid local runtime apply request')
  return deepFreeze({
    schema_version: 1,
    operation: parseLocalRuntimeOperation(value.operation),
    offline: value.offline,
    plan_revision: value.plan_revision,
    confirmation: value.confirmation,
  })
}

export function parseFileGrantId(value: unknown): FileGrantId {
  if (typeof value !== 'string' || !fileGrantIdPattern.test(value)) throw new Error('Invalid file-grant ID')
  return value as FileGrantId
}

function parseChatSessionId(value: unknown, message: string): string {
  if (typeof value !== 'string' || !chatSessionIdPattern.test(value)) throw new Error(message)
  return value
}

function parseChatRunId(value: unknown, message: string): string {
  if (typeof value !== 'string' || !chatRunIdPattern.test(value)) throw new Error(message)
  return value
}

function validChatText(value: unknown, maximum: number): value is string {
  return typeof value === 'string'
    && Array.from(value).length >= 1
    && Array.from(value).length <= maximum
    && value.trim().length > 0
    && !value.includes('\u0000')
}

function validChatEventText(value: unknown, maximum: number): value is string {
  return typeof value === 'string'
    && Array.from(value).length >= 1
    && Array.from(value).length <= maximum
    && !value.includes('\u0000')
}

function validFiniteNumber(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number'
    && Number.isFinite(value)
    && value >= minimum
    && value <= maximum
}

function utf8ByteLength(value: string): number {
  let bytes = 0
  for (let index = 0; index < value.length; index += 1) {
    const codePoint = value.codePointAt(index)
    if (codePoint === undefined) continue
    if (codePoint <= 0x7f) bytes += 1
    else if (codePoint <= 0x7ff) bytes += 2
    else if (codePoint <= 0xffff) bytes += 3
    else {
      bytes += 4
      index += 1
    }
  }
  return bytes
}

export function parseChatStreamStartRequest(value: unknown): ChatStreamStartRequest {
  const message = 'Invalid chat-stream start request'
  if (!record(value) || !exactKeys(value, [
    'schema_version',
    'session_id',
    'message',
    'max_output_tokens',
    'temperature',
    'timeout_seconds',
    'max_safe_retries',
  ]) || value.schema_version !== 1
    || !validChatText(value.message, 16_384)
    || !integer(value.max_output_tokens, 1, 4_096)
    || !validFiniteNumber(value.temperature, 0, 1)
    || !validFiniteNumber(value.timeout_seconds, 1, 120)
    || !integer(value.max_safe_retries, 0, 1)) throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    session_id: parseChatSessionId(value.session_id, message),
    message: value.message,
    max_output_tokens: value.max_output_tokens,
    temperature: value.temperature,
    timeout_seconds: value.timeout_seconds,
    max_safe_retries: value.max_safe_retries,
  })
}

export function parseChatStreamCancelRequest(value: unknown): ChatStreamCancelRequest {
  const message = 'Invalid chat-stream cancellation request'
  if (!record(value) || !exactKeys(value, ['schema_version', 'session_id', 'run_id'])
    || value.schema_version !== 1) throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    session_id: parseChatSessionId(value.session_id, message),
    run_id: parseChatRunId(value.run_id, message),
  })
}

export function parseChatStreamAckRequest(value: unknown): ChatStreamAckRequest {
  const message = 'Invalid chat-stream acknowledgement request'
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'session_id', 'run_id', 'through_sequence',
  ]) || value.schema_version !== 1
    || !integer(value.through_sequence, 1, Number.MAX_SAFE_INTEGER)) throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    session_id: parseChatSessionId(value.session_id, message),
    run_id: parseChatRunId(value.run_id, message),
    through_sequence: value.through_sequence,
  })
}

function parseChatTimestamp(value: unknown): string {
  if (!bounded(value, 1, 64)
    || !timestampPattern.test(value)
    || !/(?:Z|[+-]00:00)$/.test(value)
    || Number.isNaN(Date.parse(value))) invalidResponse()
  return value
}

function parseChatEventPayload(value: unknown, type: ChatEvent['type']): Readonly<ChatEventPayload> {
  if (!record(value) || !exactKeys(value, [
    'text', 'code', 'provider_id', 'model', 'remote', 'message_count',
  ])) invalidResponse()
  const populated = Object.entries(value)
    .filter(([, item]) => item !== null)
    .map(([name]) => name)
    .sort()
  const expected: Readonly<Record<ChatEvent['type'], readonly string[]>> = {
    active: ['model', 'provider_id', 'remote'],
    'first-token': ['text'],
    delta: ['text'],
    cancelling: [],
    completed: ['message_count'],
    interrupted: ['code'],
    failed: ['code'],
  }
  if (populated.join(',') !== expected[type].slice().sort().join(',')) invalidResponse()
  if (value.text !== null && !validChatEventText(value.text, 16_384)) invalidResponse()
  if (value.code !== null
    && (typeof value.code !== 'string' || !chatEventCodePattern.test(value.code))) invalidResponse()
  if (value.provider_id !== null && !validChatText(value.provider_id, 200)) invalidResponse()
  if (value.model !== null && !validChatText(value.model, 200)) invalidResponse()
  if (value.remote !== null && typeof value.remote !== 'boolean') invalidResponse()
  if (value.message_count !== null && !integer(value.message_count, 0, 32)) invalidResponse()
  return deepFreeze({ ...value }) as unknown as Readonly<ChatEventPayload>
}

function parseChatEvent(value: unknown): Readonly<ChatEvent> {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'run_id', 'sequence', 'type', 'timestamp', 'payload',
  ]) || value.schema_version !== 1
    || !integer(value.sequence, 1, Number.MAX_SAFE_INTEGER)
    || typeof value.type !== 'string'
    || !chatEventTypes.includes(value.type as ChatEvent['type'])) invalidResponse()
  const type = value.type as ChatEvent['type']
  return deepFreeze({
    schema_version: 1,
    run_id: parseChatRunId(value.run_id, 'Invalid bridge response'),
    sequence: value.sequence,
    type,
    timestamp: parseChatTimestamp(value.timestamp),
    payload: parseChatEventPayload(value.payload, type),
  })
}

function parseChatStreamRun(value: unknown): Readonly<ChatStreamRun> {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'session_id', 'run_id', 'state', 'latest_sequence', 'terminal',
  ]) || value.schema_version !== 1
    || !integer(value.latest_sequence, 1, Number.MAX_SAFE_INTEGER)
    || !['active', 'cancelling', 'completed', 'interrupted', 'failed'].includes(value.state as string)
    || typeof value.terminal !== 'boolean') invalidResponse()
  const terminal = ['completed', 'interrupted', 'failed'].includes(value.state as string)
  if (terminal !== value.terminal) invalidResponse()
  return deepFreeze({
    schema_version: 1,
    session_id: parseChatSessionId(value.session_id, 'Invalid bridge response'),
    run_id: parseChatRunId(value.run_id, 'Invalid bridge response'),
    state: value.state,
    latest_sequence: value.latest_sequence,
    terminal: value.terminal,
  } as ChatStreamRun)
}

function parseChatStreamAcknowledgement(value: unknown): Readonly<ChatStreamAcknowledgement> {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'session_id', 'run_id', 'through_sequence', 'acknowledged',
  ]) || value.schema_version !== 1 || value.acknowledged !== true
    || !integer(value.through_sequence, 1, Number.MAX_SAFE_INTEGER)) invalidResponse()
  return deepFreeze({
    schema_version: 1,
    session_id: parseChatSessionId(value.session_id, 'Invalid bridge response'),
    run_id: parseChatRunId(value.run_id, 'Invalid bridge response'),
    through_sequence: value.through_sequence,
    acknowledged: true,
  })
}

export function parseChatEventDelivery(value: unknown): Readonly<ChatEventDelivery> {
  if (!record(value) || !exactKeys(value, [
    'schema_version',
    'kind',
    'session_id',
    'run_id',
    'from_sequence',
    'through_sequence',
    'encoded_bytes',
    'events',
    'error',
  ]) || value.schema_version !== 1 || (value.kind !== 'batch' && value.kind !== 'failure')) {
    invalidResponse()
  }
  const sessionId = parseChatSessionId(value.session_id, 'Invalid bridge response')
  const runId = parseChatRunId(value.run_id, 'Invalid bridge response')
  if (value.kind === 'batch') {
    if (!integer(value.from_sequence, 1, Number.MAX_SAFE_INTEGER)
      || !integer(value.through_sequence, value.from_sequence, Number.MAX_SAFE_INTEGER)
      || !integer(value.encoded_bytes, 1, CHAT_STREAM_BATCH_MAX_BYTES)
      || !Array.isArray(value.events) || value.events.length < 1 || value.events.length > 4_096
      || value.error !== null) invalidResponse()
    const fromSequence = value.from_sequence
    const throughSequence = value.through_sequence
    const declaredBytes = value.encoded_bytes
    const events = value.events.map(parseChatEvent)
    const encodedBytes = utf8ByteLength(JSON.stringify(events))
    if (events[0]?.sequence !== fromSequence
      || events.at(-1)?.sequence !== throughSequence
      || encodedBytes !== declaredBytes
      || events.some((event, index) => event.run_id !== runId
        || event.sequence !== fromSequence + index)
      || events.slice(0, -1).some((event) => ['completed', 'interrupted', 'failed'].includes(event.type))) {
      invalidResponse()
    }
    return deepFreeze({
      schema_version: 1,
      kind: 'batch',
      session_id: sessionId,
      run_id: runId,
      from_sequence: fromSequence,
      through_sequence: throughSequence,
      encoded_bytes: declaredBytes,
      events,
      error: null,
    })
  }
  if (value.from_sequence !== null || value.through_sequence !== null
    || value.encoded_bytes !== 0 || value.events !== null || !record(value.error)
    || !exactKeys(value.error, ['code', 'message', 'remediation'])
    || !bridgeErrorCodes.includes(value.error.code as BridgeErrorCode)
    || !bounded(value.error.message, 1, 240) || !safeDiagnosticTextPattern.test(value.error.message)
    || !bounded(value.error.remediation, 1, 240)
    || !safeDiagnosticTextPattern.test(value.error.remediation)) invalidResponse()
  return deepFreeze({
    schema_version: 1,
    kind: 'failure',
    session_id: sessionId,
    run_id: runId,
    from_sequence: null,
    through_sequence: null,
    encoded_bytes: 0,
    events: null,
    error: {
      code: value.error.code,
      message: value.error.message,
      remediation: value.error.remediation,
    },
  } as ChatEventDelivery)
}

function parseJobId(value: unknown, message: string): string {
  if (typeof value !== 'string' || !jobIdPattern.test(value)) throw new Error(message)
  return value
}

function parseJobSubscriptionId(value: unknown, message: string): string {
  if (typeof value !== 'string' || !jobSubscriptionIdPattern.test(value)) throw new Error(message)
  return value
}

function parseJobTimestamp(value: unknown): string {
  if (!bounded(value, 1, 64) || !timestampPattern.test(value) || Number.isNaN(Date.parse(value))) {
    invalidResponse()
  }
  return value
}

function parseNullableJobTimestamp(value: unknown): string | null {
  return value === null ? null : parseJobTimestamp(value)
}

function validJobText(value: unknown, maximum: number): value is string {
  return typeof value === 'string'
    && Array.from(value).length <= maximum
    && !value.includes('\u0000')
}

function parseNullableJobText(value: unknown, maximum: number): string | null {
  if (value === null) return null
  if (!validJobText(value, maximum)) invalidResponse()
  return value
}

function parseNullableJobCode(value: unknown): string | null {
  if (value === null) return null
  if (!bounded(value, 1, 96) || !identifierPattern.test(value)) invalidResponse()
  return value
}

function parseJobProgress(value: unknown): Readonly<JobProgress> {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'operation', 'timestamp', 'completed', 'total',
  ]) || value.schema_version !== 1
    || !validJobText(value.operation, 512) || value.operation.trim().length === 0) invalidResponse()
  const timestamp = parseJobTimestamp(value.timestamp)
  if ((value.completed === null) !== (value.total === null)) invalidResponse()
  if (value.completed !== null && (!integer(value.completed, 0, 1_000_000_000)
    || !integer(value.total, 1, 1_000_000_000)
    || value.completed > value.total)) invalidResponse()
  return deepFreeze({
    schema_version: 1,
    operation: value.operation,
    timestamp,
    completed: value.completed,
    total: value.total,
  } as JobProgress)
}

function parseJobArtifact(value: unknown): Readonly<JobArtifactRef> {
  if (!record(value) || !exactKeys(value, [
    'artifact_id', 'media_type', 'artifact_type', 'size_bytes', 'status', 'sha256',
  ]) || !bounded(value.artifact_id, 36, 132) || !jobArtifactIdPattern.test(value.artifact_id)
    || value.artifact_id.includes('..')
    || !bounded(value.media_type, 3, 127) || !mediaTypePattern.test(value.media_type)
    || !bounded(value.artifact_type, 1, 96) || !identifierPattern.test(value.artifact_type)
    || !integer(value.size_bytes, 0, 2_147_483_648)
    || !['pending', 'ready', 'failed', 'revoked'].includes(value.status as string)
    || (value.sha256 !== null && (typeof value.sha256 !== 'string'
      || !digestPattern.test(value.sha256)))) invalidResponse()
  return deepFreeze({ ...value }) as unknown as Readonly<JobArtifactRef>
}

function parseJobSnapshot(value: unknown): Readonly<JobSnapshot> {
  const keys = [
    'schema_version',
    'sequence',
    'job_id',
    'name',
    'state',
    'submitted_at',
    'started_at',
    'finished_at',
    'resource_refs',
    'artifact',
    'outcome_summary',
    'next_action',
    'error_code',
    'error_message',
    'error_remediation',
    'progress',
    'cancellation_requested_at',
    'cancellation_deferred_by',
  ] as const
  if (!record(value) || !exactKeys(value, keys) || value.schema_version !== 1
    || !integer(value.sequence, 1, 9_999_999_999)
    || typeof value.job_id !== 'string' || !jobIdPattern.test(value.job_id)
    || !validJobText(value.name, 256) || value.name.trim().length === 0
    || typeof value.state !== 'string' || !jobStates.includes(value.state as typeof jobStates[number])
    || !Array.isArray(value.resource_refs) || value.resource_refs.length > 32
    || value.resource_refs.some((item) => typeof item !== 'string' || !jobResourcePattern.test(item))
    || new Set(value.resource_refs).size !== value.resource_refs.length) invalidResponse()

  const submittedAt = parseJobTimestamp(value.submitted_at)
  const startedAt = parseNullableJobTimestamp(value.started_at)
  const finishedAt = parseNullableJobTimestamp(value.finished_at)
  const cancellationRequestedAt = parseNullableJobTimestamp(value.cancellation_requested_at)
  const artifact = value.artifact === null ? null : parseJobArtifact(value.artifact)
  const progress = value.progress === null ? null : parseJobProgress(value.progress)
  const terminal = ['completed', 'failed', 'cancelled'].includes(value.state)
  if ((terminal && finishedAt === null) || (!terminal && finishedAt !== null)
    || (artifact !== null && value.state !== 'completed')) invalidResponse()

  return deepFreeze({
    schema_version: 1,
    sequence: value.sequence,
    job_id: value.job_id,
    name: value.name,
    state: value.state,
    submitted_at: submittedAt,
    started_at: startedAt,
    finished_at: finishedAt,
    resource_refs: value.resource_refs.slice(),
    artifact,
    outcome_summary: parseNullableJobText(value.outcome_summary, 2_048),
    next_action: parseNullableJobText(value.next_action, 2_048),
    error_code: parseNullableJobCode(value.error_code),
    error_message: parseNullableJobText(value.error_message, 2_048),
    error_remediation: parseNullableJobText(value.error_remediation, 2_048),
    progress,
    cancellation_requested_at: cancellationRequestedAt,
    cancellation_deferred_by: parseNullableJobText(value.cancellation_deferred_by, 512),
  } as JobSnapshot)
}

function parseJobEvent(value: unknown): Readonly<JobEvent> {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'sequence', 'kind', 'created_at', 'snapshot',
  ]) || value.schema_version !== 1 || !integer(value.sequence, 1, 9_999_999_999)
    || !['snapshot', 'progress', 'cancellation', 'terminal'].includes(value.kind as string)) {
    invalidResponse()
  }
  const createdAt = parseJobTimestamp(value.created_at)
  const snapshot = parseJobSnapshot(value.snapshot)
  const terminal = ['completed', 'failed', 'cancelled'].includes(snapshot.state)
  if (value.sequence !== snapshot.sequence || ((value.kind === 'terminal') !== terminal)) {
    invalidResponse()
  }
  return deepFreeze({
    schema_version: 1,
    sequence: value.sequence,
    kind: value.kind,
    created_at: createdAt,
    snapshot,
  } as JobEvent)
}

function parseJobList(value: unknown): Readonly<JobList> {
  if (!record(value) || !exactKeys(value, ['schema_version', 'jobs'])
    || value.schema_version !== 1 || !Array.isArray(value.jobs) || value.jobs.length > 1_000) {
    invalidResponse()
  }
  const jobs = value.jobs.map(parseJobSnapshot)
  if (new Set(jobs.map((job) => job.job_id)).size !== jobs.length) invalidResponse()
  return deepFreeze({ schema_version: 1, jobs })
}

function parseJobEventSubscription(value: unknown): Readonly<JobEventSubscription> {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'subscription_id', 'job_id', 'subscribed',
  ]) || value.schema_version !== 1 || value.subscribed !== true) invalidResponse()
  return deepFreeze({
    schema_version: 1,
    subscription_id: parseJobSubscriptionId(value.subscription_id, 'Invalid bridge response'),
    job_id: parseJobId(value.job_id, 'Invalid bridge response'),
    subscribed: true,
  })
}

function parseJobEventUnsubscription(value: unknown): Readonly<JobEventUnsubscription> {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'subscription_id', 'unsubscribed',
  ]) || value.schema_version !== 1 || value.unsubscribed !== true) invalidResponse()
  return deepFreeze({
    schema_version: 1,
    subscription_id: parseJobSubscriptionId(value.subscription_id, 'Invalid bridge response'),
    unsubscribed: true,
  })
}

export function parseJobRequest(value: unknown): JobRequest {
  const message = 'Invalid job request'
  if (!record(value) || !exactKeys(value, ['schema_version', 'job_id'])
    || value.schema_version !== 1) throw new Error(message)
  return deepFreeze({ schema_version: 1, job_id: parseJobId(value.job_id, message) })
}

export function parseJobEventSubscriptionRequest(value: unknown): JobEventSubscriptionRequest {
  const message = 'Invalid job-event subscription request'
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'subscription_id', 'job_id', 'after',
  ]) || value.schema_version !== 1 || !integer(value.after, 0, 9_999_999_999)) {
    throw new Error(message)
  }
  return deepFreeze({
    schema_version: 1,
    subscription_id: parseJobSubscriptionId(value.subscription_id, message),
    job_id: parseJobId(value.job_id, message),
    after: value.after,
  })
}

export function parseJobEventUnsubscriptionRequest(value: unknown): JobEventUnsubscriptionRequest {
  const message = 'Invalid job-event unsubscription request'
  if (!record(value) || !exactKeys(value, ['schema_version', 'subscription_id'])
    || value.schema_version !== 1) throw new Error(message)
  return deepFreeze({
    schema_version: 1,
    subscription_id: parseJobSubscriptionId(value.subscription_id, message),
  })
}

export function parseJobEventDelivery(value: unknown): Readonly<JobEventDelivery> {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'kind', 'subscription_id', 'job_id', 'event', 'error',
  ]) || value.schema_version !== 1 || (value.kind !== 'event' && value.kind !== 'failure')) {
    invalidResponse()
  }
  const subscriptionId = parseJobSubscriptionId(value.subscription_id, 'Invalid bridge response')
  const jobId = parseJobId(value.job_id, 'Invalid bridge response')
  if (value.kind === 'event') {
    if (value.error !== null) invalidResponse()
    const event = parseJobEvent(value.event)
    if (event.snapshot.job_id !== jobId) invalidResponse()
    return deepFreeze({
      schema_version: 1,
      kind: 'event',
      subscription_id: subscriptionId,
      job_id: jobId,
      event,
      error: null,
    })
  }
  if (value.event !== null || !record(value.error)
    || !exactKeys(value.error, ['code', 'message', 'remediation'])
    || !bridgeErrorCodes.includes(value.error.code as BridgeErrorCode)
    || !bounded(value.error.message, 1, 240) || !safeDiagnosticTextPattern.test(value.error.message)
    || !bounded(value.error.remediation, 1, 240)
    || !safeDiagnosticTextPattern.test(value.error.remediation)) invalidResponse()
  return deepFreeze({
    schema_version: 1,
    kind: 'failure',
    subscription_id: subscriptionId,
    job_id: jobId,
    event: null,
    error: {
      code: value.error.code,
      message: value.error.message,
      remediation: value.error.remediation,
    },
  } as JobEventDelivery)
}

function parseAppInfo(value: unknown): AppInfo {
  if (!record(value) || !exactKeys(value, ['applicationName', 'appVersion', 'buildChannel'])
    || value.applicationName !== 'AncestryLLM' || !bounded(value.appVersion, 1, 128)
    || (value.buildChannel !== 'development' && value.buildChannel !== 'packaged')) invalidResponse()
  return value as unknown as AppInfo
}

function parseStartupDiagnosticComponent(value: unknown, expectedComponent: string): StartupDiagnosticComponent {
  if (!record(value)
    || !exactKeys(value, ['component', 'status', 'code', 'message', 'remediation', 'restart_required', 'blocks_mutations'])
    || value.component !== expectedComponent
    || !startupComponentStatuses.includes(value.status as typeof startupComponentStatuses[number])
    || !bounded(value.code, 1, 96) || !identifierPattern.test(value.code)
    || !bounded(value.message, 1, 512) || !safeDiagnosticTextPattern.test(value.message)
    || (value.remediation !== null
      && (!bounded(value.remediation, 1, 512) || !safeDiagnosticTextPattern.test(value.remediation)))
    || typeof value.restart_required !== 'boolean'
    || typeof value.blocks_mutations !== 'boolean'
    || ((value.status === 'blocked') !== value.blocks_mutations)) invalidResponse()
  return value as unknown as StartupDiagnosticComponent
}

export function parseStartupDiagnosticReport(value: unknown): Readonly<StartupDiagnosticReport> {
  if (!record(value)
    || !exactKeys(value, ['schema_version', 'status', 'platform', 'components'])
    || value.schema_version !== 1
    || !startupReportStatuses.includes(value.status as typeof startupReportStatuses[number])
    || !record(value.platform)
    || !exactKeys(value.platform, ['operating_system', 'architecture'])
    || !startupOperatingSystems.includes(value.platform.operating_system as typeof startupOperatingSystems[number])
    || !startupArchitectures.includes(value.platform.architecture as typeof startupArchitectures[number])
    || !Array.isArray(value.components)
    || value.components.length !== startupDiagnosticComponents.length) invalidResponse()
  const components = value.components.map((component, index) =>
    parseStartupDiagnosticComponent(component, startupDiagnosticComponents[index] ?? ''))
  const blocked = components.some((component) => component.blocks_mutations)
  if ((value.status === 'degraded') !== blocked) invalidResponse()
  return deepFreeze({
    schema_version: 1,
    status: value.status,
    platform: {
      operating_system: value.platform.operating_system,
      architecture: value.platform.architecture,
    },
    components,
  } as StartupDiagnosticReport)
}

function parseStartupDiagnostics(value: unknown): StartupDiagnostics {
  if (!record(value) || !exactKeys(value, ['state', 'failure', 'automaticRestartsRemaining', 'manualRetriesRemaining', 'report'])
    || !startupStates.includes(value.state as typeof startupStates[number])
    || (value.failure !== null && !startupFailures.includes(value.failure as typeof startupFailures[number]))
    || !integer(value.automaticRestartsRemaining, 0, 100)
    || !integer(value.manualRetriesRemaining, 0, 100)) invalidResponse()
  const report = value.report === null ? null : parseStartupDiagnosticReport(value.report)
  if (value.state === 'ready' && report?.status !== 'ready') invalidResponse()
  if (value.state === 'degraded' && report !== null && report.status !== 'degraded') invalidResponse()
  if ((value.state === 'starting' || value.state === 'stopped') && report !== null) invalidResponse()
  return deepFreeze({
    state: value.state,
    failure: value.failure,
    automaticRestartsRemaining: value.automaticRestartsRemaining,
    manualRetriesRemaining: value.manualRetriesRemaining,
    report,
  } as StartupDiagnostics)
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

function expectedSettingDescriptor(key: ApplicationSettingKey): Readonly<{
  type: ApplicationSetting['type']
  allowedValues: readonly string[]
  minimum: number | null
  maximum: number | null
}> {
  switch (key) {
    case 'providers.default': return { type: 'string', allowedValues: providerValues, minimum: null, maximum: null }
    case 'limits.max_query_rows': return { type: 'integer', allowedValues: [], minimum: 1, maximum: 10_000 }
    case 'limits.max_output_chars': return { type: 'integer', allowedValues: [], minimum: 1_000, maximum: 5_000_000 }
    case 'limits.query_timeout_seconds': return { type: 'number', allowedValues: [], minimum: 0.1, maximum: 300 }
    case 'limits.provider_timeout_seconds': return { type: 'number', allowedValues: [], minimum: 1, maximum: 600 }
  }
}

function parseSettingField(value: unknown): ApplicationSetting {
  if (!record(value)
    || !exactKeys(value, ['key', 'label', 'help', 'type', 'value', 'default_value', 'validation', 'restart_required', 'sensitive'])
    || !applicationSettingKeys.includes(value.key as ApplicationSettingKey)
    || !bounded(value.label, 1, 128) || !bounded(value.help, 1, 512)
    || typeof value.restart_required !== 'boolean' || value.sensitive !== false
    || !record(value.validation)
    || !exactKeys(value.validation, ['allowed_values', 'minimum', 'maximum'])
    || !Array.isArray(value.validation.allowed_values)
    || value.validation.allowed_values.some((item) => !bounded(item, 1, 64))) invalidResponse()
  const key = value.key as ApplicationSettingKey
  const expected = expectedSettingDescriptor(key)
  const allowedValuesMatch = key === 'providers.default'
    ? value.validation.allowed_values.length === expected.allowedValues.length
      && value.validation.allowed_values.every((item, index) => item === expected.allowedValues[index])
    : value.validation.allowed_values.length === 0
  if (value.type !== expected.type || !validSettingValue(key, value.value)
    || !validSettingValue(key, value.default_value)
    || value.validation.minimum !== expected.minimum || value.validation.maximum !== expected.maximum
    || !allowedValuesMatch) invalidResponse()
  return value as unknown as ApplicationSetting
}

function parseSettings(value: unknown): ApplicationSettings {
  if (!record(value) || !exactKeys(value, ['schema_version', 'revision', 'fields'])
    || value.schema_version !== 1 || !integer(value.revision, 0, Number.MAX_SAFE_INTEGER)
    || !Array.isArray(value.fields) || value.fields.length !== applicationSettingKeys.length) invalidResponse()
  const fields = value.fields.map(parseSettingField)
  const fieldKeys = new Set(fields.map((field) => field.key))
  if (fieldKeys.size !== fields.length || applicationSettingKeys.some((key) => !fieldKeys.has(key))) invalidResponse()
  return deepFreeze({ schema_version: 1, revision: value.revision as number, fields })
}

function parseSecretStatus(value: unknown): SecretStatus {
  if (!record(value) || !exactKeys(value, ['reference', 'status'])
    || !secretReferences.includes(value.reference as SecretReference)
    || !secretStatuses.includes(value.status as typeof secretStatuses[number])) invalidResponse()
  return value as unknown as SecretStatus
}

const expectedProviderEndpoint: Readonly<Record<Exclude<ProviderId, 'ollama'>, string>> = Object.freeze({
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
  gemini: 'https://generativelanguage.googleapis.com',
  openrouter: 'https://openrouter.ai/api/v1',
})

const expectedProviderSecret: Readonly<Record<ProviderId, SecretReference | null>> = Object.freeze({
  ollama: null,
  openai: 'openai.api_key',
  anthropic: 'anthropic.api_key',
  gemini: 'gemini.api_key',
  openrouter: 'openrouter.api_key',
})

function endpointUsesExplicitLoopback(endpoint: string): boolean {
  const hostname = endpointHostname(endpoint)
  if (hostname === undefined) return false
  if (hostname === 'localhost' || hostname === '::1') return true
  const octets = hostname.split('.')
  return octets.length === 4
    && octets[0] === '127'
    && octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255)
}

function parseProviderProfile(value: unknown): ProviderProfileSummary {
  const message = 'Invalid provider profile response'
  if (!record(value) || !exactKeys(value, [
    'name', 'provider_id', 'model', 'endpoint', 'endpoint_kind', 'secret_reference', 'enabled',
  ]) || typeof value.enabled !== 'boolean'
    || (value.endpoint_kind !== 'loopback' && value.endpoint_kind !== 'remote')) invalidResponse()
  try {
    const providerId = parseProviderId(value.provider_id, message)
    const endpoint = parseEndpoint(value.endpoint, message)
    const secretReference = value.secret_reference === null
      ? null
      : parseSecretReference(value.secret_reference)
    if (secretReference !== expectedProviderSecret[providerId]) invalidResponse()
    if (providerId === 'ollama') {
      if (value.endpoint_kind !== 'loopback' || !endpointUsesExplicitLoopback(endpoint)) invalidResponse()
    } else if (value.endpoint_kind !== 'remote' || endpoint !== expectedProviderEndpoint[providerId]) {
      invalidResponse()
    }
    return {
      name: parseProfileName(value.name, message),
      provider_id: providerId,
      model: parseModel(value.model, message),
      endpoint,
      endpoint_kind: value.endpoint_kind,
      secret_reference: secretReference,
      enabled: value.enabled,
    }
  } catch {
    invalidResponse()
  }
}

function uniqueValues(values: readonly string[]): boolean {
  return new Set(values).size === values.length
}

function parseConsentGrant(value: unknown): ConsentGrantSummary {
  const message = 'Invalid consent response'
  if (!record(value) || !exactKeys(value, [
    'name', 'provider_profile_name', 'provider_id', 'modules', 'purposes', 'data_classes',
    'models', 'max_cost_usd', 'retain_payloads', 'active',
  ]) || typeof value.retain_payloads !== 'boolean' || typeof value.active !== 'boolean') invalidResponse()
  try {
    const modules = parseSafeCodes(value.modules, message)
    const purposes = parseSafeCodes(value.purposes, message)
    const dataClasses = parseDataClasses(value.data_classes, message)
    const models = parseModels(value.models, message)
    if (!uniqueValues(modules) || !uniqueValues(purposes)
      || !uniqueValues(dataClasses) || !uniqueValues(models)) invalidResponse()
    return {
      name: parseProfileName(value.name, message),
      provider_profile_name: parseProfileName(value.provider_profile_name, message),
      provider_id: parseProviderId(value.provider_id, message),
      modules,
      purposes,
      data_classes: dataClasses,
      models,
      max_cost_usd: parseCost(value.max_cost_usd, message),
      retain_payloads: value.retain_payloads,
      active: value.active,
    }
  } catch {
    invalidResponse()
  }
}

function parseProviderConfiguration(value: unknown): ProviderConfiguration {
  if (!record(value) || !exactKeys(value, ['schema_version', 'revision', 'profiles', 'consents'])
    || value.schema_version !== 1 || !Array.isArray(value.profiles) || value.profiles.length > 256
    || !Array.isArray(value.consents) || value.consents.length > 256) invalidResponse()
  const profiles = value.profiles.map(parseProviderProfile)
  const consents = value.consents.map(parseConsentGrant)
  const profilesByName = new Map(profiles.map((profile) => [profile.name, profile]))
  if (profilesByName.size !== profiles.length || new Set(consents.map((consent) => consent.name)).size !== consents.length) {
    invalidResponse()
  }
  if (consents.some((consent) => {
    const profile = profilesByName.get(consent.provider_profile_name)
    return profile === undefined || profile.provider_id !== consent.provider_id
  })) invalidResponse()
  return deepFreeze({
    schema_version: 1,
    revision: parseRevision(value.revision, 'Invalid provider configuration response'),
    profiles,
    consents,
  })
}

function parseProviderEndpointValidation(value: unknown): ProviderEndpointValidation {
  if (!record(value) || !exactKeys(value, [
    'schema_version', 'status', 'endpoint_kind', 'http_status', 'destination_digest',
  ]) || value.schema_version !== 1 || value.status !== 'reachable'
    || (value.endpoint_kind !== 'loopback' && value.endpoint_kind !== 'remote')
    || !integer(value.http_status, 100, 599)
    || typeof value.destination_digest !== 'string' || !digestPattern.test(value.destination_digest)) {
    invalidResponse()
  }
  return deepFreeze(value as unknown as ProviderEndpointValidation)
}

function parseConsentPreview(value: unknown): ConsentPreview {
  try {
    return parseConsentPreviewPayload(value, 'Invalid consent preview response')
  } catch {
    invalidResponse()
  }
}

function expectedGrantShape(purpose: FileGrantPurpose): Readonly<{ access: 'read' | 'write'; format: FileFormat }> {
  switch (purpose) {
    case 'gedcom-read': return { access: 'read', format: 'gedcom' }
    case 'rootsmagic-read': return { access: 'read', format: 'rootsmagic' }
    case 'gedcom-write': return { access: 'write', format: 'gedcom' }
    case 'json-write': return { access: 'write', format: 'json' }
    case 'markdown-write': return { access: 'write', format: 'markdown' }
  }
}

function parseFileGrant(value: unknown): FileGrant {
  if (!record(value) || !exactKeys(value, ['grantId', 'purpose', 'access', 'scope', 'metadata'])
    || !fileGrantIdPattern.test(String(value.grantId))
    || !fileGrantPurposes.includes(value.purpose as FileGrantPurpose)
    || !record(value.scope)
    || !exactKeys(value.scope, ['originatingWindow', 'lifetime', 'redemption'])
    || value.scope.originatingWindow !== 'requesting-window'
    || value.scope.lifetime !== 'app-session'
    || value.scope.redemption !== 'single-use'
    || !record(value.metadata)
    || !exactKeys(value.metadata, ['displayName', 'format', 'sizeBytes', 'validation'])
    || !bounded(value.metadata.displayName, 1, 255)
    || !safeDisplayNamePattern.test(value.metadata.displayName)
    || !fileFormats.includes(value.metadata.format as FileFormat)
    || !integer(value.metadata.sizeBytes, 0, Number.MAX_SAFE_INTEGER)
    || !fileValidations.includes(value.metadata.validation as FileValidation)) invalidResponse()
  const shape = expectedGrantShape(value.purpose as FileGrantPurpose)
  if (value.access !== shape.access || value.metadata.format !== shape.format) invalidResponse()
  if (shape.access === 'read' && value.metadata.validation !== 'validated-input') invalidResponse()
  if (shape.access === 'write' && value.metadata.validation === 'validated-input') invalidResponse()
  return value as unknown as FileGrant
}

export function parseArtifactRef(value: unknown): Readonly<ArtifactRef> {
  if (!record(value) || !exactKeys(value, ['artifact_id', 'artifact_type', 'media_type', 'sha256', 'size_bytes', 'status'])
    || typeof value.artifact_id !== 'string' || !artifactIdPattern.test(value.artifact_id)
    || !bounded(value.artifact_type, 1, 96) || !identifierPattern.test(value.artifact_type)
    || !bounded(value.media_type, 3, 127) || !mediaTypePattern.test(value.media_type)
    || typeof value.sha256 !== 'string' || !digestPattern.test(value.sha256)
    || !integer(value.size_bytes, 0, Number.MAX_SAFE_INTEGER)
    || (value.status !== 'staged' && value.status !== 'published')) invalidResponse()
  return deepFreeze(value as unknown as ArtifactRef)
}

function parseNullableFileGrant(value: unknown): FileGrant | null {
  return value === null ? null : parseFileGrant(value)
}

function parseFileGrantRevocation(value: unknown): FileGrantRevocation {
  if (!record(value) || !exactKeys(value, ['revoked']) || value.revoked !== true) invalidResponse()
  return value as unknown as FileGrantRevocation
}

function parseLocalRuntimeStatus(value: unknown): LocalRuntimeStatus {
  if (
    !record(value)
    || !exactKeys(value, [
      'schema_version',
      'state',
      'code',
      'supported',
      'host',
      'allocation',
      'components',
      'vm_image',
    ])
    || value.schema_version !== 1
    || !localRuntimeStates.includes(value.state as typeof localRuntimeStates[number])
    || !bounded(value.code, 1, 64)
    || !identifierPattern.test(value.code)
    || typeof value.supported !== 'boolean'
    || !record(value.host)
    || !exactKeys(value.host, [
      'operating_system',
      'architecture',
      'macos_major',
      'virtualization',
      'free_space',
      'existing_docker_contexts',
    ])
    || (value.host.operating_system !== 'macos' && value.host.operating_system !== 'unsupported')
    || !bounded(value.host.architecture, 1, 24)
    || !identifierPattern.test(value.host.architecture)
    || !integer(value.host.macos_major, 0, 99)
    || (value.host.virtualization !== 'available' && value.host.virtualization !== 'unavailable')
    || (value.host.free_space !== 'sufficient' && value.host.free_space !== 'insufficient')
    || !integer(value.host.existing_docker_contexts, 0, 10_000)
    || !record(value.allocation)
    || !exactKeys(value.allocation, ['cpus', 'memory_gib', 'disk_gib'])
    || !integer(value.allocation.cpus, 1, 64)
    || !integer(value.allocation.memory_gib, 1, 512)
    || !integer(value.allocation.disk_gib, 1, 4096)
    || !Array.isArray(value.components)
    || value.components.length !== localRuntimeComponentNames.length
    || !record(value.vm_image)
    || !exactKeys(value.vm_image, ['version', 'installed'])
    || !bounded(value.vm_image.version, 1, 32)
    || !identifierPattern.test(value.vm_image.version)
    || typeof value.vm_image.installed !== 'boolean'
  ) invalidResponse()
  const seen = new Set<string>()
  for (const component of value.components) {
    if (
      !record(component)
      || !exactKeys(component, ['name', 'version', 'installed'])
      || !localRuntimeComponentNames.includes(
        component.name as typeof localRuntimeComponentNames[number],
      )
      || seen.has(String(component.name))
      || !bounded(component.version, 1, 32)
      || !identifierPattern.test(component.version)
      || typeof component.installed !== 'boolean'
    ) invalidResponse()
    seen.add(String(component.name))
  }
  return deepFreeze(value as unknown as LocalRuntimeStatus)
}

function parseLocalRuntimeReview(
  value: unknown,
  status: LocalRuntimeStatus,
): LocalRuntimeReview {
  if (
    !record(value)
    || !exactKeys(value, ['artifacts', 'vm_image', 'ownership', 'isolation'])
    || !Array.isArray(value.artifacts)
    || value.artifacts.length !== localRuntimeComponentNames.length
  ) invalidResponse()

  const statusVersions = new Map(status.components.map((component) => [component.name, component.version]))
  const seen = new Set<string>()
  for (const artifact of value.artifacts) {
    if (
      !record(artifact)
      || !exactKeys(artifact, [
        'name',
        'version',
        'repository',
        'asset_name',
        'source_url',
        'sha256',
        'size_bytes',
        'license',
        'license_url',
        'license_sha256',
      ])
      || !localRuntimeComponentNames.includes(
        artifact.name as typeof localRuntimeComponentNames[number],
      )
      || seen.has(String(artifact.name))
      || !bounded(artifact.version, 1, 32)
      || !identifierPattern.test(artifact.version)
      || statusVersions.get(String(artifact.name)) !== artifact.version
      || artifact.repository !== localRuntimeComponentRepositories[
        artifact.name as typeof localRuntimeComponentNames[number]
      ]
      || !bounded(artifact.asset_name, 1, 160)
      || !localRuntimeAssetPattern.test(artifact.asset_name)
      || typeof artifact.source_url !== 'string'
      || typeof artifact.sha256 !== 'string'
      || !digestPattern.test(artifact.sha256)
      || !integer(artifact.size_bytes, 1, 1024 * 1024 * 1024)
      || (artifact.license !== 'Apache-2.0' && artifact.license !== 'MIT')
      || typeof artifact.license_url !== 'string'
      || typeof artifact.license_sha256 !== 'string'
      || !digestPattern.test(artifact.license_sha256)
    ) invalidResponse()

    const name = artifact.name as typeof localRuntimeComponentNames[number]
    const repository = localRuntimeComponentRepositories[name]
    const releaseUrl = `https://github.com/${repository}/releases/download/v${artifact.version}/${artifact.asset_name}`
    const dockerUrl = `https://download.docker.com/mac/static/stable/aarch64/${artifact.asset_name}`
    if (
      artifact.source_url !== releaseUrl
      && !(name === 'docker-cli' && artifact.source_url === dockerUrl)
    ) invalidResponse()
    const permittedLicenseUrls = [
      `https://raw.githubusercontent.com/${repository}/v${artifact.version}/LICENSE`,
      `https://raw.githubusercontent.com/${repository}/v${artifact.version}/LICENSE.md`,
      `https://raw.githubusercontent.com/${repository}/v${artifact.version}/LICENSE.txt`,
    ]
    if (!permittedLicenseUrls.includes(artifact.license_url)) invalidResponse()
    seen.add(name)
  }

  const vmImage = value.vm_image
  if (
    !record(vmImage)
    || !exactKeys(vmImage, [
      'version',
      'repository',
      'asset_name',
      'source_url',
      'sha256',
      'size_bytes',
    ])
    || !bounded(vmImage.version, 1, 32)
    || !identifierPattern.test(vmImage.version)
    || vmImage.version !== status.vm_image.version
    || vmImage.repository !== 'abiosoft/colima-core'
    || vmImage.asset_name !== 'ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz'
    || vmImage.source_url !== `https://github.com/abiosoft/colima-core/releases/download/v${vmImage.version}/${vmImage.asset_name}`
    || typeof vmImage.sha256 !== 'string'
    || !digestPattern.test(vmImage.sha256)
    || !integer(vmImage.size_bytes, 1, 1024 * 1024 * 1024)
  ) invalidResponse()

  const ownership = value.ownership
  if (
    !record(ownership)
    || !exactKeys(ownership, ['profile', 'context'])
    || ownership.profile !== 'ancestryllm-local-arm64'
    || ownership.context !== 'colima-ancestryllm-local-arm64'
  ) invalidResponse()

  const isolation = value.isolation
  if (
    !record(isolation)
    || !exactKeys(isolation, [
      'loopback_only',
      'kubernetes',
      'privileged_containers',
      'renderer_socket_access',
      'container_socket_access',
      'cross_profile_socket_access',
    ])
    || isolation.loopback_only !== true
    || isolation.kubernetes !== false
    || isolation.privileged_containers !== false
    || isolation.renderer_socket_access !== false
    || isolation.container_socket_access !== false
    || isolation.cross_profile_socket_access !== false
  ) invalidResponse()

  return deepFreeze(value as unknown as LocalRuntimeReview)
}

function parseLocalRuntimePreview(value: unknown): LocalRuntimePreview {
  if (
    !record(value)
    || !exactKeys(value, [
      'schema_version',
      'operation',
      'offline',
      'actions',
      'confirmation_phrase',
      'preserves_data',
      'deletes_data',
      'plan_revision',
      'status',
      'review',
    ])
    || value.schema_version !== 1
    || typeof value.offline !== 'boolean'
    || !Array.isArray(value.actions)
    || value.actions.length < 1
    || value.actions.length > 8
    || typeof value.preserves_data !== 'boolean'
    || typeof value.deletes_data !== 'boolean'
    || typeof value.plan_revision !== 'string'
    || !digestPattern.test(value.plan_revision)
  ) invalidResponse()
  const operation = (() => {
    try { return parseLocalRuntimeOperation(value.operation) } catch { return invalidResponse() }
  })()
  if (
    value.confirmation_phrase !== localRuntimeConfirmations[operation]
    || value.deletes_data !== (operation === 'uninstall-delete')
    || value.preserves_data === value.deletes_data
  ) invalidResponse()
  for (const action of value.actions) {
    if (
      !record(action)
      || !exactKeys(action, ['code'])
      || !bounded(action.code, 1, 64)
      || !identifierPattern.test(action.code)
    ) invalidResponse()
  }
  const status = parseLocalRuntimeStatus(value.status)
  parseLocalRuntimeReview(value.review, status)
  return deepFreeze(value as unknown as LocalRuntimePreview)
}

function parseLocalRuntimeOutcome(value: unknown): LocalRuntimeResult {
  if (
    !record(value)
    || !exactKeys(value, ['schema_version', 'operation', 'state', 'code'])
    || value.schema_version !== 1
    || !localRuntimeStates.includes(value.state as typeof localRuntimeStates[number])
    || !bounded(value.code, 1, 64)
    || !identifierPattern.test(value.code)
  ) invalidResponse()
  try { parseLocalRuntimeOperation(value.operation) } catch { invalidResponse() }
  return deepFreeze(value as unknown as LocalRuntimeResult)
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
export const parseSettingsResult = (value: unknown): BridgeResult<ApplicationSettings> => parseBridgeResult(value, parseSettings)
export const parseSecretStatusResult = (value: unknown): BridgeResult<SecretStatus> => parseBridgeResult(value, parseSecretStatus)
export const parseProviderConfigurationResult = (value: unknown): BridgeResult<ProviderConfiguration> => parseBridgeResult(value, parseProviderConfiguration)
export const parseProviderEndpointValidationResult = (value: unknown): BridgeResult<ProviderEndpointValidation> => parseBridgeResult(value, parseProviderEndpointValidation)
export const parseConsentPreviewResult = (value: unknown): BridgeResult<ConsentPreview> => parseBridgeResult(value, parseConsentPreview)
export const parseFileGrantResult = (value: unknown): BridgeResult<FileGrant | null> => parseBridgeResult(value, parseNullableFileGrant)
export const parseFileGrantRevocationResult = (value: unknown): BridgeResult<FileGrantRevocation> => parseBridgeResult(value, parseFileGrantRevocation)
export const parseLocalRuntimeStatusResult = (value: unknown): BridgeResult<LocalRuntimeStatus> => parseBridgeResult(value, parseLocalRuntimeStatus)
export const parseLocalRuntimePreviewResult = (value: unknown): BridgeResult<LocalRuntimePreview> => parseBridgeResult(value, parseLocalRuntimePreview)
export const parseLocalRuntimeResult = (value: unknown): BridgeResult<LocalRuntimeResult> => parseBridgeResult(value, parseLocalRuntimeOutcome)
export const parseChatStreamRunResult = (value: unknown): BridgeResult<ChatStreamRun> => parseBridgeResult(value, parseChatStreamRun)
export const parseChatEventResult = (value: unknown): BridgeResult<ChatEvent> => parseBridgeResult(value, parseChatEvent)
export const parseChatStreamAcknowledgementResult = (value: unknown): BridgeResult<ChatStreamAcknowledgement> => parseBridgeResult(value, parseChatStreamAcknowledgement)
export const parseJobListResult = (value: unknown): BridgeResult<JobList> => parseBridgeResult(value, parseJobList)
export const parseJobSnapshotResult = (value: unknown): BridgeResult<JobSnapshot> => parseBridgeResult(value, parseJobSnapshot)
export const parseJobEventResult = (value: unknown): BridgeResult<JobEvent> => parseBridgeResult(value, parseJobEvent)
export const parseJobEventSubscriptionResult = (value: unknown): BridgeResult<JobEventSubscription> => parseBridgeResult(value, parseJobEventSubscription)
export const parseJobEventUnsubscriptionResult = (value: unknown): BridgeResult<JobEventUnsubscription> => parseBridgeResult(value, parseJobEventUnsubscription)
