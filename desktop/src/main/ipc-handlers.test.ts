import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'
import type {
  BridgeResult,
  CapabilityManifest,
  ConsentPreview,
  FileGrant,
  FileGrantId,
  ProviderConfiguration,
} from '../shared-contract/desktop'
import { desktopChannels } from '../shared-contract/desktop'
import { FileGrantBrokerError } from './file-grant-broker'
import { readyStartupReportFixture } from '../mock-bridge/fixtures'
import {
  registerDesktopIpcHandlers,
  type MainDesktopBridge,
  type MainFileGrantBroker,
} from './ipc-handlers'

const result = <T>(data: T) => ({ ok: true as const, protocolVersion: '1' as const, data })
const capabilities = result({
  api: { namespace: '/api/v1', contract: 'ancestryllm.internal-api/1', application_contract: 'ancestryllm.application/0.3' },
  modules: [],
  request_policy: { max_body_bytes: 1, max_json_depth: 1, max_collection_items: 1, max_string_characters: 1 },
  pagination: { default_limit: 1, maximum_limit: 1, maximum_cursor_characters: 32 },
}) satisfies BridgeResult<CapabilityManifest>
const providerConfiguration = result({
  schema_version: 1,
  revision: '0'.repeat(64),
  profiles: [],
  consents: [],
}) satisfies BridgeResult<ProviderConfiguration>
const consentPreview = result({
  schema_version: 1,
  provider_profile_name: 'local',
  provider_id: 'ollama',
  modules: ['search'],
  purposes: ['genealogy'],
  data_classes: ['deceased_person'],
  models: ['llama3.2'],
  max_cost_usd: null,
  retain_payloads: false,
  warning_codes: [],
}) satisfies BridgeResult<ConsentPreview>

const bridge = (): MainDesktopBridge => ({
  getAppInfo: vi.fn().mockResolvedValue(result({ applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' })),
  getStartupDiagnostics: vi.fn().mockResolvedValue(result({ state: 'ready', failure: null, automaticRestartsRemaining: 1, manualRetriesRemaining: 1, report: readyStartupReportFixture })),
  getCapabilities: vi.fn().mockResolvedValue(capabilities),
  retrySidecar: vi.fn().mockResolvedValue(result({ state: 'ready', failure: null, automaticRestartsRemaining: 1, manualRetriesRemaining: 0, report: readyStartupReportFixture })),
  getPreferences: vi.fn().mockResolvedValue(result({ colorScheme: 'system', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 0 })),
  updatePreferences: vi.fn().mockResolvedValue(result({ colorScheme: 'dark', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 1 })),
  getSettings: vi.fn().mockResolvedValue(result({ schema_version: 1, revision: 0, fields: [] })),
  updateSettings: vi.fn().mockResolvedValue(result({ schema_version: 1, revision: 1, fields: [] })),
  getProviderConfiguration: vi.fn().mockResolvedValue(providerConfiguration),
  createProviderProfile: vi.fn().mockResolvedValue(providerConfiguration),
  validateProviderEndpoint: vi.fn().mockResolvedValue(result({
    schema_version: 1,
    status: 'reachable',
    endpoint_kind: 'loopback',
    http_status: 200,
    destination_digest: 'a'.repeat(64),
  })),
  previewConsent: vi.fn().mockResolvedValue(consentPreview),
  createConsent: vi.fn().mockResolvedValue(providerConfiguration),
  revokeConsent: vi.fn().mockResolvedValue(providerConfiguration),
  getSecretStatus: vi.fn().mockResolvedValue(result({ reference: 'openai.api_key', status: 'missing' })),
  setSecret: vi.fn().mockResolvedValue(result({ reference: 'openai.api_key', status: 'present' })),
  deleteSecret: vi.fn().mockResolvedValue(result({ reference: 'openai.api_key', status: 'missing' })),
})

const grantId = `grt_${'a'.repeat(64)}` as FileGrantId
const grantedGedcom = Object.freeze({
  grantId,
  purpose: 'gedcom-read',
  access: 'read',
  scope: Object.freeze({
    originatingWindow: 'requesting-window',
    lifetime: 'app-session',
    redemption: 'single-use',
  }),
  metadata: Object.freeze({
    displayName: 'fictional.ged',
    format: 'gedcom',
    sizeBytes: 26,
    validation: 'validated-input',
  }),
}) satisfies FileGrant

const fileGrantBroker = (): MainFileGrantBroker => ({
  requestOpenGrant: vi.fn().mockResolvedValue(null),
  requestSaveGrant: vi.fn().mockResolvedValue(null),
  revokeGrant: vi.fn().mockReturnValue(Object.freeze({ revoked: true })),
  revokeOwner: vi.fn(),
  revokeAll: vi.fn(),
  dispose: vi.fn(),
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

  startNavigation(url = 'app://bundle/index.html', isMainFrame = true, isSameDocument = false): void {
    this.emit(
      'did-start-navigation',
      Object.freeze({ url, isSameDocument, isMainFrame, frame: isMainFrame ? this.mainFrame : null }),
      url,
      isSameDocument,
      isMainFrame,
      1,
      1,
    )
  }

  commitNavigation(url = 'app://bundle/index.html', isMainFrame = true): void {
    if (isMainFrame) this.mainFrame = Object.freeze({ url })
    this.emit('did-frame-navigate', {}, url, 200, 'OK', isMainFrame)
  }

  navigate(url = 'app://bundle/index.html', isMainFrame = true): void {
    this.startNavigation(url, isMainFrame)
    this.commitNavigation(url, isMainFrame)
  }

  navigateInPage(url: string, isMainFrame = true): void {
    this.startNavigation(url, isMainFrame, true)
    if (isMainFrame) this.mainFrame = Object.freeze({ url })
    this.emit('did-navigate-in-page', {}, url, isMainFrame, 1, 1)
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

function harness(
  control = bridge(),
  options: Readonly<{ operationTimeoutMs?: number; fileDialogTimeoutMs?: number }> = {},
  fileGrants = fileGrantBroker(),
) {
  const handlers = new Map<string, Handler>()
  const controller = registerDesktopIpcHandlers(
    { handle: (channel, handler) => { handlers.set(channel, handler) } },
    control,
    fileGrants,
    options,
  )
  const contents = new FakeWebContents()
  const unsubscribe = controller.authorizeWebContents(
    contents,
    (url) => {
      try {
        const candidate = new URL(url)
        candidate.hash = ''
        return candidate.href === 'app://bundle/index.html'
      } catch {
        return false
      }
    },
  )
  const event = (sender = contents, senderFrame: unknown = sender.mainFrame) => ({ sender, senderFrame })
  return { control, controller, contents, event, fileGrants, handlers, unsubscribe }
}

describe('desktop IPC handlers', () => {
  it('registers exactly the twenty declared static channels', () => {
    const handlers = new Map<string, Handler>()
    registerDesktopIpcHandlers(
      { handle: (channel, handler) => { handlers.set(channel, handler) } },
      bridge(),
      fileGrantBroker(),
    )
    expect([...handlers.keys()].sort()).toEqual(Object.values(desktopChannels).sort())
    expect(handlers.size).toBe(20)
  })

  it('requires the exact live WebContents, main frame, and trusted origin on every request', async () => {
    const { control, contents, event, fileGrants, handlers } = harness()
    const getCapabilities = handlers.get(desktopChannels.getCapabilities)
    const otherContents = new FakeWebContents(contents.id)

    await expect(getCapabilities?.(event(otherContents))).resolves.toMatchObject({ ok: false, error: { code: 'UNAUTHORIZED_SENDER' } })
    await expect(getCapabilities?.(event(contents, { url: contents.mainFrame.url }))).resolves.toMatchObject({ ok: false, error: { code: 'UNAUTHORIZED_SENDER' } })
    contents.startNavigation('https://attacker.invalid/')
    expect(fileGrants.revokeOwner).toHaveBeenCalledWith(contents)
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
    const { contents, event, fileGrants, handlers, unsubscribe } = harness()
    unsubscribe()
    expect(() => unsubscribe()).not.toThrow()
    expect(fileGrants.revokeOwner).toHaveBeenCalledTimes(1)
    expect(fileGrants.revokeOwner).toHaveBeenCalledWith(contents)
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({
      ok: false,
      error: { code: 'UNAUTHORIZED_SENDER' },
    })
  })

  it('does not let a stale authorization revoke a replacement for the same WebContents', async () => {
    const { controller, contents, event, fileGrants, handlers, unsubscribe } = harness()
    const replacement = controller.authorizeWebContents(
      contents,
      (url) => url === 'app://bundle/index.html',
    )

    unsubscribe()
    expect(fileGrants.revokeOwner).toHaveBeenCalledTimes(1)
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({ ok: true })
    replacement()
    expect(fileGrants.revokeOwner).toHaveBeenCalledTimes(2)
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

  it('binds strict open, save, and revoke requests to the exact WebContents owner', async () => {
    const grants = fileGrantBroker()
    vi.mocked(grants.requestOpenGrant).mockResolvedValueOnce(grantedGedcom)
    const { contents, event, handlers } = harness(bridge(), {}, grants)

    await expect(handlers.get(desktopChannels.requestOpenFileGrant)?.(
      event(),
      { purpose: 'gedcom-read' },
    )).resolves.toEqual(result(grantedGedcom))
    expect(grants.requestOpenGrant).toHaveBeenCalledWith(
      contents,
      { purpose: 'gedcom-read' },
      expect.any(AbortSignal),
    )
    expect(JSON.stringify(await handlers.get(desktopChannels.requestSaveFileGrant)?.(
      event(),
      { purpose: 'json-write', suggestedName: 'report.json' },
    ))).not.toContain('path')
    expect(grants.requestSaveGrant).toHaveBeenCalledWith(
      contents,
      { purpose: 'json-write', suggestedName: 'report.json' },
      expect.any(AbortSignal),
    )
    await expect(handlers.get(desktopChannels.revokeFileGrant)?.(event(), grantId)).resolves.toEqual(
      result({ revoked: true }),
    )
    expect(grants.revokeGrant).toHaveBeenCalledWith(contents, grantId)
  })

  it('rejects ambient paths, purpose escalation, traversal names, and malformed grant identifiers', async () => {
    const { event, fileGrants, handlers } = harness()
    for (const [channel, payload] of [
      [desktopChannels.requestOpenFileGrant, { purpose: 'gedcom-read', path: '/private/tree.ged' }],
      [desktopChannels.requestOpenFileGrant, { purpose: 'gedcom-write' }],
      [desktopChannels.requestSaveFileGrant, { purpose: 'gedcom-write', suggestedName: '../tree.ged' }],
      [desktopChannels.revokeFileGrant, 'grt_too-short'],
    ] as const) {
      await expect(handlers.get(channel)?.(event(), payload)).resolves.toMatchObject({
        ok: false,
        error: { code: 'INVALID_REQUEST' },
      })
    }
    expect(fileGrants.requestOpenGrant).not.toHaveBeenCalled()
    expect(fileGrants.requestSaveGrant).not.toHaveBeenCalled()
    expect(fileGrants.revokeGrant).not.toHaveBeenCalled()
  })

  it('returns stable redacted file-grant failures and rejects unsafe broker responses', async () => {
    const grants = fileGrantBroker()
    vi.mocked(grants.requestOpenGrant)
      .mockRejectedValueOnce(new FileGrantBrokerError('FILE_GRANT_STALE'))
      .mockResolvedValueOnce({ ...grantedGedcom, path: '/private/fictional.ged' } as never)
    const { event, handlers } = harness(bridge(), {}, grants)
    const open = handlers.get(desktopChannels.requestOpenFileGrant)

    await expect(open?.(event(), { purpose: 'gedcom-read' })).resolves.toEqual({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'FILE_GRANT_STALE',
        message: 'The selected file changed after it was approved.',
        remediation: 'Review and select the file again.',
      },
    })
    const invalidResponse = await open?.(event(), { purpose: 'gedcom-read' })
    expect(invalidResponse).toMatchObject({ ok: false, error: { code: 'INVALID_RESPONSE' } })
    expect(JSON.stringify(invalidResponse)).not.toContain('/private/fictional.ged')
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
    const pending = deferred<Awaited<ReturnType<MainDesktopBridge['getAppInfo']>>>()
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
    const { controller, contents, event, fileGrants, handlers } = harness(control)
    const request = handlers.get(desktopChannels.getCapabilities)?.(event())

    contents.navigate('app://bundle/subframe.html', false)
    expect(signal?.aborted).toBe(false)
    contents.navigate()
    await expect(request).resolves.toMatchObject({ ok: false, error: { code: 'REQUEST_CANCELLED' } })
    const nextRequest = handlers.get(desktopChannels.getCapabilities)?.(event())
    controller.invalidateSidecarSession()
    expect(fileGrants.revokeAll).toHaveBeenCalledTimes(1)
    await expect(nextRequest).resolves.toMatchObject({ ok: false, error: { code: 'REQUEST_CANCELLED' } })
    pending.resolve(capabilities)
  })

  it('keeps trusted same-document routes authorized without revoking grants or cancelling work', async () => {
    const control = bridge()
    const pending = deferred<BridgeResult<CapabilityManifest>>()
    let signal: AbortSignal | undefined
    vi.mocked(control.getCapabilities).mockImplementation((operationSignal?: AbortSignal) => {
      signal = operationSignal
      return pending.promise
    })
    const { contents, event, fileGrants, handlers } = harness(control)
    const request = handlers.get(desktopChannels.getCapabilities)?.(event())

    contents.navigateInPage('app://bundle/index.html#/diagnostics')

    expect(signal?.aborted).toBe(false)
    expect(fileGrants.revokeOwner).not.toHaveBeenCalled()
    pending.resolve(capabilities)
    await expect(request).resolves.toMatchObject({ ok: true })
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({ ok: true })

    contents.navigateInPage('app://bundle/index.html#/settings')

    expect(fileGrants.revokeOwner).not.toHaveBeenCalled()
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({ ok: true })
  })

  it('fails closed when main-frame navigation details are malformed', async () => {
    const { contents, event, fileGrants, handlers } = harness()

    contents.emit('did-start-navigation', {}, 'app://bundle/index.html', false, true)

    expect(fileGrants.revokeOwner).toHaveBeenCalledWith(contents)
    await expect(handlers.get(desktopChannels.getAppInfo)?.(event())).resolves.toMatchObject({
      ok: false,
      error: { code: 'UNAUTHORIZED_SENDER' },
    })
  })

  it('revokes identity and cancels work when the renderer process exits', async () => {
    const control = bridge()
    vi.mocked(control.getAppInfo).mockReturnValue(new Promise(() => undefined))
    const { contents, event, fileGrants, handlers } = harness(control)
    const request = handlers.get(desktopChannels.getAppInfo)?.(event())

    contents.emit('render-process-gone')
    expect(fileGrants.revokeOwner).toHaveBeenCalledWith(contents)
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

  it('disposes all owner grants and the broker idempotently', () => {
    const { contents, controller, fileGrants } = harness()

    controller.dispose()
    controller.dispose()

    expect(fileGrants.revokeOwner).toHaveBeenCalledTimes(1)
    expect(fileGrants.revokeOwner).toHaveBeenCalledWith(contents)
    expect(fileGrants.dispose).toHaveBeenCalledTimes(1)
  })
})
