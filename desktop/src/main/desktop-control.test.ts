import { describe, expect, it, vi } from 'vitest'
import { createDesktopControlBridge, MemoryPreferencesStore } from './desktop-control'

const manifest = {
  api: { namespace: '/api/v1', contract: 'ancestryllm.internal-api/1', application_contract: 'ancestryllm.application/0.3' },
  modules: [],
  request_policy: { max_body_bytes: 1_048_576, max_json_depth: 16, max_collection_items: 1_000, max_string_characters: 65_536 },
  pagination: { default_limit: 25, maximum_limit: 100, maximum_cursor_characters: 256 },
} as const

describe('desktop control bridge', () => {
  it('returns safe app information, diagnostics, capabilities, and preferences', async () => {
    const supervisor = {
      diagnostics: () => ({ state: 'ready' as const, failure: null, automaticRestartsRemaining: 2, manualRetriesRemaining: 1 }),
      retry: vi.fn().mockResolvedValue(true),
    }
    const client = { getCapabilities: vi.fn().mockResolvedValue(manifest) }
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' },
      supervisor,
      capabilitiesClient: client,
      preferences: new MemoryPreferencesStore(),
    })

    await expect(bridge.getAppInfo()).resolves.toMatchObject({ ok: true })
    await expect(bridge.getStartupDiagnostics()).resolves.toEqual({ ok: true, protocolVersion: '1', data: { state: 'ready', failure: null, automaticRestartsRemaining: 2, manualRetriesRemaining: 1 } })
    await expect(bridge.getCapabilities()).resolves.toEqual({ ok: true, protocolVersion: '1', data: manifest })
    await expect(bridge.getPreferences()).resolves.toMatchObject({ ok: true, data: { colorScheme: 'system', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 0 } })
  })

  it('supports a bounded retry and reports the resulting degraded state', async () => {
    const diagnostics = vi.fn().mockReturnValue({ state: 'unavailable', failure: 'crash_loop', automaticRestartsRemaining: 0, manualRetriesRemaining: 0 })
    const retry = vi.fn().mockResolvedValue(false)
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' },
      supervisor: { diagnostics, retry },
      capabilitiesClient: { getCapabilities: vi.fn() },
      preferences: new MemoryPreferencesStore(),
    })

    await expect(bridge.retrySidecar()).resolves.toEqual({ ok: true, protocolVersion: '1', data: { state: 'degraded', failure: 'crash_loop', automaticRestartsRemaining: 0, manualRetriesRemaining: 0 } })
    expect(retry).toHaveBeenCalledOnce()
  })

  it('maps an unavailable sidecar to a stable serializable error', async () => {
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'packaged' },
      supervisor: {
        diagnostics: () => ({ state: 'unavailable' as const, failure: 'startup_failed' as const, automaticRestartsRemaining: 0, manualRetriesRemaining: 1 }),
        retry: vi.fn(),
      },
      capabilitiesClient: { getCapabilities: vi.fn().mockRejectedValue(new Error('Bearer abc and port 54321 failed')) },
      preferences: new MemoryPreferencesStore(),
    })

    const result = await bridge.getCapabilities()
    expect(result).toEqual({ ok: false, protocolVersion: '1', error: { code: 'SIDECAR_UNAVAILABLE', message: 'The private service is unavailable.', remediation: 'Retry the service or restart AncestryLLM.' } })
    expect(JSON.stringify(result)).not.toMatch(/abc|54321|Bearer/i)
  })

  it('updates preferences through a main-owned store and increments revision', async () => {
    const bridge = createDesktopControlBridge({
      appInfo: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' },
      supervisor: { diagnostics: () => ({ state: 'idle' as const, failure: null, automaticRestartsRemaining: 0, manualRetriesRemaining: 0 }), retry: vi.fn() },
      capabilitiesClient: { getCapabilities: vi.fn() },
      preferences: new MemoryPreferencesStore(),
    })
    await expect(bridge.updatePreferences({ expectedRevision: 0, colorScheme: 'dark', reducedMotion: true })).resolves.toMatchObject({ ok: true, data: { colorScheme: 'dark', reducedMotion: true, revision: 1 } })
    await expect(bridge.updatePreferences({ expectedRevision: 0, colorScheme: 'light' })).resolves.toMatchObject({ ok: false, error: { code: 'PREFERENCES_CONFLICT' } })
  })
})
