import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  parseAppInfoResult,
  parseArtifactRef,
  parseCapabilitiesResult,
  parseFileGrantId,
  parseFileGrantResult,
  parseFileGrantRevocationResult,
  parseOpenFileGrantRequest,
  parsePreferenceUpdate,
  parsePreferencesResult,
  parseSaveFileGrantRequest,
  parseStartupDiagnosticsResult,
} from './runtime'

type ContractProperty = {
  const?: string
  pattern?: string
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  minItems?: number
  maxItems?: number
}

type MutableAction = { dispatch_key: string; name: string; summary: string }
type MutableModule = { module_id: string; name: string; summary: string; actions: MutableAction[] }
type MutableManifest = {
  api: { namespace: string; contract: string; application_contract: string }
  modules: MutableModule[]
  request_policy: {
    max_body_bytes: number
    max_json_depth: number
    max_collection_items: number
    max_string_characters: number
  }
  pagination: { default_limit: number; maximum_limit: number; maximum_cursor_characters: number }
}

const openApi = JSON.parse(readFileSync(
  resolve(process.cwd(), '../docs/api/openapi-v1.json'),
  'utf8',
)) as { components: { schemas: Record<string, { properties: Record<string, ContractProperty> }> } }

const contractProperty = (schema: string, property: string): ContractProperty => {
  const definition = openApi.components.schemas[schema]?.properties[property]
  if (!definition) throw new Error(`Missing OpenAPI property ${schema}.${property}`)
  return definition
}

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

const mutableManifest = (): MutableManifest =>
  structuredClone(capabilityManifest) as unknown as MutableManifest

const firstModule = (manifest: MutableManifest): MutableModule => {
  const module = manifest.modules[0]
  if (!module) throw new Error('Capability fixture must contain a module')
  return module
}

const firstAction = (manifest: MutableManifest): MutableAction => {
  const action = firstModule(manifest).actions[0]
  if (!action) throw new Error('Capability fixture must contain an action')
  return action
}

const capabilityResult = (data: MutableManifest) => ({ ok: true, protocolVersion: '1', data })
const grantId = `grt_${'a'.repeat(64)}`
const fileGrantResult = {
  ok: true,
  protocolVersion: '1',
  data: {
    grantId,
    purpose: 'gedcom-read',
    access: 'read',
    scope: {
      originatingWindow: 'requesting-window',
      lifetime: 'app-session',
      redemption: 'single-use',
    },
    metadata: {
      displayName: 'fictional.ged',
      format: 'gedcom',
      sizeBytes: 26,
      validation: 'validated-input',
    },
  },
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

  it('accepts exact path-free file-grant requests, results, revocations, and artifact references', () => {
    expect(parseOpenFileGrantRequest({ purpose: 'gedcom-read' })).toEqual({ purpose: 'gedcom-read' })
    expect(parseSaveFileGrantRequest({ purpose: 'markdown-write', suggestedName: 'family-summary.md' })).toEqual({
      purpose: 'markdown-write',
      suggestedName: 'family-summary.md',
    })
    expect(parseFileGrantId(grantId)).toBe(grantId)
    expect(parseFileGrantResult(fileGrantResult)).toEqual(fileGrantResult)
    expect(parseFileGrantResult({ ok: true, protocolVersion: '1', data: null })).toEqual({
      ok: true,
      protocolVersion: '1',
      data: null,
    })
    expect(parseFileGrantRevocationResult({
      ok: true,
      protocolVersion: '1',
      data: { revoked: true },
    }).ok).toBe(true)
    expect(parseArtifactRef({
      artifact_id: `art_${'b'.repeat(32)}`,
      artifact_type: 'gedcom_export',
      media_type: 'text/vnd.gedcom',
      sha256: 'c'.repeat(64),
      size_bytes: 42,
      status: 'staged',
    })).toMatchObject({ artifact_type: 'gedcom_export', status: 'staged' })
  })

  it('rejects renderer paths, malformed IDs, unknown fields, and incoherent grant metadata', () => {
    expect(() => parseOpenFileGrantRequest({ purpose: 'gedcom-read', path: '/private/tree.ged' })).toThrow('Invalid open-file grant request')
    expect(() => parseSaveFileGrantRequest({ purpose: 'gedcom-write', suggestedName: '../tree.ged' })).toThrow('Invalid save-file grant request')
    expect(() => parseFileGrantId('grt_predictable')).toThrow('Invalid file-grant ID')
    expect(() => parseFileGrantResult({
      ...fileGrantResult,
      data: { ...fileGrantResult.data, path: '/private/tree.ged' },
    })).toThrow('Invalid bridge response')
    expect(() => parseFileGrantResult({
      ...fileGrantResult,
      data: { ...fileGrantResult.data, access: 'write' },
    })).toThrow('Invalid bridge response')
    expect(() => parseFileGrantResult({
      ...fileGrantResult,
      data: { ...fileGrantResult.data, scope: { ...fileGrantResult.data.scope, lifetime: 'forever' } },
    })).toThrow('Invalid bridge response')
    expect(() => parseFileGrantResult({
      ...fileGrantResult,
      data: {
        ...fileGrantResult.data,
        metadata: { ...fileGrantResult.data.metadata, validation: 'replacement-confirmed' },
      },
    })).toThrow('Invalid bridge response')
    expect(() => parseArtifactRef({
      artifact_id: `art_${'b'.repeat(32)}`,
      artifact_type: 'gedcom_export',
      media_type: 'text/vnd.gedcom',
      sha256: 'c'.repeat(64),
      size_bytes: 42,
      status: 'staged',
      path: '/private/tree.ged',
    })).toThrow('Invalid bridge response')
  })

  it('rejects OpenAPI capability drift and unsafe extra fields', () => {
    expect(() => parseCapabilitiesResult({ ok: true, protocolVersion: '1', data: { ...capabilityManifest, token: 'secret' } })).toThrow('Invalid bridge response')
    expect(() => parseCapabilitiesResult({ ok: true, protocolVersion: '1', data: { ...capabilityManifest, api: { ...capabilityManifest.api, contract: 'ancestryllm.internal-api/2' } } })).toThrow('Invalid bridge response')
  })

  it('derives runtime capability boundaries from the checked-in OpenAPI contract', () => {
    const strings = [
      { schema: 'CapabilityModule', property: 'module_id', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstModule(manifest).module_id = value } },
      { schema: 'CapabilityModule', property: 'name', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstModule(manifest).name = value } },
      { schema: 'CapabilityModule', property: 'summary', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstModule(manifest).summary = value } },
      { schema: 'CapabilityAction', property: 'dispatch_key', value: (length: number) => `a.${'x'.repeat(length - 2)}`, set: (manifest: MutableManifest, value: string) => { firstAction(manifest).dispatch_key = value } },
      { schema: 'CapabilityAction', property: 'name', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstAction(manifest).name = value } },
      { schema: 'CapabilityAction', property: 'summary', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstAction(manifest).summary = value } },
    ]
    for (const item of strings) {
      const { minLength, maxLength } = contractProperty(item.schema, item.property)
      expect(minLength).toBeTypeOf('number')
      expect(maxLength).toBeTypeOf('number')
      for (const length of [minLength as number, maxLength as number]) {
        const manifest = mutableManifest()
        item.set(manifest, item.value(length))
        expect(() => parseCapabilitiesResult(capabilityResult(manifest))).not.toThrow()
      }
      for (const length of [(minLength as number) - 1, (maxLength as number) + 1]) {
        const manifest = mutableManifest()
        item.set(manifest, item.value(length))
        expect(() => parseCapabilitiesResult(capabilityResult(manifest))).toThrow('Invalid bridge response')
      }
    }

    const patterns = [
      {
        schema: 'CapabilityModule',
        property: 'module_id',
        candidates: ['tree', 'tree.summary', 'tree:summary-1', 'tree_summary', 'tree/summary', 'tree summary', 'árbol'],
        set: (manifest: MutableManifest, value: string) => { firstModule(manifest).module_id = value },
      },
      {
        schema: 'CapabilityAction',
        property: 'name',
        candidates: ['summary', 'tree.summary', 'tree:summary-1', 'tree_summary', 'tree/summary', 'tree summary', 'árbol'],
        set: (manifest: MutableManifest, value: string) => { firstAction(manifest).name = value },
      },
      {
        schema: 'CapabilityAction',
        property: 'dispatch_key',
        candidates: ['tree.summary', 'tree-1.summary_2', 'tree', 'tree.summary.detail', 'tree:summary', 'tree/summary', 'tree summary'],
        set: (manifest: MutableManifest, value: string) => { firstAction(manifest).dispatch_key = value },
      },
    ]
    for (const item of patterns) {
      const { pattern } = contractProperty(item.schema, item.property)
      expect(pattern).toBeTypeOf('string')
      const contractPattern = new RegExp(pattern as string)
      expect(item.candidates.some((candidate) => contractPattern.test(candidate))).toBe(true)
      expect(item.candidates.some((candidate) => !contractPattern.test(candidate))).toBe(true)
      for (const candidate of item.candidates) {
        const manifest = mutableManifest()
        item.set(manifest, candidate)
        const assertion = expect(() => parseCapabilitiesResult(capabilityResult(manifest)))
        if (contractPattern.test(candidate)) assertion.not.toThrow()
        else assertion.toThrow('Invalid bridge response')
      }
    }

    const numbers = [
      { schema: 'RequestSizePolicy', property: 'max_body_bytes', set: (manifest: MutableManifest, value: number) => { manifest.request_policy.max_body_bytes = value } },
      { schema: 'RequestSizePolicy', property: 'max_json_depth', set: (manifest: MutableManifest, value: number) => { manifest.request_policy.max_json_depth = value } },
      { schema: 'RequestSizePolicy', property: 'max_collection_items', set: (manifest: MutableManifest, value: number) => { manifest.request_policy.max_collection_items = value } },
      { schema: 'RequestSizePolicy', property: 'max_string_characters', set: (manifest: MutableManifest, value: number) => { manifest.request_policy.max_string_characters = value } },
      { schema: 'PaginationPolicy', property: 'default_limit', set: (manifest: MutableManifest, value: number) => { manifest.pagination.default_limit = value } },
      { schema: 'PaginationPolicy', property: 'maximum_limit', set: (manifest: MutableManifest, value: number) => { manifest.pagination.maximum_limit = value; manifest.pagination.default_limit = Math.min(value, manifest.pagination.default_limit) } },
      { schema: 'PaginationPolicy', property: 'maximum_cursor_characters', set: (manifest: MutableManifest, value: number) => { manifest.pagination.maximum_cursor_characters = value } },
    ]
    for (const item of numbers) {
      const { minimum, maximum } = contractProperty(item.schema, item.property)
      expect(minimum).toBeTypeOf('number')
      expect(maximum).toBeTypeOf('number')
      for (const value of [minimum as number, maximum as number]) {
        const manifest = mutableManifest()
        item.set(manifest, value)
        expect(() => parseCapabilitiesResult(capabilityResult(manifest))).not.toThrow()
      }
      for (const value of [(minimum as number) - 1, (maximum as number) + 1]) {
        const manifest = mutableManifest()
        item.set(manifest, value)
        expect(() => parseCapabilitiesResult(capabilityResult(manifest))).toThrow('Invalid bridge response')
      }
    }

    const modulesMaximum = contractProperty('CapabilityManifest', 'modules').maxItems as number
    const moduleTemplate = firstModule(mutableManifest())
    for (const [count, accepted] of [[modulesMaximum, true], [modulesMaximum + 1, false]] as const) {
      const manifest = mutableManifest()
      manifest.modules = Array.from({ length: count }, () => structuredClone(moduleTemplate))
      const assertion = expect(() => parseCapabilitiesResult(capabilityResult(manifest)))
      if (accepted) assertion.not.toThrow()
      else assertion.toThrow('Invalid bridge response')
    }

    const actions = contractProperty('CapabilityModule', 'actions')
    const actionTemplate = firstAction(mutableManifest())
    for (const [count, accepted] of [[actions.minItems as number, true], [actions.maxItems as number, true], [(actions.minItems as number) - 1, false], [(actions.maxItems as number) + 1, false]] as const) {
      const manifest = mutableManifest()
      firstModule(manifest).actions = Array.from({ length: count }, () => structuredClone(actionTemplate))
      const assertion = expect(() => parseCapabilitiesResult(capabilityResult(manifest)))
      if (accepted) assertion.not.toThrow()
      else assertion.toThrow('Invalid bridge response')
    }

    for (const property of ['namespace', 'contract', 'application_contract'] as const) {
      const expected = contractProperty('ApiVersion', property).const as string
      const accepted = mutableManifest()
      accepted.api[property] = expected
      expect(() => parseCapabilitiesResult(capabilityResult(accepted))).not.toThrow()
      const rejected = mutableManifest()
      rejected.api[property] = `${expected}.drift`
      expect(() => parseCapabilitiesResult(capabilityResult(rejected))).toThrow('Invalid bridge response')
    }
  })
})
