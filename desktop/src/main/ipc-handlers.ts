/** Registers a bounded, exact-owner desktop IPC bridge. */
import {
  DESKTOP_PROTOCOL_VERSION,
  desktopChannels,
  type AncestryBridge,
  type BridgeErrorCode,
  type BridgeResult,
  type CapabilityManifest,
  type LocalPreferences,
  type PreferenceUpdate,
} from '../shared-contract/desktop'
import {
  parseAppInfoResult,
  parseCapabilitiesResult,
  parsePreferenceUpdate,
  parsePreferencesResult,
  parseStartupDiagnosticsResult,
} from '../shared-contract/runtime'
import { validateStructuredClone } from './structured-clone-policy'

type IpcHandler = (event: unknown, ...args: unknown[]) => Promise<unknown>
export interface IpcRegistrar { handle(channel: string, handler: IpcHandler): void }

export interface BridgeFrame { readonly url: string }
export interface BridgeWebContents {
  readonly mainFrame: BridgeFrame
  isDestroyed(): boolean
  on(event: string, listener: (...args: unknown[]) => void): unknown
  removeListener(event: string, listener: (...args: unknown[]) => void): unknown
}

export interface MainDesktopBridge extends AncestryBridge {
  getAppInfo(signal?: AbortSignal): ReturnType<AncestryBridge['getAppInfo']>
  getStartupDiagnostics(signal?: AbortSignal): ReturnType<AncestryBridge['getStartupDiagnostics']>
  getCapabilities(signal?: AbortSignal): ReturnType<AncestryBridge['getCapabilities']>
  retrySidecar(signal?: AbortSignal): ReturnType<AncestryBridge['retrySidecar']>
  getPreferences(signal?: AbortSignal): ReturnType<AncestryBridge['getPreferences']>
  updatePreferences(update: PreferenceUpdate, signal?: AbortSignal): ReturnType<AncestryBridge['updatePreferences']>
}

export interface DesktopIpcController {
  authorizeWebContents(
    contents: BridgeWebContents,
    trustedUrl: (url: string) => boolean,
  ): () => void
  invalidateSidecarSession(): void
  dispose(): void
}

interface RegistrationOptions { readonly operationTimeoutMs?: number }
interface Authorization {
  readonly contents: BridgeWebContents
  readonly trustedUrl: (url: string) => boolean
  readonly controllers: Set<AbortController>
  readonly queue: QueueEntry[]
  readonly removeLifecycleListeners: () => void
  active: number
  generation: number
  navigating: boolean
  capabilityFlight?: CapabilityFlight
}
interface CapabilityFlight {
  readonly generation: number
  readonly promise: Promise<BridgeResult<CapabilityManifest>>
  subscribers: number
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
const DEFAULT_OPERATION_TIMEOUT_MS = 5_000
const CANCELLED = Symbol('bridge-request-cancelled')
const TIMED_OUT = Symbol('bridge-request-timed-out')

const requestLimits = Object.freeze({
  maxBytes: 8_192,
  maxDepth: 4,
  maxItems: 32,
  maxStringCharacters: 256,
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

function invalidate(state: Authorization): void {
  state.generation += 1
  for (const controller of state.controllers) controller.abort(CANCELLED)
  for (const entry of state.queue.splice(0)) entry.cancel()
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
): void {
  if (authorizations.get(state.contents) !== state) return
  authorizations.delete(state.contents)
  state.removeLifecycleListeners()
  invalidate(state)
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
  options: Readonly<RegistrationOptions> = {},
): DesktopIpcController {
  const timeoutMs = options.operationTimeoutMs ?? DEFAULT_OPERATION_TIMEOUT_MS
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new Error('Desktop IPC operation timeout must be positive.')
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

  return Object.freeze({
    authorizeWebContents(
      contents: BridgeWebContents,
      trustedUrl: (url: string) => boolean,
    ): () => void {
      if (disposed || contents.isDestroyed()) throw new Error('Cannot authorize unavailable WebContents.')
      const previous = authorizations.get(contents)
      if (previous) removeAuthorization(authorizations, previous)
      const revoke = () => removeAuthorization(authorizations, state)
      const navigate = (_event: unknown, _url: unknown, _inPlace: unknown, isMainFrame: unknown) => {
        if (isMainFrame !== true) return
        state.navigating = true
        invalidate(state)
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
        removeAuthorization(authorizations, state)
      }
    },
    invalidateSidecarSession(): void {
      for (const state of authorizations.values()) invalidate(state)
    },
    dispose(): void {
      if (disposed) return
      disposed = true
      for (const state of [...authorizations.values()]) removeAuthorization(authorizations, state)
    },
  })
}
