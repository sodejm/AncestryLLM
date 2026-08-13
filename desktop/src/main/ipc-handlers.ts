/** Registers a bounded, exact-owner desktop IPC bridge. */
import {
  DESKTOP_PROTOCOL_VERSION,
  desktopChannels,
  desktopEventChannels,
  type AncestryBridge,
  type ApplicationSettings,
  type ApplicationSettingsPatch,
  type BridgeErrorCode,
  type BridgeResult,
  type CapabilityManifest,
  type ConsentCreateRequest,
  type ConsentPreview,
  type ConsentPreviewRequest,
  type ConsentRevokeRequest,
  type FileGrant,
  type FileGrantId,
  type FileGrantRevocation,
  type LocalPreferences,
  type LocalRuntimeApplyRequest,
  type LocalRuntimePreview,
  type LocalRuntimeRequest,
  type LocalRuntimeResult,
  type JobEvent,
  type JobEventSubscriptionRequest,
  type JobEventUnsubscriptionRequest,
  type JobRequest,
  type JobSnapshot,
  type OpenFileGrantRequest,
  type PreferenceUpdate,
  type ProviderConfiguration,
  type ProviderEndpointValidation,
  type ProviderEndpointValidationRequest,
  type ProviderProfileCreateRequest,
  type SaveFileGrantRequest,
  type SecretReferenceRequest,
  type SecretSetRequest,
  type SecretStatus,
} from '../shared-contract/desktop'
import {
  parseAppInfoResult,
  parseCapabilitiesResult,
  parseConsentCreateRequest,
  parseConsentPreviewRequest,
  parseConsentPreviewResult,
  parseConsentRevokeRequest,
  parseFileGrantId,
  parseFileGrantResult,
  parseFileGrantRevocationResult,
  parseLocalRuntimeApplyRequest,
  parseLocalRuntimePreviewResult,
  parseLocalRuntimeRequest,
  parseLocalRuntimeResult,
  parseLocalRuntimeStatusResult,
  parseJobEventDelivery,
  parseJobEventSubscriptionRequest,
  parseJobEventSubscriptionResult,
  parseJobEventUnsubscriptionRequest,
  parseJobEventUnsubscriptionResult,
  parseJobListResult,
  parseJobRequest,
  parseJobSnapshotResult,
  parseOpenFileGrantRequest,
  parsePreferenceUpdate,
  parsePreferencesResult,
  parseProviderConfigurationResult,
  parseProviderEndpointValidationRequest,
  parseProviderEndpointValidationResult,
  parseProviderProfileCreateRequest,
  parseSaveFileGrantRequest,
  parseSecretReferenceRequest,
  parseSecretSetRequest,
  parseSecretStatusResult,
  parseSettingsPatch,
  parseSettingsResult,
  parseStartupDiagnosticsResult,
} from '../shared-contract/runtime'
import { FileGrantBrokerError } from './file-grant-broker'
import { validateStructuredClone } from './structured-clone-policy'

type IpcHandler = (event: unknown, ...args: unknown[]) => Promise<unknown>
export interface IpcRegistrar { handle(channel: string, handler: IpcHandler): void }

export interface BridgeFrame { readonly url: string }
export interface BridgeWebContents {
  readonly mainFrame: BridgeFrame
  isDestroyed(): boolean
  send(channel: string, ...args: unknown[]): void
  on(event: string, listener: (...args: unknown[]) => void): unknown
  removeListener(event: string, listener: (...args: unknown[]) => void): unknown
}

export interface MainDesktopBridge extends Omit<
  AncestryBridge,
  | 'getProviderConfiguration'
  | 'createProviderProfile'
  | 'validateProviderEndpoint'
  | 'previewConsent'
  | 'createConsent'
  | 'revokeConsent'
  | 'requestOpenFileGrant'
  | 'requestSaveFileGrant'
  | 'revokeFileGrant'
  | 'getLocalRuntimeStatus'
  | 'previewLocalRuntime'
  | 'applyLocalRuntime'
  | 'listJobs'
  | 'getJob'
  | 'cancelJob'
  | 'subscribeJobEvents'
  | 'unsubscribeJobEvents'
  | 'onJobEvent'
> {
  getAppInfo(signal?: AbortSignal): ReturnType<AncestryBridge['getAppInfo']>
  getStartupDiagnostics(signal?: AbortSignal): ReturnType<AncestryBridge['getStartupDiagnostics']>
  getCapabilities(signal?: AbortSignal): ReturnType<AncestryBridge['getCapabilities']>
  retrySidecar(signal?: AbortSignal): ReturnType<AncestryBridge['retrySidecar']>
  getPreferences(signal?: AbortSignal): ReturnType<AncestryBridge['getPreferences']>
  updatePreferences(update: PreferenceUpdate, signal?: AbortSignal): ReturnType<AncestryBridge['updatePreferences']>
  getSettings(signal?: AbortSignal): ReturnType<AncestryBridge['getSettings']>
  updateSettings(update: ApplicationSettingsPatch, signal?: AbortSignal): ReturnType<AncestryBridge['updateSettings']>
  getSecretStatus(request: SecretReferenceRequest, signal?: AbortSignal): ReturnType<AncestryBridge['getSecretStatus']>
  setSecret(request: SecretSetRequest, signal?: AbortSignal): ReturnType<AncestryBridge['setSecret']>
  deleteSecret(request: SecretReferenceRequest, signal?: AbortSignal): ReturnType<AncestryBridge['deleteSecret']>
  getProviderConfiguration(signal?: AbortSignal): ReturnType<AncestryBridge['getProviderConfiguration']>
  createProviderProfile(
    request: ProviderProfileCreateRequest,
    signal?: AbortSignal,
  ): ReturnType<AncestryBridge['createProviderProfile']>
  validateProviderEndpoint(
    request: ProviderEndpointValidationRequest,
    signal?: AbortSignal,
  ): ReturnType<AncestryBridge['validateProviderEndpoint']>
  previewConsent(
    request: ConsentPreviewRequest,
    signal?: AbortSignal,
  ): ReturnType<AncestryBridge['previewConsent']>
  createConsent(
    request: ConsentCreateRequest,
    signal?: AbortSignal,
  ): ReturnType<AncestryBridge['createConsent']>
  revokeConsent(
    request: ConsentRevokeRequest,
    signal?: AbortSignal,
  ): ReturnType<AncestryBridge['revokeConsent']>
  getLocalRuntimeStatus(signal?: AbortSignal): ReturnType<AncestryBridge['getLocalRuntimeStatus']>
  previewLocalRuntime(
    request: LocalRuntimeRequest,
    signal?: AbortSignal,
  ): ReturnType<AncestryBridge['previewLocalRuntime']>
  applyLocalRuntime(
    request: LocalRuntimeApplyRequest,
    signal?: AbortSignal,
  ): ReturnType<AncestryBridge['applyLocalRuntime']>
  listJobs(signal?: AbortSignal): ReturnType<AncestryBridge['listJobs']>
  getJob(request: JobRequest, signal?: AbortSignal): ReturnType<AncestryBridge['getJob']>
  cancelJob(request: JobRequest, signal?: AbortSignal): ReturnType<AncestryBridge['cancelJob']>
  streamJobEvents(
    request: JobEventSubscriptionRequest,
    listener: (event: Readonly<JobEvent>) => void,
    signal?: AbortSignal,
  ): Promise<void>
}

export interface MainFileGrantBroker {
  requestOpenGrant(
    owner: object,
    request: OpenFileGrantRequest,
    signal?: AbortSignal,
  ): Promise<Readonly<FileGrant> | null>
  requestSaveGrant(
    owner: object,
    request: SaveFileGrantRequest,
    signal?: AbortSignal,
  ): Promise<Readonly<FileGrant> | null>
  revokeGrant(owner: object, grantId: string): Readonly<FileGrantRevocation>
  revokeOwner(owner: object): void
  revokeAll(): void
  dispose(): void
}

export interface DesktopIpcController {
  authorizeWebContents(
    contents: BridgeWebContents,
    trustedUrl: (url: string) => boolean,
  ): () => void
  invalidateSidecarSession(): void
  dispose(): void
}

export interface RegistrationOptions {
  readonly operationTimeoutMs?: number
  readonly fileDialogTimeoutMs?: number
  readonly runtimeOperationTimeoutMs?: number
}
interface Authorization {
  readonly contents: BridgeWebContents
  readonly trustedUrl: (url: string) => boolean
  readonly controllers: Set<AbortController>
  readonly queue: QueueEntry[]
  readonly jobSubscriptions: Map<string, JobSubscription>
  readonly removeLifecycleListeners: () => void
  active: number
  generation: number
  navigating: boolean
  capabilityFlight?: CapabilityFlight
}
interface JobSubscription {
  readonly controller: AbortController
  readonly generation: number
  readonly request: JobEventSubscriptionRequest
  lastSequence: number
  terminalSeen: boolean
}
interface NavigationStartDetails {
  readonly isMainFrame: boolean
  readonly isSameDocument: boolean
}
interface CapabilityFlight {
  readonly generation: number
  readonly promise: Promise<BridgeResult<CapabilityManifest>>
  subscribers: number
}

function parseNavigationStartDetails(value: unknown): NavigationStartDetails | undefined {
  if (typeof value !== 'object' || value === null) return undefined
  try {
    const candidate = value as Readonly<Record<'isMainFrame' | 'isSameDocument', unknown>>
    if (typeof candidate.isMainFrame !== 'boolean' || typeof candidate.isSameDocument !== 'boolean') {
      return undefined
    }
    return Object.freeze({
      isMainFrame: candidate.isMainFrame,
      isSameDocument: candidate.isSameDocument,
    })
  } catch {
    return undefined
  }
}
interface QueueEntry {
  readonly generation: number
  start(): void
  cancel(): void
}
interface OperationRun<T> {
  readonly response: Promise<BridgeResult<T>>
  readonly settled: Promise<void>
}

const MAX_ACTIVE_REQUESTS = 4
const MAX_QUEUED_REQUESTS = 8
const MAX_CAPABILITY_SUBSCRIBERS = 32
const MAX_JOB_SUBSCRIPTIONS = 32
const DEFAULT_OPERATION_TIMEOUT_MS = 5_000
const DEFAULT_FILE_DIALOG_TIMEOUT_MS = 300_000
const DEFAULT_RUNTIME_OPERATION_TIMEOUT_MS = 30 * 60 * 1000
const CANCELLED = Symbol('bridge-request-cancelled')
const TIMED_OUT = Symbol('bridge-request-timed-out')

const requestLimits = Object.freeze({
  maxBytes: 8_192,
  maxDepth: 4,
  maxItems: 32,
  maxStringCharacters: 256,
})
const secretRequestLimits = Object.freeze({
  maxBytes: 66_000,
  maxDepth: 3,
  maxItems: 4,
  maxStringCharacters: 65_536,
})
const providerRequestLimits = Object.freeze({
  maxBytes: 65_600,
  maxDepth: 5,
  maxItems: 512,
  maxStringCharacters: 2_048,
})
const responseLimits = Object.freeze({
  maxBytes: 1_100_000,
  maxDepth: 10,
  maxItems: 70_000,
  maxStringCharacters: 1_048_576,
})

function error<T>(
  code: BridgeErrorCode,
  message: string,
  remediation: string,
): BridgeResult<T> {
  return Object.freeze({
    ok: false,
    protocolVersion: DESKTOP_PROTOCOL_VERSION,
    error: Object.freeze({ code, message, remediation }),
  })
}

const unauthorized = <T>(): BridgeResult<T> =>
  error('UNAUTHORIZED_SENDER', 'The desktop request was denied.', 'Reload the AncestryLLM window.')
const invalidRequest = <T>(): BridgeResult<T> =>
  error('INVALID_REQUEST', 'The desktop request was invalid.', 'Reload the AncestryLLM window and try again.')
const invalidResponse = <T>(): BridgeResult<T> =>
  error('INVALID_RESPONSE', 'The desktop response was invalid.', 'Restart AncestryLLM.')
const internalError = <T>(): BridgeResult<T> =>
  error('INTERNAL_ERROR', 'The desktop request could not be completed.', 'Try again or restart AncestryLLM.')
const overloaded = <T>(): BridgeResult<T> =>
  error('BRIDGE_OVERLOADED', 'The desktop request queue is full.', 'Wait for current desktop requests to finish and try again.')
const cancelled = <T>(): BridgeResult<T> =>
  error('REQUEST_CANCELLED', 'The desktop request was cancelled.', 'Retry from the current AncestryLLM window.')
const timedOut = <T>(): BridgeResult<T> =>
  error('REQUEST_TIMEOUT', 'The desktop request timed out.', 'Try again or restart AncestryLLM.')

function success<T>(data: T): BridgeResult<T> {
  return Object.freeze({ ok: true, protocolVersion: DESKTOP_PROTOCOL_VERSION, data })
}

function fileGrantFailure<T>(cause: unknown): BridgeResult<T> {
  if (!(cause instanceof FileGrantBrokerError)) return internalError<T>()
  switch (cause.code) {
    case 'FILE_SELECTION_INVALID':
      return error(cause.code, 'The selected file is not valid for this operation.', 'Choose a supported regular file and try again.')
    case 'FILE_TOO_LARGE':
      return error(cause.code, 'The selected file exceeds the supported size limit.', 'Choose a smaller file and try again.')
    case 'FILE_GRANT_FORBIDDEN':
      return error(cause.code, 'This file permission cannot be used for that operation.', 'Select the file again for the requested operation.')
    case 'FILE_GRANT_REVOKED':
      return error(cause.code, 'This file permission is no longer available.', 'Select the file again and retry the operation.')
    case 'FILE_GRANT_STALE':
      return error(cause.code, 'The selected file changed after it was approved.', 'Review and select the file again.')
    case 'FILE_GRANT_CONFLICT':
      return error(cause.code, 'The selected file is already in use by another operation.', 'Finish or cancel the other operation and try again.')
    case 'FILE_DIALOG_FAILED':
      return error(cause.code, 'The system file dialog could not complete the request.', 'Try again or restart AncestryLLM.')
  }
}

async function fileGrantOperation<T>(operation: () => Promise<T> | T): Promise<BridgeResult<T>> {
  try {
    return success(await operation())
  } catch (cause) {
    return fileGrantFailure<T>(cause)
  }
}

function eventParts(event: unknown): Readonly<{ sender: unknown; senderFrame: unknown }> | undefined {
  if (typeof event !== 'object' || event === null) return undefined
  try {
    const candidate = event as Readonly<{ sender?: unknown; senderFrame?: unknown }>
    return { sender: candidate.sender, senderFrame: candidate.senderFrame }
  } catch {
    return undefined
  }
}

function safeResponse<T>(
  response: unknown,
  parse: (value: unknown) => BridgeResult<T>,
): BridgeResult<T> {
  try {
    validateStructuredClone(response, responseLimits)
    return parse(response)
  } catch {
    return invalidResponse<T>()
  }
}

function runOperation<T>(
  state: Authorization,
  timeoutMs: number,
  operation: (signal: AbortSignal) => Promise<unknown>,
  parse: (value: unknown) => BridgeResult<T>,
): OperationRun<T> {
  const controller = new AbortController()
  state.controllers.add(controller)
  const timeout = setTimeout(() => controller.abort(TIMED_OUT), timeoutMs)
  const stopped = new Promise<BridgeResult<T>>((resolve) => {
    controller.signal.addEventListener('abort', () => {
      resolve(controller.signal.reason === TIMED_OUT ? timedOut<T>() : cancelled<T>())
    }, { once: true })
  })
  let pending: Promise<unknown>
  try {
    pending = operation(controller.signal)
  } catch (operationError) {
    pending = Promise.reject(operationError)
  }
  const completed = pending.then(
      (response) => safeResponse(response, parse),
      () => internalError<T>(),
    )
  const settled = completed.then(() => undefined).finally(() => {
    clearTimeout(timeout)
    state.controllers.delete(controller)
  })
  return {
    response: Promise.race([completed, stopped]),
    settled,
  }
}

function pump(state: Authorization): void {
  while (state.active < MAX_ACTIVE_REQUESTS) {
    const entry = state.queue.shift()
    if (!entry) return
    if (entry.generation !== state.generation) {
      entry.cancel()
      continue
    }
    entry.start()
  }
}

function schedule<T>(
  state: Authorization,
  timeoutMs: number,
  operation: (signal: AbortSignal) => Promise<unknown>,
  parse: (value: unknown) => BridgeResult<T>,
): Promise<BridgeResult<T>> {
  if (state.active >= MAX_ACTIVE_REQUESTS && state.queue.length >= MAX_QUEUED_REQUESTS) {
    return Promise.resolve(overloaded<T>())
  }
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs
    let finished = false
    let queueTimeout: ReturnType<typeof setTimeout> | undefined
    const finish = (result: BridgeResult<T>): void => {
      if (finished) return
      finished = true
      if (queueTimeout) clearTimeout(queueTimeout)
      resolve(result)
    }
    const entry: QueueEntry = {
      generation: state.generation,
      cancel: () => finish(cancelled<T>()),
      start: () => {
        if (finished) return
        if (queueTimeout) clearTimeout(queueTimeout)
        state.active += 1
        const run = runOperation(
          state,
          Math.max(1, deadline - Date.now()),
          operation,
          parse,
        )
        void run.response.then(finish)
        void run.settled
          .finally(() => {
            state.active -= 1
            pump(state)
          })
      },
    }
    if (state.active < MAX_ACTIVE_REQUESTS) {
      entry.start()
    } else {
      state.queue.push(entry)
      queueTimeout = setTimeout(() => {
        const queuedIndex = state.queue.indexOf(entry)
        if (queuedIndex >= 0) state.queue.splice(queuedIndex, 1)
        finish(timedOut<T>())
      }, timeoutMs)
    }
  })
}

function invalidate(state: Authorization, notifyJobStreams = false): void {
  const subscriptions = [...state.jobSubscriptions.values()]
  if (notifyJobStreams) {
    for (const subscription of subscriptions) {
      jobStreamFailure(state, subscription, 'JOB_SUBSCRIPTION_CLOSED')
    }
  }
  state.generation += 1
  for (const controller of state.controllers) controller.abort(CANCELLED)
  for (const entry of state.queue.splice(0)) entry.cancel()
  state.jobSubscriptions.clear()
  for (const subscription of subscriptions) subscription.controller.abort(CANCELLED)
}

function jobStreamFailure(
  state: Authorization,
  subscription: JobSubscription,
  code: 'JOB_SUBSCRIPTION_CLOSED' | 'JOB_EVENT_STREAM_FAILED',
): void {
  try {
    if (state.contents.isDestroyed() || state.navigating
      || state.generation !== subscription.generation
      || !state.trustedUrl(state.contents.mainFrame.url)) return
    const diagnostic = code === 'JOB_SUBSCRIPTION_CLOSED'
      ? Object.freeze({
          code,
          message: 'Task updates ended before the task finished.',
          remediation: 'Refresh the task center to reconnect.',
        })
      : Object.freeze({
          code,
          message: 'Task updates could not be verified.',
          remediation: 'Refresh the task center to reconnect.',
        })
    const delivery = parseJobEventDelivery({
      schema_version: 1,
      kind: 'failure',
      subscription_id: subscription.request.subscription_id,
      job_id: subscription.request.job_id,
      event: null,
      error: diagnostic,
    })
    validateStructuredClone(delivery, responseLimits)
    state.contents.send(desktopEventChannels.jobEvent, delivery)
  } catch {
    // A destroyed or replaced renderer must not keep a stream alive.
  }
}

function deliverJobEvent(
  state: Authorization,
  subscription: JobSubscription,
  event: Readonly<JobEvent>,
): void {
  if (state.jobSubscriptions.get(subscription.request.subscription_id) !== subscription
    || state.generation !== subscription.generation) return
  try {
    const delivery = parseJobEventDelivery({
      schema_version: 1,
      kind: 'event',
      subscription_id: subscription.request.subscription_id,
      job_id: subscription.request.job_id,
      event,
      error: null,
    })
    validateStructuredClone(delivery, responseLimits)
    if (delivery.event === null || delivery.event.sequence <= subscription.lastSequence) return
    if (state.contents.isDestroyed() || state.navigating
      || !state.trustedUrl(state.contents.mainFrame.url)) throw new Error('Renderer unavailable')
    state.contents.send(desktopEventChannels.jobEvent, delivery)
    subscription.lastSequence = delivery.event.sequence
    subscription.terminalSeen = event.kind === 'terminal'
    if (subscription.terminalSeen) {
      state.jobSubscriptions.delete(subscription.request.subscription_id)
      subscription.controller.abort(CANCELLED)
    }
  } catch {
    state.jobSubscriptions.delete(subscription.request.subscription_id)
    subscription.controller.abort(CANCELLED)
    jobStreamFailure(state, subscription, 'JOB_EVENT_STREAM_FAILED')
  }
}

function capabilityRequest(
  state: Authorization,
  timeoutMs: number,
  bridge: MainDesktopBridge,
): Promise<BridgeResult<CapabilityManifest>> {
  const existing = state.capabilityFlight
  if (existing?.generation === state.generation) {
    if (existing.subscribers >= MAX_CAPABILITY_SUBSCRIBERS) {
      return Promise.resolve(overloaded())
    }
    existing.subscribers += 1
    return existing.promise
  }
  const promise = schedule(
    state,
    timeoutMs,
    (signal) => bridge.getCapabilities(signal),
    parseCapabilitiesResult,
  )
  const flight: CapabilityFlight = { generation: state.generation, promise, subscribers: 1 }
  state.capabilityFlight = flight
  void promise.then(() => {
    if (state.capabilityFlight === flight) delete state.capabilityFlight
  })
  return promise
}

function removeAuthorization(
  authorizations: Map<BridgeWebContents, Authorization>,
  state: Authorization,
  fileGrants: MainFileGrantBroker,
): void {
  if (authorizations.get(state.contents) !== state) return
  authorizations.delete(state.contents)
  state.removeLifecycleListeners()
  invalidate(state)
  fileGrants.revokeOwner(state.contents)
}

function registerNoArgumentHandler<T>(
  ipc: IpcRegistrar,
  channel: string,
  authorize: (event: unknown) => Authorization | undefined,
  operation: (signal: AbortSignal) => Promise<unknown>,
  parseResponse: (value: unknown) => BridgeResult<T>,
  timeoutMs: number,
): void {
  ipc.handle(channel, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<T>()
    if (args.length !== 0) return invalidRequest<T>()
    return schedule(state, timeoutMs, operation, parseResponse)
  })
}

export function registerDesktopIpcHandlers(
  ipc: IpcRegistrar,
  bridge: MainDesktopBridge,
  fileGrants: MainFileGrantBroker,
  options: Readonly<RegistrationOptions> = {},
): DesktopIpcController {
  const timeoutMs = options.operationTimeoutMs ?? DEFAULT_OPERATION_TIMEOUT_MS
  const fileDialogTimeoutMs = options.fileDialogTimeoutMs ?? DEFAULT_FILE_DIALOG_TIMEOUT_MS
  const runtimeOperationTimeoutMs = options.runtimeOperationTimeoutMs
    ?? DEFAULT_RUNTIME_OPERATION_TIMEOUT_MS
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new Error('Desktop IPC operation timeout must be positive.')
  }
  if (!Number.isFinite(fileDialogTimeoutMs) || fileDialogTimeoutMs <= 0) {
    throw new Error('Desktop IPC file dialog timeout must be positive.')
  }
  if (!Number.isFinite(runtimeOperationTimeoutMs) || runtimeOperationTimeoutMs <= 0) {
    throw new Error('Desktop IPC runtime operation timeout must be positive.')
  }
  const authorizations = new Map<BridgeWebContents, Authorization>()
  let disposed = false

  const authorize = (event: unknown): Authorization | undefined => {
    const parts = eventParts(event)
    if (!parts || typeof parts.sender !== 'object' || parts.sender === null) return undefined
    const state = authorizations.get(parts.sender as BridgeWebContents)
    if (!state) return undefined
    try {
      if (state.contents.isDestroyed() || parts.senderFrame !== state.contents.mainFrame) return undefined
      if (state.navigating) return undefined
      if (!state.trustedUrl(state.contents.mainFrame.url)) return undefined
      return state
    } catch {
      return undefined
    }
  }

  registerNoArgumentHandler(ipc, desktopChannels.getAppInfo, authorize, (signal) => bridge.getAppInfo(signal), parseAppInfoResult, timeoutMs)
  registerNoArgumentHandler(ipc, desktopChannels.getStartupDiagnostics, authorize, (signal) => bridge.getStartupDiagnostics(signal), parseStartupDiagnosticsResult, timeoutMs)
  ipc.handle(desktopChannels.getCapabilities, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<CapabilityManifest>()
    if (args.length !== 0) return invalidRequest<CapabilityManifest>()
    return capabilityRequest(state, timeoutMs, bridge)
  })
  registerNoArgumentHandler(ipc, desktopChannels.retrySidecar, authorize, (signal) => bridge.retrySidecar(signal), parseStartupDiagnosticsResult, timeoutMs)
  registerNoArgumentHandler(ipc, desktopChannels.getPreferences, authorize, (signal) => bridge.getPreferences(signal), parsePreferencesResult, timeoutMs)
  ipc.handle(desktopChannels.updatePreferences, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<LocalPreferences>()
    if (args.length !== 1) return invalidRequest<LocalPreferences>()
    let update: PreferenceUpdate
    try {
      validateStructuredClone(args[0], requestLimits)
      update = parsePreferenceUpdate(args[0])
    } catch {
      return invalidRequest<LocalPreferences>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.updatePreferences(update, signal),
      parsePreferencesResult,
    )
  })
  registerNoArgumentHandler(
    ipc,
    desktopChannels.getSettings,
    authorize,
    (signal) => bridge.getSettings(signal),
    parseSettingsResult,
    timeoutMs,
  )
  ipc.handle(desktopChannels.updateSettings, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<ApplicationSettings>()
    if (args.length !== 1) return invalidRequest<ApplicationSettings>()
    let update: ApplicationSettingsPatch
    try {
      validateStructuredClone(args[0], requestLimits)
      update = parseSettingsPatch(args[0])
    } catch {
      return invalidRequest<ApplicationSettings>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.updateSettings(update, signal),
      parseSettingsResult,
    )
  })
  ipc.handle(desktopChannels.getSecretStatus, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<SecretStatus>()
    if (args.length !== 1) return invalidRequest<SecretStatus>()
    let request: SecretReferenceRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseSecretReferenceRequest(args[0])
    } catch {
      return invalidRequest<SecretStatus>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.getSecretStatus(request, signal),
      parseSecretStatusResult,
    )
  })
  ipc.handle(desktopChannels.setSecret, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<SecretStatus>()
    if (args.length !== 1) return invalidRequest<SecretStatus>()
    let request: SecretSetRequest
    try {
      validateStructuredClone(args[0], secretRequestLimits)
      request = parseSecretSetRequest(args[0])
    } catch {
      return invalidRequest<SecretStatus>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.setSecret(request, signal),
      parseSecretStatusResult,
    )
  })
  ipc.handle(desktopChannels.deleteSecret, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<SecretStatus>()
    if (args.length !== 1) return invalidRequest<SecretStatus>()
    let request: SecretReferenceRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseSecretReferenceRequest(args[0])
    } catch {
      return invalidRequest<SecretStatus>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.deleteSecret(request, signal),
      parseSecretStatusResult,
    )
  })
  registerNoArgumentHandler(
    ipc,
    desktopChannels.getProviderConfiguration,
    authorize,
    (signal) => bridge.getProviderConfiguration(signal),
    parseProviderConfigurationResult,
    timeoutMs,
  )
  ipc.handle(desktopChannels.createProviderProfile, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<ProviderConfiguration>()
    if (args.length !== 1) return invalidRequest<ProviderConfiguration>()
    let request: ProviderProfileCreateRequest
    try {
      validateStructuredClone(args[0], providerRequestLimits)
      request = parseProviderProfileCreateRequest(args[0])
    } catch {
      return invalidRequest<ProviderConfiguration>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.createProviderProfile(request, signal),
      parseProviderConfigurationResult,
    )
  })
  ipc.handle(desktopChannels.validateProviderEndpoint, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<ProviderEndpointValidation>()
    if (args.length !== 1) return invalidRequest<ProviderEndpointValidation>()
    let request: ProviderEndpointValidationRequest
    try {
      validateStructuredClone(args[0], providerRequestLimits)
      request = parseProviderEndpointValidationRequest(args[0])
    } catch {
      return invalidRequest<ProviderEndpointValidation>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.validateProviderEndpoint(request, signal),
      parseProviderEndpointValidationResult,
    )
  })
  ipc.handle(desktopChannels.previewConsent, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<ConsentPreview>()
    if (args.length !== 1) return invalidRequest<ConsentPreview>()
    let request: ConsentPreviewRequest
    try {
      validateStructuredClone(args[0], providerRequestLimits)
      request = parseConsentPreviewRequest(args[0])
    } catch {
      return invalidRequest<ConsentPreview>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.previewConsent(request, signal),
      parseConsentPreviewResult,
    )
  })
  ipc.handle(desktopChannels.createConsent, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<ProviderConfiguration>()
    if (args.length !== 1) return invalidRequest<ProviderConfiguration>()
    let request: ConsentCreateRequest
    try {
      validateStructuredClone(args[0], providerRequestLimits)
      request = parseConsentCreateRequest(args[0])
    } catch {
      return invalidRequest<ProviderConfiguration>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.createConsent(request, signal),
      parseProviderConfigurationResult,
    )
  })
  ipc.handle(desktopChannels.revokeConsent, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<ProviderConfiguration>()
    if (args.length !== 1) return invalidRequest<ProviderConfiguration>()
    let request: ConsentRevokeRequest
    try {
      validateStructuredClone(args[0], providerRequestLimits)
      request = parseConsentRevokeRequest(args[0])
    } catch {
      return invalidRequest<ProviderConfiguration>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.revokeConsent(request, signal),
      parseProviderConfigurationResult,
    )
  })
  ipc.handle(desktopChannels.requestOpenFileGrant, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<FileGrant | null>()
    if (args.length !== 1) return invalidRequest<FileGrant | null>()
    let request: OpenFileGrantRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseOpenFileGrantRequest(args[0])
    } catch {
      return invalidRequest<FileGrant | null>()
    }
    return schedule(
      state,
      fileDialogTimeoutMs,
      (signal) => fileGrantOperation(() => fileGrants.requestOpenGrant(state.contents, request, signal)),
      parseFileGrantResult,
    )
  })
  ipc.handle(desktopChannels.requestSaveFileGrant, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<FileGrant | null>()
    if (args.length !== 1) return invalidRequest<FileGrant | null>()
    let request: SaveFileGrantRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseSaveFileGrantRequest(args[0])
    } catch {
      return invalidRequest<FileGrant | null>()
    }
    return schedule(
      state,
      fileDialogTimeoutMs,
      (signal) => fileGrantOperation(() => fileGrants.requestSaveGrant(state.contents, request, signal)),
      parseFileGrantResult,
    )
  })
  ipc.handle(desktopChannels.revokeFileGrant, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<FileGrantRevocation>()
    if (args.length !== 1) return invalidRequest<FileGrantRevocation>()
    let grantId: FileGrantId
    try {
      validateStructuredClone(args[0], requestLimits)
      grantId = parseFileGrantId(args[0])
    } catch {
      return invalidRequest<FileGrantRevocation>()
    }
    return schedule(
      state,
      timeoutMs,
      () => fileGrantOperation(() => fileGrants.revokeGrant(state.contents, grantId)),
      parseFileGrantRevocationResult,
    )
  })
  registerNoArgumentHandler(
    ipc,
    desktopChannels.getLocalRuntimeStatus,
    authorize,
    (signal) => bridge.getLocalRuntimeStatus(signal),
    parseLocalRuntimeStatusResult,
    runtimeOperationTimeoutMs,
  )
  ipc.handle(desktopChannels.previewLocalRuntime, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<LocalRuntimePreview>()
    if (args.length !== 1) return invalidRequest<LocalRuntimePreview>()
    let request: LocalRuntimeRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseLocalRuntimeRequest(args[0])
    } catch {
      return invalidRequest<LocalRuntimePreview>()
    }
    return schedule(
      state,
      runtimeOperationTimeoutMs,
      (signal) => bridge.previewLocalRuntime(request, signal),
      parseLocalRuntimePreviewResult,
    )
  })
  ipc.handle(desktopChannels.applyLocalRuntime, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<LocalRuntimeResult>()
    if (args.length !== 1) return invalidRequest<LocalRuntimeResult>()
    let request: LocalRuntimeApplyRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseLocalRuntimeApplyRequest(args[0])
    } catch {
      return invalidRequest<LocalRuntimeResult>()
    }
    return schedule(
      state,
      runtimeOperationTimeoutMs,
      (signal) => bridge.applyLocalRuntime(request, signal),
      parseLocalRuntimeResult,
    )
  })
  registerNoArgumentHandler(
    ipc,
    desktopChannels.listJobs,
    authorize,
    (signal) => bridge.listJobs(signal),
    parseJobListResult,
    timeoutMs,
  )
  ipc.handle(desktopChannels.getJob, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<JobSnapshot>()
    if (args.length !== 1) return invalidRequest<JobSnapshot>()
    let request: JobRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseJobRequest(args[0])
    } catch {
      return invalidRequest<JobSnapshot>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.getJob(request, signal),
      parseJobSnapshotResult,
    )
  })
  ipc.handle(desktopChannels.cancelJob, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized<JobSnapshot>()
    if (args.length !== 1) return invalidRequest<JobSnapshot>()
    let request: JobRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseJobRequest(args[0])
    } catch {
      return invalidRequest<JobSnapshot>()
    }
    return schedule(
      state,
      timeoutMs,
      (signal) => bridge.cancelJob(request, signal),
      parseJobSnapshotResult,
    )
  })
  ipc.handle(desktopChannels.subscribeJobEvents, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized()
    if (args.length !== 1) return invalidRequest()
    let request: JobEventSubscriptionRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseJobEventSubscriptionRequest(args[0])
    } catch {
      return invalidRequest()
    }
    if (state.jobSubscriptions.has(request.subscription_id)) {
      return error(
        'JOB_SUBSCRIPTION_CONFLICT',
        'That task update subscription is already active.',
        'Reload the task center before retrying.',
      )
    }
    if (state.jobSubscriptions.size >= MAX_JOB_SUBSCRIPTIONS) {
      return error(
        'JOB_SUBSCRIBER_LIMIT',
        'The task update subscription limit was reached.',
        'Close unused task views and try again.',
      )
    }
    const subscription: JobSubscription = {
      controller: new AbortController(),
      generation: state.generation,
      request,
      lastSequence: request.after,
      terminalSeen: false,
    }
    state.jobSubscriptions.set(request.subscription_id, subscription)
    let stream: Promise<void>
    try {
      stream = bridge.streamJobEvents(
        request,
        (next) => deliverJobEvent(state, subscription, next),
        subscription.controller.signal,
      )
    } catch {
      state.jobSubscriptions.delete(request.subscription_id)
      subscription.controller.abort(CANCELLED)
      return error(
        'JOB_EVENT_STREAM_FAILED',
        'Task updates could not be verified.',
        'Refresh the task center to reconnect.',
      )
    }
    void stream.then(
      () => {
        if (state.jobSubscriptions.get(request.subscription_id) !== subscription) return
        state.jobSubscriptions.delete(request.subscription_id)
        if (!subscription.controller.signal.aborted && !subscription.terminalSeen) {
          jobStreamFailure(state, subscription, 'JOB_SUBSCRIPTION_CLOSED')
        }
      },
      () => {
        if (state.jobSubscriptions.get(request.subscription_id) !== subscription) return
        state.jobSubscriptions.delete(request.subscription_id)
        if (!subscription.controller.signal.aborted) {
          jobStreamFailure(state, subscription, 'JOB_EVENT_STREAM_FAILED')
        }
      },
    )
    return safeResponse(success({
      schema_version: 1,
      subscription_id: request.subscription_id,
      job_id: request.job_id,
      subscribed: true,
    }), parseJobEventSubscriptionResult)
  })
  ipc.handle(desktopChannels.unsubscribeJobEvents, async (event, ...args) => {
    const state = authorize(event)
    if (!state) return unauthorized()
    if (args.length !== 1) return invalidRequest()
    let request: JobEventUnsubscriptionRequest
    try {
      validateStructuredClone(args[0], requestLimits)
      request = parseJobEventUnsubscriptionRequest(args[0])
    } catch {
      return invalidRequest()
    }
    const subscription = state.jobSubscriptions.get(request.subscription_id)
    if (subscription) {
      state.jobSubscriptions.delete(request.subscription_id)
      subscription.controller.abort(CANCELLED)
    }
    return safeResponse(success({
      schema_version: 1,
      subscription_id: request.subscription_id,
      unsubscribed: true,
    }), parseJobEventUnsubscriptionResult)
  })

  return Object.freeze({
    authorizeWebContents(
      contents: BridgeWebContents,
      trustedUrl: (url: string) => boolean,
    ): () => void {
      if (disposed || contents.isDestroyed()) throw new Error('Cannot authorize unavailable WebContents.')
      const previous = authorizations.get(contents)
      if (previous) removeAuthorization(authorizations, previous, fileGrants)
      const revoke = () => removeAuthorization(authorizations, state, fileGrants)
      const navigate = (event: unknown) => {
        const details = parseNavigationStartDetails(event)
        if (details?.isMainFrame === false) return
        if (details?.isSameDocument === true) return
        state.navigating = true
        invalidate(state)
        fileGrants.revokeOwner(state.contents)
      }
      const commitNavigation = (
        _event: unknown,
        _url: unknown,
        _statusCode: unknown,
        _statusText: unknown,
        isMainFrame: unknown,
      ) => {
        if (isMainFrame === true) state.navigating = false
      }
      let listenersRemoved = false
      const state: Authorization = {
        contents,
        trustedUrl,
        controllers: new Set(),
        queue: [],
        jobSubscriptions: new Map(),
        active: 0,
        generation: 0,
        navigating: false,
        removeLifecycleListeners: () => {
          if (listenersRemoved) return
          listenersRemoved = true
          contents.removeListener('destroyed', revoke)
          contents.removeListener('render-process-gone', revoke)
          contents.removeListener('did-start-navigation', navigate)
          contents.removeListener('did-frame-navigate', commitNavigation)
        },
      }
      contents.on('destroyed', revoke)
      contents.on('render-process-gone', revoke)
      contents.on('did-start-navigation', navigate)
      contents.on('did-frame-navigate', commitNavigation)
      authorizations.set(contents, state)
      let unsubscribed = false
      return () => {
        if (unsubscribed) return
        unsubscribed = true
        removeAuthorization(authorizations, state, fileGrants)
      }
    },
    invalidateSidecarSession(): void {
      fileGrants.revokeAll()
      for (const state of authorizations.values()) invalidate(state, true)
    },
    dispose(): void {
      if (disposed) return
      disposed = true
      for (const state of [...authorizations.values()]) removeAuthorization(authorizations, state, fileGrants)
      fileGrants.dispose()
    },
  })
}
