import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'
import type { AncestryBridge, BridgeResult, CapabilityManifest } from '../shared-contract/desktop'
import { desktopChannels } from '../shared-contract/desktop'
import { registerDesktopIpcHandlers } from './ipc-handlers'

const result = <T>(data: T) => ({ ok: true as const, protocolVersion: '1' as const, data })
const capabilities = result({
  api: { namespace: '/api/v1', contract: 'ancestryllm.internal-api/1', application_contract: 'ancestryllm.application/0.3' },
  modules: [],
  request_policy: { max_body_bytes: 1, max_json_depth: 1, max_collection_items: 1, max_string_characters: 1 },
  pagination: { default_limit: 1, maximum_limit: 1, maximum_cursor_characters: 32 },
}) satisfies BridgeResult<CapabilityManifest>

const bridge = (): AncestryBridge => ({
  getAppInfo: vi.fn().mockResolvedValue(result({ applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' })),
  getStartupDiagnostics: vi.fn().mockResolvedValue(result({ state: 'ready', failure: null, automaticRestartsRemaining: 1, manualRetriesRemaining: 1 })),
  getCapabilities: vi.fn().mockResolvedValue(capabilities),
  retrySidecar: vi.fn().mockResolvedValue(result({ state: 'ready', failure: null, automaticRestartsRemaining: 1, manualRetriesRemaining: 0 })),
  getPreferences: vi.fn().mockResolvedValue(result({ colorScheme: 'system', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 0 })),
  updatePreferences: vi.fn().mockResolvedValue(result({ colorScheme: 'dark', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 1 })),
})

class FakeWebContents extends EventEmitter {
  readonly id: number
  mainFrame: Readonly<{ url: string }>
  private destroyed = false

  constructor(id = 1, url = 'app://bundle/index.html') {
    super()
    this.id = id
    this.mainFrame = Object.freeze({ url })
  }

  isDestroyed(): boolean { return this.destroyed }

  startNavigation(url = 'app://bundle/index.html', isMainFrame = true): void {
    this.emit('did-start-navigation', {}, url, false, isMainFrame)
  }

  commitNavigation(url = 'app://bundle/index.html', isMainFrame = true): void {
    if (isMainFrame) this.mainFrame = Object.freeze({ url })
    this.emit('did-frame-navigate', {}, url, 200, 'OK', isMainFrame)
  }

  navigate(url = 'app://bundle/index.html', isMainFrame = true): void {
    this.startNavigation(url, isMainFrame)
    this.commitNavigation(url, isMainFrame)
  }

  destroy(): void {
    this.destroyed = true
    this.emit('destroyed')
  }
}

type Handler = (event: unknown, ...args: unknown[]) => Promise<unknown>

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((complete) => { resolve = complete })
  return { promise, resolve }
}

function harness(control = bridge(), options: Readonly<{ operationTimeoutMs?: number }> = {}) {
  const handlers = new Map<string, Handler>()
  const controller = registerDesktopIpcHandlers(
    { handle: (channel, handler) => { handlers.set(channel, handler) } },
    control,
    options,
  )
  const contents = new FakeWebContents()
  const unsubscribe = controller.authorizeWebContents(
    contents,
    (url) => url === 'app://bundle/index.html',
  )
  const event = (sender = contents, senderFrame: unknown = sender.mainFrame) => ({ sender, senderFrame })
  return { control, controller, contents, event, handlers, unsubscribe }
}

describe('desktop IPC handlers', () => {
  it('registers exactly the six declared static channels', () => {
    const handlers = new Map<string, Handler>()
    registerDesktopIpcHandlers(
      { handle: (channel, handler) => { handlers.set(channel, handler) } },
      bridge(),
    )
    expect([...handlers.keys()].sort()).toEqual(Object.values(desktopChannels).sort())
    expect(handlers.size).toBe(6)
  })

  it('requires the exact live WebContents, main frame, and trusted origin on every request', async () => {
    const { control, contents, event, handlers } = harness()
    const getCapabilities = handlers.get(desktopChannels.getCapabilities)
    const otherContents = new FakeWebContents(contents.id)

    await expect(getCapabilities?.(event(otherContents))).resolves.toMatchObject({ ok: false, error: { code: 'UNAUTHORIZED_SENDER' } })
    await expect(getCapabilities?.(event(contents, { url: contents.mainFrame.url }))).resolves.toMatchObject({ ok: false, error: { code: 'UNAUTHORIZED_SENDER' } })
    contents.startNavigation('https://attacker.invalid/')
    await expect(getCapabilities?.(event())).resolves.toMatchObject({ ok: false, error: { code: 'UNAUTHORIZED_SENDER' } })
    contents.commitNavigation('https://attacker.invalid/')
    await expect(getCapabilities?.(event())).resolves.toMatchObject({ ok: false, error: { code: 'UNAUTHORIZED_SENDER' } })
    contents.navigate()
    await expect(getCapabilities?.(event())).resolves.toMatchObject({ ok: true })
    contents.destroy()
    await expect(getCapabilities?.(event())).resolves.toMatchObject({ ok: false, error: { code: 'UNAUTHORIZED_SENDER' } })
    expect(control.getCapabilities).toHaveBeenCalledTimes(1)
  })

  it('returns an idempotent unsubscribe that revokes the WebContents identity', async () => {
    const { event, handlers, unsubscribe } = harness()
    unsubscribe()
    expect(() => unsubscribe()).not.toThrow()
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({
      ok: false,
      error: { code: 'UNAUTHORIZED_SENDER' },
    })
  })

  it('does not let a stale authorization revoke a replacement for the same WebContents', async () => {
    const { controller, contents, event, handlers, unsubscribe } = harness()
    const replacement = controller.authorizeWebContents(
      contents,
      (url) => url === 'app://bundle/index.html',
    )

    unsubscribe()
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({ ok: true })
    replacement()
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({
      ok: false,
      error: { code: 'UNAUTHORIZED_SENDER' },
    })
  })

  it('rejects invalid, surplus, inherited, custom-prototype, and cyclic payloads before main control', async () => {
    const { control, event, handlers } = harness()
    const update = handlers.get(desktopChannels.updatePreferences)
    const inherited = Object.assign(Object.create({ colorScheme: 'dark' }) as Record<string, unknown>, {
      expectedRevision: 0,
      reducedMotion: true,
    })
    const customPrototype = Object.assign(Object.create({ marker: true }) as Record<string, unknown>, {
      expectedRevision: 0,
      colorScheme: 'dark',
    })
    const cyclic: Record<string, unknown> = { expectedRevision: 0, colorScheme: 'dark' }
    cyclic.self = cyclic
    const accessor = { colorScheme: 'dark' } as Record<string, unknown>
    Object.defineProperty(accessor, 'expectedRevision', { enumerable: true, get: () => 0 })
    const hidden = { expectedRevision: 0, colorScheme: 'dark' }
    Object.defineProperty(hidden, 'privateField', { enumerable: false, value: 'canary' })
    const symbolKey = { expectedRevision: 0, colorScheme: 'dark', [Symbol('private')]: true }
    const repeated = { expectedRevision: 0, colorScheme: 'dark' } as Record<string, unknown>
    const shared = { value: true }
    repeated.first = shared
    repeated.second = shared

    for (const payload of [
      { expectedRevision: 0, colorScheme: 'sepia' },
      { expectedRevision: Number.NaN, colorScheme: 'dark' },
      { expectedRevision: 0, colorScheme: 'dark', unknown: true },
      { expectedRevision: 0, colorScheme: 'x'.repeat(257) },
      { colorScheme: 'dark' },
      inherited,
      customPrototype,
      cyclic,
      accessor,
      hidden,
      symbolKey,
      repeated,
    ]) {
      await expect(update?.(event(), payload)).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    }
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event(), 'surplus')).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    expect(control.updatePreferences).not.toHaveBeenCalled()
    expect(control.getAppInfo).not.toHaveBeenCalled()
  })

  it('validates structured-clone bounds and the runtime response before returning it', async () => {
    const control = bridge()
    vi.mocked(control.getAppInfo).mockResolvedValueOnce(result({
      applicationName: 'AncestryLLM',
      appVersion: 'x'.repeat(1_048_577),
      buildChannel: 'development',
    }) as never)
    const { event, handlers } = harness(control)
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({
      ok: false,
      error: { code: 'INVALID_RESPONSE' },
    })

    let accessorRead = false
    const unsafeModules: unknown[] = [undefined]
    Object.defineProperty(unsafeModules, '0', {
      enumerable: true,
      get: () => {
        accessorRead = true
        return {}
      },
    })
    vi.mocked(control.getCapabilities).mockResolvedValueOnce(result({
      ...capabilities.data,
      modules: unsafeModules,
    }) as never)
    await expect(handlers.get(desktopChannels.getCapabilities)?.(event())).resolves.toMatchObject({
      ok: false,
      error: { code: 'INVALID_RESPONSE' },
    })
    expect(accessorRead).toBe(false)
  })

  it('single-flights and bounds capability readers per WebContents', async () => {
    const control = bridge()
    const pending = deferred<BridgeResult<CapabilityManifest>>()
    vi.mocked(control.getCapabilities).mockReturnValue(pending.promise)
    const { event, handlers } = harness(control)
    const getCapabilities = handlers.get(desktopChannels.getCapabilities)
    const readers = Array.from({ length: 32 }, () => getCapabilities?.(event()))

    expect(control.getCapabilities).toHaveBeenCalledTimes(1)
    await expect(getCapabilities?.(event())).resolves.toMatchObject({
      ok: false,
      error: {
        code: 'BRIDGE_OVERLOADED',
        message: 'The desktop request queue is full.',
      },
    })
    pending.resolve(capabilities)
    await expect(Promise.all(readers)).resolves.toSatisfy(
      (values: unknown[]) => values.every((value) => (value as { ok?: boolean }).ok === true),
    )
  })

  it('uses a bounded non-coalesced queue and returns a stable redacted overload error', async () => {
    const control = bridge()
    const pending = deferred<Awaited<ReturnType<AncestryBridge['getAppInfo']>>>()
    vi.mocked(control.getAppInfo).mockReturnValue(pending.promise)
    const { controller, event, handlers } = harness(control)
    const getAppInfo = handlers.get(desktopChannels.getAppInfo)
    const accepted = Array.from({ length: 12 }, () => getAppInfo?.(event()))

    expect(control.getAppInfo).toHaveBeenCalledTimes(4)
    await expect(getAppInfo?.(event())).resolves.toEqual({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'BRIDGE_OVERLOADED',
        message: 'The desktop request queue is full.',
        remediation: 'Wait for current desktop requests to finish and try again.',
      },
    })
    controller.dispose()
    await expect(Promise.all(accepted)).resolves.toSatisfy(
      (values: unknown[]) => values.every((value) => (value as { error?: { code?: string } }).error?.code === 'REQUEST_CANCELLED'),
    )
  })

  it('cancels queued and in-flight work on navigation and sidecar-session changes', async () => {
    const control = bridge()
    const pending = deferred<BridgeResult<CapabilityManifest>>()
    let signal: AbortSignal | undefined
    vi.mocked(control.getCapabilities).mockImplementation((operationSignal?: AbortSignal) => {
      signal = operationSignal
      return pending.promise
    })
    const { controller, contents, event, handlers } = harness(control)
    const request = handlers.get(desktopChannels.getCapabilities)?.(event())

    contents.navigate('app://bundle/subframe.html', false)
    expect(signal?.aborted).toBe(false)
    contents.navigate()
    await expect(request).resolves.toMatchObject({ ok: false, error: { code: 'REQUEST_CANCELLED' } })
    const nextRequest = handlers.get(desktopChannels.getCapabilities)?.(event())
    controller.invalidateSidecarSession()
    await expect(nextRequest).resolves.toMatchObject({ ok: false, error: { code: 'REQUEST_CANCELLED' } })
    pending.resolve(capabilities)
  })

  it('revokes identity and cancels work when the renderer process exits', async () => {
    const control = bridge()
    vi.mocked(control.getAppInfo).mockReturnValue(new Promise(() => undefined))
    const { contents, event, handlers } = harness(control)
    const request = handlers.get(desktopChannels.getAppInfo)?.(event())

    contents.emit('render-process-gone')
    await expect(request).resolves.toMatchObject({ ok: false, error: { code: 'REQUEST_CANCELLED' } })
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({
      ok: false,
      error: { code: 'UNAUTHORIZED_SENDER' },
    })
  })

  it('returns a stable timeout while the underlying operation remains stalled', async () => {
    const control = bridge()
    const signals: AbortSignal[] = []
    vi.mocked(control.getAppInfo).mockImplementation((signal?: AbortSignal) => {
      if (signal) signals.push(signal)
      return new Promise(() => undefined)
    })
    const { event, handlers } = harness(control, { operationTimeoutMs: 5 })
    const getAppInfo = handlers.get(desktopChannels.getAppInfo)
    const firstBurst = Array.from({ length: 12 }, () => getAppInfo?.(event()))
    await expect(Promise.all(firstBurst)).resolves.toSatisfy(
      (values: unknown[]) => values.every(
        (value) => (value as { error?: { code?: string } }).error?.code === 'REQUEST_TIMEOUT',
      ),
    )
    expect(control.getAppInfo).toHaveBeenCalledTimes(4)
    expect(signals).toHaveLength(4)
    expect(signals.every((signal) => signal.aborted)).toBe(true)

    const secondBurst = Array.from({ length: 8 }, () => getAppInfo?.(event()))
    await expect(getAppInfo?.(event())).resolves.toMatchObject({
      ok: false,
      error: { code: 'BRIDGE_OVERLOADED' },
    })
    await expect(Promise.all(secondBurst)).resolves.toSatisfy(
      (values: unknown[]) => values.every(
        (value) => (value as { error?: { code?: string } }).error?.code === 'REQUEST_TIMEOUT',
      ),
    )
    expect(control.getAppInfo).toHaveBeenCalledTimes(4)
  })
})
