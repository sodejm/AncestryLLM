import { describe, expect, it, vi } from 'vitest'
import type { AncestryBridge } from '../shared-contract/desktop'
import { desktopChannels } from '../shared-contract/desktop'
import { registerDesktopIpcHandlers } from './ipc-handlers'

const result = <T>(data: T) => ({ ok: true as const, protocolVersion: '1' as const, data })
const bridge = (): AncestryBridge => ({
  getAppInfo: vi.fn().mockResolvedValue(result({ applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' })),
  getStartupDiagnostics: vi.fn().mockResolvedValue(result({ state: 'ready', failure: null, automaticRestartsRemaining: 1, manualRetriesRemaining: 1 })),
  getCapabilities: vi.fn().mockResolvedValue(result({ api: { namespace: '/api/v1', contract: 'ancestryllm.internal-api/1', application_contract: 'ancestryllm.application/0.3' }, modules: [], request_policy: { max_body_bytes: 1, max_json_depth: 1, max_collection_items: 1, max_string_characters: 1 }, pagination: { default_limit: 1, maximum_limit: 1, maximum_cursor_characters: 32 } })),
  retrySidecar: vi.fn().mockResolvedValue(result({ state: 'ready', failure: null, automaticRestartsRemaining: 1, manualRetriesRemaining: 0 })),
  getPreferences: vi.fn().mockResolvedValue(result({ colorScheme: 'system', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 0 })),
  updatePreferences: vi.fn().mockResolvedValue(result({ colorScheme: 'dark', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 1 })),
})

describe('desktop IPC handlers', () => {
  it('registers exactly the six declared channels', () => {
    const handlers = new Map<string, (event: unknown, ...args: unknown[]) => Promise<unknown>>()
    registerDesktopIpcHandlers({ handle: (channel, handler) => { handlers.set(channel, handler) } }, bridge(), () => true)
    expect([...handlers.keys()].sort()).toEqual(Object.values(desktopChannels).sort())
    expect(handlers.size).toBe(6)
  })

  it('returns a coded error for an unauthorized sender without invoking main control', async () => {
    const control = bridge()
    const handlers = new Map<string, (event: unknown, ...args: unknown[]) => Promise<unknown>>()
    registerDesktopIpcHandlers({ handle: (channel, handler) => { handlers.set(channel, handler) } }, control, () => false)
    await expect(handlers.get(desktopChannels.getCapabilities)?.({})).resolves.toEqual({ ok: false, protocolVersion: '1', error: { code: 'UNAUTHORIZED_SENDER', message: 'The desktop request was denied.', remediation: 'Reload the AncestryLLM window.' } })
    expect(control.getCapabilities).not.toHaveBeenCalled()
  })

  it('rejects invalid or surplus payloads before invoking main control', async () => {
    const control = bridge()
    const handlers = new Map<string, (event: unknown, ...args: unknown[]) => Promise<unknown>>()
    registerDesktopIpcHandlers({ handle: (channel, handler) => { handlers.set(channel, handler) } }, control, () => true)
    await expect(handlers.get(desktopChannels.updatePreferences)?.({}, { expectedRevision: 0, colorScheme: 'sepia' })).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    await expect(handlers.get(desktopChannels.updatePreferences)?.({}, { colorScheme: 'dark' })).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    await expect(handlers.get(desktopChannels.getAppInfo)?.({}, 'surplus')).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_REQUEST' } })
    expect(control.updatePreferences).not.toHaveBeenCalled()
    expect(control.getAppInfo).not.toHaveBeenCalled()
  })

  it('validates the runtime response before returning it to preload', async () => {
    const control = bridge()
    vi.mocked(control.getAppInfo).mockResolvedValueOnce(result({ applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development', token: 'secret' }) as never)
    const handlers = new Map<string, (event: unknown, ...args: unknown[]) => Promise<unknown>>()
    registerDesktopIpcHandlers({ handle: (channel, handler) => { handlers.set(channel, handler) } }, control, () => true)
    await expect(handlers.get(desktopChannels.getAppInfo)?.({})).resolves.toMatchObject({ ok: false, error: { code: 'INVALID_RESPONSE' } })
  })
})
