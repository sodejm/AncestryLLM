/** Verifies IPC ownership, payload validation, event delivery, and error mapping. */
import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'
import type {
  BridgeResult,
  CapabilityManifest,
  ChatCapability,
  ChatEvent,
  ChatSession,
  ChatStreamRun,
  ConsentPreview,
  FileGrant,
  FileGrantId,
  JobEvent,
  JobList,
  JobSnapshot,
  LocalRuntimePreview,
  LocalRuntimeStatus,
  ProviderConfiguration,
} from '../shared-contract/desktop'
import { desktopChannels, desktopEventChannels } from '../shared-contract/desktop'
import { FileGrantBrokerError } from './file-grant-broker'
import { readyStartupReportFixture } from '../mock-bridge/fixtures'
import { SidecarClientError } from './sidecar-client'
import {
  registerDesktopIpcHandlers,
  type MainDesktopBridge,
  type MainFileGrantBroker,
  type MainNativeActions,
  type RegistrationOptions,
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
const localRuntimeStatus = Object.freeze({
  schema_version: 1,
  state: 'not-installed',
  code: 'RUNTIME_NOT_INSTALLED',
  supported: true,
  host: Object.freeze({
    operating_system: 'macos',
    architecture: 'arm64',
    macos_major: 15,
    virtualization: 'available',
    free_space: 'sufficient',
    existing_docker_contexts: 0,
  }),
  allocation: Object.freeze({ cpus: 4, memory_gib: 8, disk_gib: 60 }),
  components: Object.freeze([
    Object.freeze({ name: 'colima', version: '0.10.3', installed: false }),
    Object.freeze({ name: 'lima', version: '2.2.0', installed: false }),
    Object.freeze({ name: 'docker-cli', version: '29.7.2', installed: false }),
    Object.freeze({ name: 'docker-buildx', version: '0.36.1', installed: false }),
    Object.freeze({ name: 'docker-compose', version: '5.4.0', installed: false }),
  ]),
  vm_image: Object.freeze({ version: '0.10.4', installed: false }),
}) satisfies LocalRuntimeStatus
const localRuntimeReview = Object.freeze({
  artifacts: Object.freeze([
    Object.freeze({
      name: 'colima',
      version: '0.10.3',
      repository: 'abiosoft/colima',
      asset_name: 'colima-Darwin-arm64',
      source_url: 'https://github.com/abiosoft/colima/releases/download/v0.10.3/colima-Darwin-arm64',
      sha256: '1'.repeat(64),
      size_bytes: 15_656_320,
      license: 'MIT',
      license_url: 'https://raw.githubusercontent.com/abiosoft/colima/v0.10.3/LICENSE',
      license_sha256: '2'.repeat(64),
    }),
    Object.freeze({
      name: 'lima',
      version: '2.2.0',
      repository: 'lima-vm/lima',
      asset_name: 'lima-2.2.0-Darwin-arm64.tar.gz',
      source_url: 'https://github.com/lima-vm/lima/releases/download/v2.2.0/lima-2.2.0-Darwin-arm64.tar.gz',
      sha256: '3'.repeat(64),
      size_bytes: 37_586_365,
      license: 'Apache-2.0',
      license_url: 'https://raw.githubusercontent.com/lima-vm/lima/v2.2.0/LICENSE',
      license_sha256: '4'.repeat(64),
    }),
    Object.freeze({
      name: 'docker-cli',
      version: '29.7.2',
      repository: 'docker/cli',
      asset_name: 'docker-29.7.2.tgz',
      source_url: 'https://download.docker.com/mac/static/stable/aarch64/docker-29.7.2.tgz',
      sha256: '5'.repeat(64),
      size_bytes: 18_920_558,
      license: 'Apache-2.0',
      license_url: 'https://raw.githubusercontent.com/docker/cli/v29.7.2/LICENSE',
      license_sha256: '6'.repeat(64),
    }),
    Object.freeze({
      name: 'docker-buildx',
      version: '0.36.1',
      repository: 'docker/buildx',
      asset_name: 'buildx-v0.36.1.darwin-arm64',
      source_url: 'https://github.com/docker/buildx/releases/download/v0.36.1/buildx-v0.36.1.darwin-arm64',
      sha256: '7'.repeat(64),
      size_bytes: 62_541_920,
      license: 'Apache-2.0',
      license_url: 'https://raw.githubusercontent.com/docker/buildx/v0.36.1/LICENSE',
      license_sha256: '8'.repeat(64),
    }),
    Object.freeze({
      name: 'docker-compose',
      version: '5.4.0',
      repository: 'docker/compose',
      asset_name: 'docker-compose-darwin-aarch64',
      source_url: 'https://github.com/docker/compose/releases/download/v5.4.0/docker-compose-darwin-aarch64',
      sha256: '9'.repeat(64),
      size_bytes: 46_852_962,
      license: 'Apache-2.0',
      license_url: 'https://raw.githubusercontent.com/docker/compose/v5.4.0/LICENSE',
      license_sha256: 'a'.repeat(64),
    }),
  ]),
  vm_image: Object.freeze({
    version: '0.10.4',
    repository: 'abiosoft/colima-core',
    asset_name: 'ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz',
    source_url: 'https://github.com/abiosoft/colima-core/releases/download/v0.10.4/ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz',
    sha256: 'b'.repeat(64),
    size_bytes: 332_354_401,
  }),
  ownership: Object.freeze({
    profile: 'ancestryllm-local-arm64',
    context: 'colima-ancestryllm-local-arm64',
  }),
  isolation: Object.freeze({
    loopback_only: true,
    kubernetes: false,
    privileged_containers: false,
    renderer_socket_access: false,
    container_socket_access: false,
    cross_profile_socket_access: false,
  }),
}) satisfies LocalRuntimePreview['review']
const localRuntimePreview = result({
  schema_version: 1,
  operation: 'setup',
  offline: false,
  actions: [{ code: 'RUNTIME_INSTALL_COMPONENTS' }],
  confirmation_phrase: 'SET UP LOCAL RUNTIME',
  preserves_data: true,
  deletes_data: false,
  plan_revision: 'a'.repeat(64),
  status: localRuntimeStatus,
  review: localRuntimeReview,
}) satisfies BridgeResult<LocalRuntimePreview>

const runningJob = Object.freeze({
  schema_version: 1,
  sequence: 1,
  job_id: 'j000001',
  name: 'Export fictional tree',
  state: 'running',
  submitted_at: '2026-08-12T12:00:00Z',
  started_at: '2026-08-12T12:00:01Z',
  finished_at: null,
  resource_refs: Object.freeze([]),
  artifact: null,
  outcome_summary: null,
  next_action: null,
  error_code: null,
  error_message: null,
  error_remediation: null,
  progress: Object.freeze({
    schema_version: 1,
    operation: 'Writing records',
    timestamp: '2026-08-12T12:00:02Z',
    completed: 1,
    total: 4,
  }),
  cancellation_requested_at: null,
  cancellation_deferred_by: null,
}) satisfies JobSnapshot
const jobList = result(Object.freeze({
  schema_version: 1,
  jobs: Object.freeze([runningJob]),
})) satisfies BridgeResult<JobList>
const progressEvent = Object.freeze({
  schema_version: 1,
  sequence: 2,
  kind: 'progress',
  created_at: '2026-08-12T12:00:03Z',
  snapshot: Object.freeze({
    ...runningJob,
    sequence: 2,
    progress: Object.freeze({
      ...runningJob.progress,
      timestamp: '2026-08-12T12:00:03Z',
      completed: 2,
    }),
  }),
}) satisfies JobEvent
const subscriptionId = `sub_${'a'.repeat(32)}`
const chatSessionId = `chat_${'c'.repeat(32)}`
const chatRunId = `run_${'d'.repeat(32)}`
const chatCapability = Object.freeze({
  schema_version: 1,
  max_active_sessions: 32,
  max_messages: 32,
  max_message_characters: 16_384,
  max_context_characters: 65_536,
  max_output_tokens: 4_096,
  max_temperature: 1,
  max_timeout_seconds: 120,
  max_safe_retries: 1,
  transient: true,
  tools_enabled: false,
  payload_retention: false,
  output_is_evidence: false,
  streaming: true,
  stream_replay_max_bytes: 262_144,
} satisfies ChatCapability)
const chatCreateRequest = Object.freeze({
  schema_version: 1 as const,
  provider_profile_name: 'local-test',
  model: 'fictional-model',
  purpose: 'genealogy_analysis' as const,
  data_classes: Object.freeze(['public_genealogy', 'deceased_person'] as const),
  consent_name: null,
})
const chatSession = Object.freeze({
  schema_version: 1,
  session_id: chatSessionId,
  provider_profile_name: chatCreateRequest.provider_profile_name,
  provider_id: 'ollama',
  model: chatCreateRequest.model,
  purpose: chatCreateRequest.purpose,
  data_classes: chatCreateRequest.data_classes,
  remote: false,
  consent_name: null,
  message_count: 0,
  transient: true,
  payload_retention: false,
} satisfies ChatSession)
const chatSessionRequest = Object.freeze({
  schema_version: 1 as const,
  session_id: chatSessionId,
})
const chatClosure = Object.freeze({
  schema_version: 1 as const,
  session_id: chatSessionId,
  closed: true as const,
})
const activeChatRun = Object.freeze({
  schema_version: 1,
  session_id: chatSessionId,
  run_id: chatRunId,
  state: 'active',
  latest_sequence: 1,
  terminal: false,
} satisfies ChatStreamRun)
const interruptedChatRun = Object.freeze({
  ...activeChatRun,
  state: 'interrupted',
  latest_sequence: 2,
  terminal: true,
} satisfies ChatStreamRun)
const chatStartRequest = Object.freeze({
  schema_version: 1 as const,
  session_id: chatSessionId,
  message: 'Summarize the fictional family.',
  max_output_tokens: 256,
  temperature: 0.2,
  timeout_seconds: 30,
  max_safe_retries: 1,
})

function chatEvent(sequence: number, type: ChatEvent['type']): Readonly<ChatEvent> {
  return Object.freeze({
    schema_version: 1,
    run_id: chatRunId,
    sequence,
    type,
    timestamp: `2026-08-13T12:00:00.${String(sequence).padStart(6, '0')}+00:00`,
    payload: Object.freeze({
      text: null,
      code: null,
      provider_id: type === 'active' ? 'openai' : null,
      model: type === 'active' ? 'gpt-test' : null,
      remote: type === 'active' ? true : null,
      message_count: type === 'completed' ? 2 : null,
    }),
  })
}

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
  getLocalRuntimeStatus: vi.fn().mockResolvedValue(result(localRuntimeStatus)),
  previewLocalRuntime: vi.fn().mockResolvedValue(localRuntimePreview),
  applyLocalRuntime: vi.fn().mockResolvedValue(result({
    schema_version: 1,
    operation: 'setup',
    state: 'ready',
    code: 'RUNTIME_READY',
  })),
  getChatCapability: vi.fn().mockResolvedValue(result(chatCapability)),
  createChatSession: vi.fn().mockResolvedValue(result(chatSession)),
  closeChatSession: vi.fn().mockResolvedValue(result(chatClosure)),
  startChatStream: vi.fn().mockResolvedValue(result(activeChatRun)),
  cancelChatStream: vi.fn().mockResolvedValue(result(interruptedChatRun)),
  streamChatEvents: vi.fn().mockResolvedValue(undefined),
  listJobs: vi.fn().mockResolvedValue(jobList),
  getJob: vi.fn().mockResolvedValue(result(runningJob)),
  cancelJob: vi.fn().mockResolvedValue(result({
    ...runningJob,
    sequence: 2,
    state: 'cancelling',
    cancellation_requested_at: '2026-08-12T12:00:03Z',
  })),
  streamJobEvents: vi.fn().mockResolvedValue(undefined),
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

function nativeActionHarness() {
  const openExternalLink = vi.fn().mockResolvedValue(Object.freeze({ status: 'opened' as const }))
  const copyText = vi.fn().mockResolvedValue(undefined)
  const openDiagnosticsDirectory = vi.fn().mockResolvedValue(undefined)
  const clearDiagnostics = vi.fn().mockResolvedValue(undefined)
  return {
    actions: Object.freeze({
      openExternalLink,
      copyText,
      openDiagnosticsDirectory,
      clearDiagnostics,
    }) satisfies MainNativeActions,
    clearDiagnostics,
    copyText,
    openDiagnosticsDirectory,
    openExternalLink,
  }
}

class FakeWebContents extends EventEmitter {
  readonly id: number
  readonly sent: Array<Readonly<{ channel: string; args: readonly unknown[] }>> = []
  mainFrame: Readonly<{ url: string }>
  private destroyed = false

  constructor(id = 1, url = 'app://bundle/index.html') {
    super()
    this.id = id
    this.mainFrame = Object.freeze({ url })
  }

  isDestroyed(): boolean { return this.destroyed }

  send(channel: string, ...args: unknown[]): void {
    this.sent.push(Object.freeze({ channel, args: Object.freeze(args) }))
  }

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
  options: Readonly<RegistrationOptions> = {},
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
  it('registers exactly the thirty-eight declared static channels', () => {
    const handlers = new Map<string, Handler>()
    registerDesktopIpcHandlers(
      { handle: (channel, handler) => { handlers.set(channel, handler) } },
      bridge(),
      fileGrantBroker(),
    )
    expect([...handlers.keys()].sort()).toEqual(Object.values(desktopChannels).sort())
    expect(handlers.size).toBe(38)
  })

  it('routes strict native actions through the authorized main-process adapter', async () => {
    const native = nativeActionHarness()
    const { event, handlers } = harness(bridge(), { nativeActions: native.actions })
    const destination = 'https://example.org/research?q=family'

    await expect(handlers.get(desktopChannels.openExternalLink)?.(event(), {
      schema_version: 1,
      destination,
    })).resolves.toEqual(result({
      schema_version: 1,
      destination,
      status: 'opened',
    }))
    await expect(handlers.get(desktopChannels.copyText)?.(event(), {
      schema_version: 1,
      text: 'First line\nSecond line',
    })).resolves.toEqual(result({ schema_version: 1, copied: true }))
    expect(native.openExternalLink).toHaveBeenCalledWith(destination)
    expect(native.copyText).toHaveBeenCalledWith('First line\nSecond line')

    await expect(handlers.get(desktopChannels.openExternalLink)?.(event(), {
      schema_version: 1,
      destination: 'javascript:alert(1)',
    })).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    await expect(handlers.get(desktopChannels.copyText)?.(event(), {
      schema_version: 1,
      text: 'visible',
      html: '<strong>visible</strong>',
    })).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    expect(native.openExternalLink).toHaveBeenCalledTimes(1)
    expect(native.copyText).toHaveBeenCalledTimes(1)
  })

  it('exposes only fixed diagnostics-directory actions and rejects renderer-supplied paths', async () => {
    const native = nativeActionHarness()
    const { event, handlers } = harness(bridge(), { nativeActions: native.actions })

    await expect(handlers.get(desktopChannels.openDiagnosticsDirectory)?.(event())).resolves.toEqual(
      result({ schema_version: 1, opened: true }),
    )
    await expect(handlers.get(desktopChannels.clearDiagnostics)?.(event())).resolves.toEqual(
      result({ schema_version: 1, cleared: true }),
    )
    expect(native.openDiagnosticsDirectory).toHaveBeenCalledOnce()
    expect(native.clearDiagnostics).toHaveBeenCalledOnce()

    await expect(handlers.get(desktopChannels.openDiagnosticsDirectory)?.(
      event(),
      '/private/renderer-selected-path',
    )).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    await expect(handlers.get(desktopChannels.clearDiagnostics)?.(
      event(),
      { directory: '/private/renderer-selected-path' },
    )).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    expect(native.openDiagnosticsDirectory).toHaveBeenCalledOnce()
    expect(native.clearDiagnostics).toHaveBeenCalledOnce()
  })

  it('binds chat sessions and their streams to the renderer that created them', async () => {
    const control = bridge()
    vi.mocked(control.streamChatEvents).mockImplementation((_request, _next, signal) => (
      new Promise((resolve) => signal?.addEventListener('abort', () => resolve(), { once: true }))
    ))
    const { controller, event, handlers } = harness(control)
    const otherContents = new FakeWebContents(2)
    const releaseOther = controller.authorizeWebContents(
      otherContents,
      (url) => url === 'app://bundle/index.html',
    )
    const otherEvent = { sender: otherContents, senderFrame: otherContents.mainFrame }

    await expect(handlers.get(desktopChannels.getChatCapability)?.(event())).resolves.toEqual(
      result(chatCapability),
    )
    await expect(handlers.get(desktopChannels.createChatSession)?.(
      event(),
      chatCreateRequest,
    )).resolves.toEqual(result(chatSession))
    await expect(handlers.get(desktopChannels.startChatStream)?.(
      otherEvent,
      chatStartRequest,
    )).resolves.toMatchObject({ ok: false, error: { code: 'CHAT_SESSION_NOT_FOUND' } })
    expect(control.startChatStream).not.toHaveBeenCalled()

    await expect(handlers.get(desktopChannels.startChatStream)?.(
      event(),
      chatStartRequest,
    )).resolves.toEqual(result(activeChatRun))
    await expect(handlers.get(desktopChannels.closeChatSession)?.(
      event(),
      chatSessionRequest,
    )).resolves.toEqual(result(chatClosure))
    await expect(handlers.get(desktopChannels.startChatStream)?.(
      event(),
      chatStartRequest,
    )).resolves.toMatchObject({ ok: false, error: { code: 'CHAT_SESSION_NOT_FOUND' } })

    expect(control.createChatSession).toHaveBeenCalledWith(
      chatCreateRequest,
      expect.any(AbortSignal),
    )
    expect(control.closeChatSession).toHaveBeenCalledWith(
      chatSessionRequest,
      expect.any(AbortSignal),
    )
    expect(control.startChatStream).toHaveBeenCalledTimes(1)
    releaseOther()
    controller.dispose()
  })

  it('binds chat streams to one renderer and accepts exact delivered-batch acknowledgements', async () => {
    const control = bridge()
    let listener: ((event: Readonly<ChatEvent>, flow: Readonly<{
      pause(): void
      resume(): void
    }>) => void) | undefined
    let streamSignal: AbortSignal | undefined
    vi.mocked(control.streamChatEvents).mockImplementation((_request, next, signal) => {
      listener = next
      streamSignal = signal
      return new Promise((resolve) => signal?.addEventListener('abort', () => resolve(), { once: true }))
    })
    const { contents, event, handlers } = harness(control)

    await handlers.get(desktopChannels.createChatSession)?.(event(), chatCreateRequest)
    await expect(handlers.get(desktopChannels.startChatStream)?.(
      event(),
      chatStartRequest,
    )).resolves.toEqual(result(activeChatRun))
    expect(control.streamChatEvents).toHaveBeenCalledTimes(1)
    const [streamRequest, streamListener, receivedSignal] = vi.mocked(control.streamChatEvents).mock.calls[0] ?? []
    expect(streamRequest).toEqual({
      schema_version: 1,
      session_id: chatSessionId,
      run_id: chatRunId,
      after: 0,
    })
    expect(typeof streamListener).toBe('function')
    expect(receivedSignal).toBeInstanceOf(AbortSignal)

    const flow = Object.freeze({ pause: vi.fn(), resume: vi.fn() })
    listener?.(chatEvent(1, 'active'), flow)
    listener?.(chatEvent(2, 'completed'), flow)

    expect(contents.sent).toHaveLength(1)
    expect(contents.sent[0]).toMatchObject({
      channel: desktopEventChannels.chatEventBatch,
      args: [{
        kind: 'batch',
        session_id: chatSessionId,
        run_id: chatRunId,
        from_sequence: 1,
        through_sequence: 2,
        error: null,
      }],
    })
    await expect(handlers.get(desktopChannels.acknowledgeChatStream)?.(event(), {
      schema_version: 1,
      session_id: chatSessionId,
      run_id: chatRunId,
      through_sequence: 2,
    })).resolves.toEqual(result({
      schema_version: 1,
      session_id: chatSessionId,
      run_id: chatRunId,
      through_sequence: 2,
      acknowledged: true,
    }))
    expect(streamSignal?.aborted).toBe(true)
    expect(control.cancelChatStream).not.toHaveBeenCalled()
  })

  it('cancels renderer-owned chat work when the owner starts navigating away', async () => {
    const control = bridge()
    let streamSignal: AbortSignal | undefined
    vi.mocked(control.streamChatEvents).mockImplementation((_request, _next, signal) => {
      streamSignal = signal
      return new Promise((resolve) => signal?.addEventListener('abort', () => resolve(), { once: true }))
    })
    const { contents, event, handlers } = harness(control)

    await handlers.get(desktopChannels.createChatSession)?.(event(), chatCreateRequest)
    await handlers.get(desktopChannels.startChatStream)?.(event(), chatStartRequest)
    contents.startNavigation('https://attacker.invalid/')

    await vi.waitFor(() => expect(control.cancelChatStream).toHaveBeenCalledWith({
      schema_version: 1,
      session_id: chatSessionId,
      run_id: chatRunId,
    }))
    await vi.waitFor(() => expect(control.closeChatSession).toHaveBeenCalledWith(
      chatSessionRequest,
    ))
    expect(streamSignal?.aborted).toBe(true)
  })

  it('routes strict job list, detail, and cancellation requests through bounded operations', async () => {
    const control = bridge()
    const { event, handlers } = harness(control)
    const request = Object.freeze({ schema_version: 1 as const, job_id: runningJob.job_id })

    await expect(handlers.get(desktopChannels.listJobs)?.(event())).resolves.toEqual(jobList)
    await expect(handlers.get(desktopChannels.getJob)?.(event(), request)).resolves.toEqual(result(runningJob))
    await expect(handlers.get(desktopChannels.cancelJob)?.(event(), request)).resolves.toMatchObject({
      ok: true,
      data: { job_id: runningJob.job_id, state: 'cancelling' },
    })
    await expect(handlers.get(desktopChannels.getJob)?.(
      event(),
      { ...request, path: '/private/tree.ged' },
    )).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    await expect(handlers.get(desktopChannels.listJobs)?.(event(), 'surplus')).resolves.toMatchObject({
      ok: false,
      error: { code: 'INVALID_REQUEST' },
    })
    expect(control.listJobs).toHaveBeenCalledWith(expect.any(AbortSignal))
    expect(control.getJob).toHaveBeenCalledWith(request, expect.any(AbortSignal))
    expect(control.cancelJob).toHaveBeenCalledWith(request, expect.any(AbortSignal))
    expect(control.getJob).toHaveBeenCalledTimes(1)
  })

  it('binds job subscriptions to one renderer and aborts them on explicit unsubscribe', async () => {
    const control = bridge()
    let listener: ((event: Readonly<JobEvent>) => void) | undefined
    let streamSignal: AbortSignal | undefined
    vi.mocked(control.streamJobEvents).mockImplementation((_request, next, signal) => {
      listener = next
      streamSignal = signal
      return new Promise((resolve) => signal?.addEventListener('abort', () => resolve(), { once: true }))
    })
    const { contents, event, handlers } = harness(control)
    const request = Object.freeze({
      schema_version: 1 as const,
      subscription_id: subscriptionId,
      job_id: runningJob.job_id,
      after: 1,
    })

    await expect(handlers.get(desktopChannels.subscribeJobEvents)?.(event(), request)).resolves.toEqual(result({
      schema_version: 1,
      subscription_id: subscriptionId,
      job_id: runningJob.job_id,
      subscribed: true,
    }))
    await expect(handlers.get(desktopChannels.subscribeJobEvents)?.(event(), request)).resolves.toMatchObject({
      ok: false,
      error: { code: 'JOB_SUBSCRIPTION_CONFLICT' },
    })
    listener?.(progressEvent)
    listener?.(progressEvent)
    expect(contents.sent).toEqual([{
      channel: desktopEventChannels.jobEvent,
      args: [{
        schema_version: 1,
        kind: 'event',
        subscription_id: subscriptionId,
        job_id: runningJob.job_id,
        event: progressEvent,
        error: null,
      }],
    }])
    await expect(handlers.get(desktopChannels.unsubscribeJobEvents)?.(event(), {
      schema_version: 1,
      subscription_id: subscriptionId,
    })).resolves.toEqual(result({
      schema_version: 1,
      subscription_id: subscriptionId,
      unsubscribed: true,
    }))
    expect(streamSignal?.aborted).toBe(true)
    await expect(handlers.get(desktopChannels.unsubscribeJobEvents)?.(event(), {
      schema_version: 1,
      subscription_id: subscriptionId,
    })).resolves.toMatchObject({ ok: true })
  })

  it('closes a job subscription immediately after its terminal event', async () => {
    const control = bridge()
    let listener: ((event: Readonly<JobEvent>) => void) | undefined
    let streamSignal: AbortSignal | undefined
    vi.mocked(control.streamJobEvents).mockImplementation((_request, next, signal) => {
      listener = next
      streamSignal = signal
      return new Promise((resolve) => signal?.addEventListener('abort', () => resolve(), { once: true }))
    })
    const { contents, event, handlers } = harness(control)
    const request = Object.freeze({
      schema_version: 1 as const,
      subscription_id: subscriptionId,
      job_id: runningJob.job_id,
      after: 1,
    })

    await handlers.get(desktopChannels.subscribeJobEvents)?.(event(), request)
    listener?.({
      schema_version: 1,
      sequence: 2,
      kind: 'terminal',
      created_at: '2026-08-12T12:00:04Z',
      snapshot: {
        ...runningJob,
        sequence: 2,
        state: 'completed',
        finished_at: '2026-08-12T12:00:04Z',
      },
    })

    expect(contents.sent).toHaveLength(1)
    expect(contents.sent[0]).toMatchObject({
      channel: desktopEventChannels.jobEvent,
      args: [{ kind: 'event', event: { kind: 'terminal' } }],
    })
    expect(streamSignal?.aborted).toBe(true)
    await expect(handlers.get(desktopChannels.unsubscribeJobEvents)?.(event(), {
      schema_version: 1,
      subscription_id: subscriptionId,
    })).resolves.toMatchObject({ ok: true })
  })

  it('fails a malformed job stream closed without exposing event data', async () => {
    const control = bridge()
    let listener: ((event: Readonly<JobEvent>) => void) | undefined
    let streamSignal: AbortSignal | undefined
    vi.mocked(control.streamJobEvents).mockImplementation((_request, next, signal) => {
      listener = next
      streamSignal = signal
      return new Promise((resolve) => signal?.addEventListener('abort', () => resolve(), { once: true }))
    })
    const { contents, event, handlers } = harness(control)
    const request = {
      schema_version: 1,
      subscription_id: subscriptionId,
      job_id: runningJob.job_id,
      after: 1,
    }

    await handlers.get(desktopChannels.subscribeJobEvents)?.(event(), request)
    listener?.({
      ...progressEvent,
      snapshot: { ...progressEvent.snapshot, error_message: 'secret-marker\u0000invalid' },
    } as never)

    expect(streamSignal?.aborted).toBe(true)
    expect(contents.sent).toHaveLength(1)
    expect(contents.sent[0]).toMatchObject({
      channel: desktopEventChannels.jobEvent,
      args: [{ kind: 'failure', error: { code: 'JOB_EVENT_STREAM_FAILED' } }],
    })
    expect(JSON.stringify(contents.sent)).not.toContain('secret-marker')
  })

  it('preserves the stable replay-expiration code when a job stream rejects', async () => {
    const control = bridge()
    vi.mocked(control.streamJobEvents).mockRejectedValue(
      new SidecarClientError('job_event_replay_expired'),
    )
    const { contents, event, handlers } = harness(control)
    const request = {
      schema_version: 1,
      subscription_id: subscriptionId,
      job_id: runningJob.job_id,
      after: 1,
    }

    await handlers.get(desktopChannels.subscribeJobEvents)?.(event(), request)

    await vi.waitFor(() => expect(contents.sent).toContainEqual({
      channel: desktopEventChannels.jobEvent,
      args: [expect.objectContaining({
        kind: 'failure',
        error: expect.objectContaining({ code: 'JOB_EVENT_REPLAY_EXPIRED' }),
      })],
    }))
  })

  it('aborts renderer job streams on main-frame navigation and sidecar replacement', async () => {
    const control = bridge()
    const signals: AbortSignal[] = []
    vi.mocked(control.streamJobEvents).mockImplementation((_request, _next, signal) => {
      if (signal) signals.push(signal)
      return new Promise((resolve) => signal?.addEventListener('abort', () => resolve(), { once: true }))
    })
    const { contents, controller, event, handlers } = harness(control)
    const subscribe = handlers.get(desktopChannels.subscribeJobEvents)
    const request = (suffix: string) => ({
      schema_version: 1,
      subscription_id: `sub_${suffix.repeat(32)}`,
      job_id: runningJob.job_id,
      after: 1,
    })

    await subscribe?.(event(), request('b'))
    contents.navigate()
    expect(signals[0]?.aborted).toBe(true)
    await subscribe?.(event(), request('c'))
    controller.invalidateSidecarSession()
    expect(signals[1]?.aborted).toBe(true)
    expect(contents.sent).toContainEqual({
      channel: desktopEventChannels.jobEvent,
      args: [expect.objectContaining({
        kind: 'failure',
        subscription_id: `sub_${'c'.repeat(32)}`,
        error: expect.objectContaining({ code: 'JOB_SUBSCRIPTION_CLOSED' }),
      })],
    })
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
