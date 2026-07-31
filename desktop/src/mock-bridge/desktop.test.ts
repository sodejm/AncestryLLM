import { describe, expect, it } from 'vitest'
import { createMockAncestryBridge } from './desktop'

describe('versioned mock bridge', () => {
  it('returns the same deeply frozen fictional success fixture', async () => {
    const bridge = createMockAncestryBridge('success')
    expect(await bridge.startup()).toEqual(await bridge.startup())
    expect(await bridge.startup()).toMatchObject({ ok: true, protocolVersion: '1' })
    expect(Object.isFrozen(await bridge.startup())).toBe(true)
  })
  it('returns a deterministic sanitized failure fixture', async () => {
    const result = await createMockAncestryBridge('failure').startup()
    expect(result).toEqual({ ok: false, protocolVersion: '1', error: { code: 'DESKTOP_UNAVAILABLE', message: 'Desktop diagnostics are temporarily unavailable.', remediation: 'Restart AncestryLLM.' } })
    expect(JSON.stringify(result)).not.toMatch(/path|token|secret|trace/i)
  })
})
