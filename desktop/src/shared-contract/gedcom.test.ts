/** Exercises the bounded, path-free GEDCOM presentation boundary. */
import { describe, expect, it } from 'vitest'
import { parseGedcomInspection, parseGedcomRootPage, parseGedcomRootQuery } from './gedcom'

const candidate = {
  person_ref: `person:${'a'.repeat(32)}`, reason_code: 'individual-record',
  display_name: 'Ada Example', source_identifier: '@I1@', birth_date: '1900',
  death_date: '', relationship_summary: '0 parents; 1 partners; 2 children',
}

describe('GEDCOM presentation contract', () => {
  it('accepts an empty source and exposes no eager person collection', () => {
    const value = { schema_version: 1, operation: 'gedcom.inspect', value: {
      summary: { source: { artifact_id: `art_${'a'.repeat(64)}`, media_type: 'text/vnd.gedcom',
        artifact_type: 'gedcom', size_bytes: 90, status: 'ready', sha256: 'b'.repeat(64) },
      gedcom_version: '5.5.5', encoding: 'UTF-8', individual_count: 0, family_count: 0, other_record_count: 2 },
      findings: [], finding_count: 0, root_candidate_count: 0,
    } }
    expect(parseGedcomInspection(value)).toEqual(value)
    expect(() => parseGedcomInspection({ ...value, value: { ...value.value, root_candidates: [] } })).toThrow()
  })

  it('preserves duplicate names with distinct source-bound identities', () => {
    const page = { schema_version: 1, candidates: [candidate,
      { ...candidate, person_ref: `person:${'b'.repeat(32)}`, source_identifier: '@I2@' }],
    total_count: 2, next_cursor: null }
    expect(parseGedcomRootPage(page)).toEqual(page)
    expect(() => parseGedcomRootPage({ ...page, candidates: Array(101).fill(candidate) })).toThrow()
    expect(() => parseGedcomRootPage({ ...page, candidates: [{ ...candidate, path: '/private/source.ged' }] })).toThrow()
    expect(() => parseGedcomRootPage({ ...page, candidates: [{ ...candidate, display_name: 'a'.repeat(129) }] })).toThrow()
    expect(() => parseGedcomRootPage({ ...page, candidates: [{ ...candidate, display_name: 'Ada\u202eExample' }] })).toThrow()
  })

  it('requires bounded searches and opaque cursors without filesystem identity', () => {
    const query = { schema_version: 1, job_id: 'j000001', query: 'Ada', limit: 25, cursor: null }
    expect(parseGedcomRootQuery(query)).toEqual(query)
    for (const change of [{ limit: 0 }, { limit: 101 }, { query: 'x'.repeat(129) },
      { cursor: '/private/source.ged' }, { path: '/private/source.ged' }, { job_id: '../other' }]) {
      expect(() => parseGedcomRootQuery({ ...query, ...change })).toThrow()
    }
  })
})
