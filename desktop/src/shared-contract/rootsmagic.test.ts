/** Exercises the path-free, bounded RootsMagic desktop protocol. */
import { describe, expect, it } from 'vitest'
import {
  parseRootsMagicExportRequest,
  parseRootsMagicJobResult,
  parseRootsMagicQueryRequest,
  parseRootsMagicSourceSummary,
} from './rootsmagic'

const sourceRef = 'a'.repeat(64)

describe('RootsMagic desktop contract', () => {
  it('accepts immutable source summaries without filesystem identity', () => {
    const value = { schema_version: 1, source_ref: sourceRef, friendly_name: 'family.rmtree',
      fingerprint: 'b'.repeat(64), detected_version: 'unknown', grant_status_code: 'active', immutable: true }
    expect(parseRootsMagicSourceSummary(value)).toEqual(value)
    expect(() => parseRootsMagicSourceSummary({ ...value, path: '/private/family.rmtree' })).toThrow()
  })

  it('limits queries to named presets and bound scalar parameters', () => {
    const value = { schema_version: 1, source_ref: sourceRef, query_id: 'family_links',
      person_id: 42, name_filter: '', offset: 0, page_size: 25 }
    expect(parseRootsMagicQueryRequest(value)).toEqual(value)
    expect(() => parseRootsMagicQueryRequest({ ...value, query_id: 'SELECT * FROM PersonTable' })).toThrow()
    expect(() => parseRootsMagicQueryRequest({ ...value, fields: ['Name'] })).toThrow()
    expect(() => parseRootsMagicQueryRequest({ ...value, person_id: null })).toThrow()
    expect(parseRootsMagicQueryRequest({ ...value, query_id: 'people', person_id: null,
      name_filter: 'Ada' })).toMatchObject({ query_id: 'people', name_filter: 'Ada' })
  })

  it('keeps export policy explicit and bounded', () => {
    const value = { schema_version: 1, source_ref: sourceRef, output_capability: 'c'.repeat(64),
      root_person_id: 42, scope: 'connected', generations: null, living: 'exclude' }
    expect(parseRootsMagicExportRequest(value)).toEqual(value)
    expect(() => parseRootsMagicExportRequest({ ...value, root_person_id: 0 })).toThrow()
    expect(() => parseRootsMagicExportRequest({ ...value, generations: 101 })).toThrow()
    expect(() => parseRootsMagicExportRequest({ ...value, profile_code: 'custom' })).toThrow()
  })

  it('validates paged query results and the fixed portable export receipt', () => {
    const page = { schema_version: 1, kind: 'query', result: { schema_version: 1, query_id: 'people',
      columns: ['person_id', 'display_name'], rows: [{ values: [42, 'Ada Example'] }],
      offset: 0, returned_rows: 1, total_rows: 1, has_more: false, next_offset: null } }
    expect(parseRootsMagicJobResult(page)).toEqual(page)
    expect(() => parseRootsMagicJobResult({ ...page, result: { ...page.result, schema_version: 2 } })).toThrow()
    expect(() => parseRootsMagicJobResult({ ...page, result: { ...page.result, rows: [
      { values: [Number.MAX_SAFE_INTEGER + 1, 'Unsafe identifier'] },
    ] } })).toThrow()
    expect(() => parseRootsMagicJobResult({ ...page, result: { ...page.result, rows: [
      { values: [42, 'Ada Example'], path: '/private/source.rmtree' },
    ] } })).toThrow()
    const receipt = { schema_version: 1, kind: 'export', result: { schema_version: 1,
      artifact_id: 'art_1234567890abcdef1234567890abcdef', display_name: 'family.ged',
      source_ref: sourceRef, source_fingerprint: 'd'.repeat(64), profile_code: 'portable',
      gedcom_version: '5.5.5' } }
    expect(parseRootsMagicJobResult(receipt)).toEqual(receipt)
    expect(() => parseRootsMagicJobResult({ ...receipt, result: { ...receipt.result,
      gedcom_version: '7.0' } })).toThrow()
  })
})
