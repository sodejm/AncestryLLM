import { describe, expect, it } from 'vitest'
import { createMockAncestryBridge } from './desktop'

describe('versioned mock bridge', () => {
  it('exposes exactly fourteen deterministic, deeply frozen methods', async () => {
    const bridge = createMockAncestryBridge('success')
    expect(Object.keys(bridge).sort()).toEqual([
      'deleteSecret',
      'getAppInfo',
      'getCapabilities',
      'getPreferences',
      'getSecretStatus',
      'getSettings',
      'getStartupDiagnostics',
      'requestOpenFileGrant',
      'requestSaveFileGrant',
      'retrySidecar',
      'revokeFileGrant',
      'setSecret',
      'updatePreferences',
      'updateSettings',
    ])
    expect(await bridge.getStartupDiagnostics()).toEqual(await bridge.getStartupDiagnostics())
    expect(Object.isFrozen(await bridge.getCapabilities())).toBe(true)
  })

  it('models atomic settings revisions and status-only secret lifecycle responses', async () => {
    const bridge = createMockAncestryBridge('success')
    const settings = await bridge.getSettings()
    expect(settings).toMatchObject({ ok: true, data: { revision: 0 } })
    await expect(bridge.updateSettings({
      schema_version: 1,
      expected_revision: 0,
      changes: { 'limits.max_query_rows': 250 },
    })).resolves.toMatchObject({ ok: true, data: { revision: 1 } })
    await expect(bridge.updateSettings({
      schema_version: 1,
      expected_revision: 0,
      changes: { 'limits.max_query_rows': 500 },
    })).resolves.toMatchObject({ ok: false, error: { code: 'SETTINGS_CONFLICT' } })

    const canary = 'credential-value-that-must-not-survive'
    const written = await bridge.setSecret({ reference: 'openai.api_key', value: canary })
    const status = await bridge.getSecretStatus({ reference: 'openai.api_key' })
    const deleted = await bridge.deleteSecret({ reference: 'openai.api_key' })
    const absent = await bridge.getSecretStatus({ reference: 'openai.api_key' })
    expect(written).toMatchObject({ ok: true, data: { status: 'present' } })
    expect(status).toMatchObject({ ok: true, data: { status: 'present' } })
    expect(deleted).toMatchObject({ ok: true, data: { status: 'missing' } })
    expect(absent).toMatchObject({ ok: true, data: { status: 'missing' } })
    expect(JSON.stringify([written, status, deleted, absent])).not.toContain(canary)
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
