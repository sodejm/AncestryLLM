import { describe, expect, it } from 'vitest'
import { createMockAncestryBridge } from './desktop'

describe('versioned mock bridge', () => {
  it('exposes exactly six deterministic, deeply frozen methods', async () => {
    const bridge = createMockAncestryBridge('success')
    expect(Object.keys(bridge).sort()).toEqual([
      'getAppInfo',
      'getCapabilities',
      'getPreferences',
      'getStartupDiagnostics',
      'retrySidecar',
      'updatePreferences',
    ])
    expect(await bridge.getStartupDiagnostics()).toEqual(await bridge.getStartupDiagnostics())
    expect(Object.isFrozen(await bridge.getCapabilities())).toBe(true)
  })

  it('supports degraded, retry, unavailable-sidecar, and revision-conflict fixtures', async () => {
    const bridge = createMockAncestryBridge('unavailable')
    expect(await bridge.getStartupDiagnostics()).toMatchObject({ ok: true, data: { state: 'degraded' } })
    expect(await bridge.getCapabilities()).toMatchObject({ ok: false, error: { code: 'SIDECAR_UNAVAILABLE' } })
    expect(await bridge.retrySidecar()).toMatchObject({ ok: true, data: { state: 'ready' } })
    expect(await bridge.updatePreferences({ expectedRevision: 0, colorScheme: 'dark' })).toMatchObject({ ok: true, data: { revision: 1 } })
    expect(await bridge.updatePreferences({ expectedRevision: 0, colorScheme: 'light' })).toMatchObject({ ok: false, error: { code: 'PREFERENCES_CONFLICT' } })
  })
})
