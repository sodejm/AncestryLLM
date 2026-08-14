/** Verifies the development mock bridge mirrors the versioned desktop contract. */
import { describe, expect, it, vi } from 'vitest'
import { createMockAncestryBridge } from './desktop'

describe('versioned mock bridge', () => {
  it('exposes exactly thirty-eight deterministic, deeply frozen methods', async () => {
    const bridge = createMockAncestryBridge('success')
    expect(Object.keys(bridge).sort()).toEqual([
      'acknowledgeChatStream',
      'cancelChatStream',
      'closeChatSession',
      'copyText',
      'createConsent',
      'createChatSession',
      'createProviderProfile',
      'deleteSecret',
      'getAppInfo',
      'getCapabilities',
      'getChatCapability',
      'getLocalRuntimeStatus',
      'getJob',
      'getPreferences',
      'getProviderConfiguration',
      'getSecretStatus',
      'getSettings',
      'getStartupDiagnostics',
      'previewConsent',
      'previewLocalRuntime',
      'listJobs',
      'requestOpenFileGrant',
      'requestSaveFileGrant',
      'retrySidecar',
      'revokeConsent',
      'revokeFileGrant',
      'setSecret',
      'startChatStream',
      'subscribeJobEvents',
      'unsubscribeJobEvents',
      'onJobEvent',
      'onChatEventBatch',
      'openExternalLink',
      'cancelJob',
      'updatePreferences',
      'updateSettings',
      'validateProviderEndpoint',
      'applyLocalRuntime',
    ].sort())
    expect(await bridge.getStartupDiagnostics()).toEqual(await bridge.getStartupDiagnostics())
    expect(Object.isFrozen(await bridge.getCapabilities())).toBe(true)
  })

  it('streams safe cancellation states once and retains the terminal backend snapshot', async () => {
    const bridge = createMockAncestryBridge('success')
    const listed = await bridge.listJobs()
    if (!listed.ok) throw new Error('Expected mock task list')
    const active = listed.data.jobs.find((job) => job.state === 'running')
    if (active === undefined) throw new Error('Expected an active mock task')

    const states: string[] = []
    const removeListener = bridge.onJobEvent((delivery) => {
      if (delivery.kind === 'event') states.push(delivery.event.snapshot.state)
    })
    const subscriptionId = `sub_${'1'.repeat(32)}`
    await expect(bridge.subscribeJobEvents({
      schema_version: 1,
      subscription_id: subscriptionId,
      job_id: active.job_id,
      after: active.sequence,
    })).resolves.toMatchObject({ ok: true, data: { subscribed: true } })

    await expect(bridge.cancelJob({ schema_version: 1, job_id: active.job_id }))
      .resolves.toMatchObject({ ok: true, data: { state: 'cancelling' } })
    await vi.waitFor(() => expect(states).toEqual([
      'cancelling',
      'pending-safe-point',
      'cancelled',
    ]))
    await expect(bridge.getJob({ schema_version: 1, job_id: active.job_id }))
      .resolves.toMatchObject({ ok: true, data: { state: 'cancelled', sequence: 4 } })
    await expect(bridge.cancelJob({ schema_version: 1, job_id: active.job_id }))
      .resolves.toMatchObject({ ok: true, data: { state: 'cancelled', sequence: 4 } })
    expect(states.filter((state) => state === 'cancelled')).toHaveLength(1)

    await bridge.unsubscribeJobEvents({ schema_version: 1, subscription_id: subscriptionId })
    removeListener()
  })

  it('models explicit preview confirmation and local-runtime state transitions', async () => {
    const bridge = createMockAncestryBridge('success')
    const preview = await bridge.previewLocalRuntime({
      schema_version: 1,
      operation: 'setup',
      offline: false,
    })
    expect(preview).toMatchObject({
      ok: true,
      data: {
        confirmation_phrase: 'SET UP LOCAL RUNTIME',
        status: { state: 'not-installed' },
      },
    })
    if (!preview.ok) throw new Error('Expected runtime preview')
    await expect(bridge.applyLocalRuntime({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.data.plan_revision,
      confirmation: preview.data.confirmation_phrase,
    })).resolves.toMatchObject({ ok: true, data: { state: 'ready' } })
    await expect(bridge.getLocalRuntimeStatus()).resolves.toMatchObject({
      ok: true,
      data: { state: 'ready' },
    })
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
