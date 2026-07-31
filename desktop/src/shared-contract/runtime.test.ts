import { describe, expect, it } from 'vitest'
import { parseBridgeResult, parseTheme } from './runtime'

describe('runtime bridge validation', () => {
  it('rejects invalid themes and unknown response fields', () => {
    expect(() => parseTheme('sepia')).toThrow('Invalid desktop theme')
    expect(() => parseBridgeResult({ ok: true, protocolVersion: '1', data: {}, surprise: true })).toThrow('Invalid bridge response')
  })
  it('rejects unbounded messages', () => {
    expect(() => parseBridgeResult({ ok: false, protocolVersion: '1', error: { code: 'FAILED', message: 'x'.repeat(241) } })).toThrow('Invalid bridge response')
  })
})
