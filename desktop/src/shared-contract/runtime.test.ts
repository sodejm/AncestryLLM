import { describe, expect, it } from 'vitest'
import {
  parseAppInfoResult,
  parseCapabilitiesResult,
  parsePreferenceUpdate,
  parsePreferencesResult,
  parseStartupDiagnosticsResult,
} from './runtime'

const capabilityManifest = {
  api: {
    namespace: '/api/v1',
    contract: 'ancestryllm.internal-api/1',
    application_contract: 'ancestryllm.application/0.3',
  },
  modules: [{
    module_id: 'tree',
    name: 'Tree',
    summary: 'Fictional local tree tools.',
    actions: [{ dispatch_key: 'tree.summary', name: 'summary', summary: 'Summarize a fictional tree.' }],
  }],
  request_policy: {
    max_body_bytes: 1_048_576,
    max_json_depth: 16,
    max_collection_items: 1_000,
    max_string_characters: 65_536,
  },
  pagination: { default_limit: 25, maximum_limit: 100, maximum_cursor_characters: 256 },
} as const

describe('runtime bridge validation', () => {
  it('accepts exact versioned results for each renderer-safe response', () => {
    expect(parseAppInfoResult({ ok: true, protocolVersion: '1', data: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' } }).ok).toBe(true)
    expect(parseStartupDiagnosticsResult({ ok: true, protocolVersion: '1', data: { state: 'degraded', failure: 'startup_failed', automaticRestartsRemaining: 0, manualRetriesRemaining: 1 } }).ok).toBe(true)
    expect(parseCapabilitiesResult({ ok: true, protocolVersion: '1', data: capabilityManifest }).ok).toBe(true)
    expect(parsePreferencesResult({ ok: true, protocolVersion: '1', data: { colorScheme: 'system', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 0 } }).ok).toBe(true)
  })

  it('rejects invalid requests, unknown fields, and unbounded errors', () => {
    expect(parsePreferenceUpdate({ expectedRevision: 0, colorScheme: 'dark' })).toEqual({ expectedRevision: 0, colorScheme: 'dark' })
    expect(() => parsePreferenceUpdate({ expectedRevision: 0, colorScheme: 'sepia' })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({ expectedRevision: 0, reducedMotion: true, surprise: true })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({ colorScheme: 'dark' })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({ expectedRevision: -1, colorScheme: 'dark' })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({ expectedRevision: 0 })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({})).toThrow('Invalid preference update')
    expect(() => parseAppInfoResult({ ok: true, protocolVersion: '1', data: {}, surprise: true })).toThrow('Invalid bridge response')
    expect(() => parseAppInfoResult({ ok: false, protocolVersion: '1', error: { code: 'INTERNAL_ERROR', message: 'x'.repeat(241), remediation: 'Restart AncestryLLM.' } })).toThrow('Invalid bridge response')
  })

  it('rejects OpenAPI capability drift and unsafe extra fields', () => {
    expect(() => parseCapabilitiesResult({ ok: true, protocolVersion: '1', data: { ...capabilityManifest, token: 'secret' } })).toThrow('Invalid bridge response')
    expect(() => parseCapabilitiesResult({ ok: true, protocolVersion: '1', data: { ...capabilityManifest, api: { ...capabilityManifest.api, contract: 'ancestryllm.internal-api/2' } } })).toThrow('Invalid bridge response')
  })
})
