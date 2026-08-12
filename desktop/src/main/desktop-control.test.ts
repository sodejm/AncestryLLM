import { describe, expect, it, vi } from 'vitest'
import { createDesktopControlBridge, MemoryPreferencesStore } from './desktop-control'
import type { SidecarClient } from './sidecar-client'

const manifest = {
  api: { namespace: '/api/v1', contract: 'ancestryllm.internal-api/1', application_contract: 'ancestryllm.application/0.3' },
  modules: [],
  request_policy: { max_body_bytes: 1_048_576, max_json_depth: 16, max_collection_items: 1_000, max_string_characters: 65_536 },
  pagination: { default_limit: 25, maximum_limit: 100, maximum_cursor_characters: 256 },
} as const

const startupReport = {
  schema_version: 1 as const,
  status: 'ready' as const,
  platform: { operating_system: 'macos' as const, architecture: 'arm64' as const },
  components: [
    { component: 'configuration' as const, status: 'ready' as const, code: 'CONFIGURATION_READY', message: 'Configuration is ready.', remediation: null, restart_required: false, blocks_mutations: false },
    { component: 'sqlcipher' as const, status: 'ready' as const, code: 'SQLCIPHER_READY', message: 'SQLCipher is ready.', remediation: null, restart_required: false, blocks_mutations: false },
    { component: 'keyring' as const, status: 'ready' as const, code: 'KEYRING_READY', message: 'Credential storage is ready.', remediation: null, restart_required: false, blocks_mutations: false },
    { component: 'workspace' as const, status: 'ready' as const, code: 'DATABASE_DIRECTORY_READY', message: 'Workspace is ready.', remediation: null, restart_required: false, blocks_mutations: false },
  ],
} as const

const blockedStartupReport = {
  ...startupReport,
  status: 'degraded' as const,
  components: [
    { component: 'configuration' as const, status: 'blocked' as const, code: 'CONFIG_INVALID', message: 'Configuration is invalid.', remediation: 'Repair config.toml and retry.', restart_required: false, blocks_mutations: true },
    ...startupReport.components.slice(1),
  ],
} as const

const sidecarClient = (
  overrides: Partial<SidecarClient> = {},
): SidecarClient => ({
  getStartupDiagnostics: vi.fn().mockResolvedValue(startupReport),
  getCapabilities: vi.fn().mockResolvedValue(manifest),
  getSettings: vi.fn(),
  updateSettings: vi.fn(),
  getProviderConfiguration: vi.fn(),
  createProviderProfile: vi.fn(),
  validateProviderEndpoint: vi.fn(),
  previewConsent: vi.fn(),
  createConsent: vi.fn(),
  revokeConsent: vi.fn(),
  getSecretStatus: vi.fn(),
  setSecret: vi.fn(),
  deleteSecret: vi.fn(),
  ...overrides,
})

describe('desktop control bridge', () => {
  it('returns safe app information, diagnostics, capabilities, and preferences', async () => {
    const supervisor = {
      diagnostics: () => ({ state: 'ready' as const, failure: null, automaticRestartsRemaining: 2, manualRetriesRemaining: 1 }),
      retry: vi.fn().mockResolvedValue(true),
    }
    const client = sidecarClient()
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' },
      supervisor,
      sidecarClient: client,
      preferences: new MemoryPreferencesStore(),
    })

    await expect(bridge.getAppInfo()).resolves.toMatchObject({ ok: true })
    await expect(bridge.getStartupDiagnostics()).resolves.toEqual({ ok: true, protocolVersion: '1', data: { state: 'ready', failure: null, automaticRestartsRemaining: 2, manualRetriesRemaining: 1, report: startupReport } })
    await expect(bridge.getCapabilities()).resolves.toEqual({ ok: true, protocolVersion: '1', data: manifest })
    await expect(bridge.getPreferences()).resolves.toMatchObject({ ok: true, data: { colorScheme: 'system', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 0 } })
  })

  it('supports a bounded retry and reports the resulting degraded state', async () => {
    const diagnostics = vi.fn().mockReturnValue({ state: 'unavailable', failure: 'crash_loop', automaticRestartsRemaining: 0, manualRetriesRemaining: 0 })
    const retry = vi.fn().mockResolvedValue(false)
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' },
      supervisor: { diagnostics, retry },
      sidecarClient: sidecarClient(),
      preferences: new MemoryPreferencesStore(),
    })

    await expect(bridge.retrySidecar()).resolves.toEqual({ ok: true, protocolVersion: '1', data: { state: 'degraded', failure: 'crash_loop', automaticRestartsRemaining: 0, manualRetriesRemaining: 0, report: null } })
    expect(retry).toHaveBeenCalledOnce()
  })

  it('maps an unavailable sidecar to a stable serializable error', async () => {
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'packaged' },
      supervisor: {
        diagnostics: () => ({ state: 'unavailable' as const, failure: 'startup_failed' as const, automaticRestartsRemaining: 0, manualRetriesRemaining: 1 }),
        retry: vi.fn(),
      },
      sidecarClient: sidecarClient({ getCapabilities: vi.fn().mockRejectedValue(new Error('Bearer abc and port 54321 failed')) }),
      preferences: new MemoryPreferencesStore(),
    })

    const result = await bridge.getCapabilities()
    expect(result).toEqual({ ok: false, protocolVersion: '1', error: { code: 'SIDECAR_UNAVAILABLE', message: 'The private service is unavailable.', remediation: 'Retry the service or restart AncestryLLM.' } })
    expect(JSON.stringify(result)).not.toMatch(/abc|54321|Bearer/i)
  })

  it('propagates cancellation to the sidecar client without converting it to a renderer error', async () => {
    const cancelled = new Error('private cancellation reason')
    const client = sidecarClient({
      getCapabilities: vi.fn((signal?: AbortSignal) => new Promise<typeof manifest>((_resolve, reject) => {
        signal?.addEventListener('abort', () => reject(signal.reason), { once: true })
      })),
    })
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'packaged' },
      supervisor: {
        diagnostics: () => ({ state: 'ready' as const, failure: null, automaticRestartsRemaining: 0, manualRetriesRemaining: 0 }),
        retry: vi.fn(),
      },
      sidecarClient: client,
      preferences: new MemoryPreferencesStore(),
    })
    const controller = new AbortController()
    const request = bridge.getCapabilities(controller.signal)

    controller.abort(cancelled)

    await expect(request).rejects.toBe(cancelled)
    expect(client.getCapabilities).toHaveBeenCalledWith(controller.signal)
  })

  it('updates preferences through a main-owned store and increments revision', async () => {
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' },
      supervisor: { diagnostics: () => ({ state: 'ready' as const, failure: null, automaticRestartsRemaining: 0, manualRetriesRemaining: 0 }), retry: vi.fn() },
      sidecarClient: sidecarClient(),
      preferences: new MemoryPreferencesStore(),
    })
    await expect(bridge.updatePreferences({ expectedRevision: 0, colorScheme: 'dark', reducedMotion: true })).resolves.toMatchObject({ ok: true, data: { colorScheme: 'dark', reducedMotion: true, revision: 1 } })
    await expect(bridge.updatePreferences({ expectedRevision: 0, colorScheme: 'light' })).resolves.toMatchObject({ ok: false, error: { code: 'PREFERENCES_CONFLICT' } })
  })

  it('blocks every renderer mutation when startup diagnostics are degraded', async () => {
    const client = sidecarClient({
      getStartupDiagnostics: vi.fn().mockResolvedValue(blockedStartupReport),
      updateSettings: vi.fn(),
      setSecret: vi.fn(),
      deleteSecret: vi.fn(),
    })
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'packaged' },
      supervisor: { diagnostics: () => ({ state: 'ready' as const, failure: null, automaticRestartsRemaining: 0, manualRetriesRemaining: 1 }), retry: vi.fn() },
      sidecarClient: client,
      preferences: new MemoryPreferencesStore(),
    })

    await expect(bridge.updatePreferences({ expectedRevision: 0, colorScheme: 'dark' })).resolves.toMatchObject({ ok: false, error: { code: 'STARTUP_MUTATION_BLOCKED' } })
    await expect(bridge.updateSettings({ schema_version: 1, expected_revision: 0, changes: { 'providers.default': 'none' } })).resolves.toMatchObject({ ok: false, error: { code: 'STARTUP_MUTATION_BLOCKED' } })
    await expect(bridge.setSecret({ reference: 'openai.api_key', value: 'never-written' })).resolves.toMatchObject({ ok: false, error: { code: 'STARTUP_MUTATION_BLOCKED' } })
    await expect(bridge.deleteSecret({ reference: 'openai.api_key' })).resolves.toMatchObject({ ok: false, error: { code: 'STARTUP_MUTATION_BLOCKED' } })
    await expect(bridge.getPreferences()).resolves.toMatchObject({ ok: true, data: { revision: 0, colorScheme: 'system' } })
    expect(client.updateSettings).not.toHaveBeenCalled()
    expect(client.setSecret).not.toHaveBeenCalled()
    expect(client.deleteSecret).not.toHaveBeenCalled()
  })
})
