/** Verifies exact native-action contracts at the renderer bridge boundary. */
import { describe, expect, it } from 'vitest'
import {
  parseCopyTextRequest,
  parseCopyTextResult,
  parseOpenExternalLinkRequest,
  parseOpenExternalLinkResult,
} from './runtime'

const success = <T extends object>(data: T) => Object.freeze({
  ok: true as const,
  protocolVersion: '1' as const,
  data,
})

describe('renderer native-action contracts', () => {
  it('normalizes a bounded HTTPS destination and rejects confused-deputy inputs', () => {
    expect(parseOpenExternalLinkRequest({
      schema_version: 1,
      destination: 'https://EXAMPLE.org/research?q=family#person',
    })).toEqual({
      schema_version: 1,
      destination: 'https://example.org/research?q=family#person',
    })

    for (const destination of [
      'http://example.org/',
      'javascript:alert(1)',
      'https://user:password@example.org/',
      'https://example.org:8443/',
      ' https://example.org/',
      'https:\\example.org/',
      `https://example.org/${String.fromCharCode(1)}`,
      `https://example.org/${'a'.repeat(2_048)}`,
    ]) {
      expect(() => parseOpenExternalLinkRequest({
        schema_version: 1,
        destination,
      })).toThrow('Invalid external-link request')
    }
    expect(() => parseOpenExternalLinkRequest({
      schema_version: 1,
      destination: 'https://example.org/',
      bypass_confirmation: true,
    })).toThrow('Invalid external-link request')
  })

  it('accepts display text only and rejects hidden controls, empty, oversized, or extended requests', () => {
    expect(parseCopyTextRequest({
      schema_version: 1,
      text: 'First line\nSecond\tcolumn',
    })).toEqual({
      schema_version: 1,
      text: 'First line\nSecond\tcolumn',
    })

    for (const text of ['', '\u0000secret', `x${String.fromCharCode(1)}y`, 'x'.repeat(16_385)]) {
      expect(() => parseCopyTextRequest({ schema_version: 1, text })).toThrow(
        'Invalid copy-text request',
      )
    }
    expect(() => parseCopyTextRequest({
      schema_version: 1,
      text: 'visible text',
      html: '<strong>visible text</strong>',
    })).toThrow('Invalid copy-text request')
  })

  it('parses only strict native-action outcomes', () => {
    expect(parseOpenExternalLinkResult(success({
      schema_version: 1,
      destination: 'https://example.org/research',
      status: 'cancelled',
    }))).toEqual(success({
      schema_version: 1,
      destination: 'https://example.org/research',
      status: 'cancelled',
    }))
    expect(parseCopyTextResult(success({ schema_version: 1, copied: true }))).toEqual(
      success({ schema_version: 1, copied: true }),
    )

    expect(() => parseOpenExternalLinkResult(success({
      schema_version: 1,
      destination: 'http://example.org/',
      status: 'opened',
    }))).toThrow('Invalid bridge response')
    expect(() => parseCopyTextResult(success({
      schema_version: 1,
      copied: true,
      html: '<p>copied</p>',
    }))).toThrow('Invalid bridge response')
  })
})
