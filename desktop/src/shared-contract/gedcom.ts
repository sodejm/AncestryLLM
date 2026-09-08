/** Defines bounded, private GEDCOM intake presentation without host paths or raw records. */
import type { ArtifactRef, JobRequest } from './desktop'

/** Sanitized inspection metadata; person records are fetched only through bounded pages. */
export interface GedcomInspection {
  schema_version: 1
  operation: 'gedcom.inspect'
  value: {
    summary: { source: ArtifactRef; gedcom_version: string; encoding: string;
      individual_count: number; family_count: number; other_record_count: number }
    findings: readonly { code: string; severity: 'info' | 'warning' | 'error'; subject_ref: string | null }[]
    finding_count: number
    root_candidate_count: number
  }
}

/** Minimal person labels for explicit root selection, scoped to one source fingerprint. */
export interface GedcomRootCandidate {
  person_ref: string
  reason_code: 'individual-record'
  display_name: string
  source_identifier: string
  birth_date: string
  death_date: string
  relationship_summary: string
}

/** One bounded root-search result, retained only while its intake workspace is open. */
export interface GedcomRootPage {
  schema_version: 1
  candidates: readonly GedcomRootCandidate[]
  total_count: number
  next_cursor: string | null
}

/** Bounded search over one owned inspection, never an arbitrary file or URL. */
export interface GedcomRootQuery extends JobRequest {
  query: string
  limit: number
  cursor: string | null
}

/** Acknowledges that one inspection is no longer accessible to its renderer. */
export interface GedcomDiscard { schema_version: 1 }

function fail(): never { throw new Error('Invalid GEDCOM contract') }
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join(',') !== [...keys].sort().join(',')) fail()
  return value as Record<string, unknown>
}
function text(value: unknown, limit: number): asserts value is string {
  if (typeof value !== 'string' || [...value].length > limit || /\p{C}/u.test(value)) fail()
}
function count(value: unknown, limit = 5_000_000): asserts value is number {
  if (!Number.isSafeInteger(value) || (value as number) < 0 || (value as number) > limit) fail()
}
function cursor(value: unknown): void {
  if (value !== null && (typeof value !== 'string' || !/^c1_[a-f0-9]{8}_[a-f0-9]{64}$/.test(value))) fail()
}
function freeze<T>(value: T): Readonly<T> {
  if (value && typeof value === 'object') {
    for (const item of Object.values(value)) freeze(item)
    Object.freeze(value)
  }
  return value
}

/** Rejects oversize searches, unknown fields and non-opaque inspection identities. */
export function parseGedcomRootQuery(value: unknown): Readonly<GedcomRootQuery> {
  const data = object(value, ['schema_version', 'job_id', 'query', 'limit', 'cursor'])
  if (data.schema_version !== 1 || typeof data.job_id !== 'string' || !/^j[0-9]{6,12}$/.test(data.job_id)) fail()
  text(data.query, 128)
  count(data.limit, 100)
  if (data.limit < 1) fail()
  cursor(data.cursor)
  return freeze(data as unknown as GedcomRootQuery)
}

/** Validates a page without admitting raw records, filesystem paths or unbounded labels. */
export function parseGedcomRootPage(value: unknown): Readonly<GedcomRootPage> {
  const data = object(value, ['schema_version', 'candidates', 'total_count', 'next_cursor'])
  if (data.schema_version !== 1 || !Array.isArray(data.candidates) || data.candidates.length > 100) fail()
  count(data.total_count)
  if (data.total_count < data.candidates.length) fail()
  cursor(data.next_cursor)
  for (const item of data.candidates) {
    const person = object(item, ['person_ref', 'reason_code', 'display_name', 'source_identifier',
      'birth_date', 'death_date', 'relationship_summary'])
    if (typeof person.person_ref !== 'string' || !/^person:[a-f0-9]{32}$/.test(person.person_ref)
      || person.reason_code !== 'individual-record') fail()
    text(person.display_name, 128); text(person.source_identifier, 96)
    text(person.birth_date, 64); text(person.death_date, 64); text(person.relationship_summary, 128)
  }
  if (new Set(data.candidates.map((item: GedcomRootCandidate) => item.person_ref)).size !== data.candidates.length) fail()
  return freeze(data as unknown as GedcomRootPage)
}

/** Validates bounded inspection metadata without accepting an eager root collection. */
export function parseGedcomInspection(value: unknown): Readonly<GedcomInspection> {
  const envelope = object(value, ['schema_version', 'operation', 'value'])
  if (envelope.schema_version !== 1 || envelope.operation !== 'gedcom.inspect') fail()
  const data = object(envelope.value, ['summary', 'findings', 'finding_count', 'root_candidate_count'])
  const summary = object(data.summary, ['source', 'gedcom_version', 'encoding', 'individual_count',
    'family_count', 'other_record_count'])
  const source = object(summary.source, ['artifact_id', 'artifact_type', 'media_type', 'sha256', 'size_bytes', 'status'])
  if (typeof source.artifact_id !== 'string' || !/^art_[a-f0-9]{64}$/.test(source.artifact_id)
    || source.artifact_type !== 'gedcom' || source.media_type !== 'text/vnd.gedcom'
    || source.status !== 'ready' || typeof source.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(source.sha256)) fail()
  count(source.size_bytes, 512 * 1024 * 1024)
  text(summary.gedcom_version, 32); text(summary.encoding, 32)
  count(summary.individual_count); count(summary.family_count); count(summary.other_record_count)
  count(data.finding_count); count(data.root_candidate_count)
  if (!Array.isArray(data.findings) || data.findings.length > 100 || data.findings.length > data.finding_count) fail()
  for (const item of data.findings) {
    const finding = object(item, ['code', 'severity', 'subject_ref'])
    if (typeof finding.code !== 'string' || !/^[a-z][a-z0-9-]{0,95}$/.test(finding.code)
      || !['info', 'warning', 'error'].includes(finding.severity as string)
      || (finding.subject_ref !== null && (typeof finding.subject_ref !== 'string'
        || !/^person:[a-f0-9]{32}$/.test(finding.subject_ref)))) fail()
  }
  return freeze(envelope as unknown as GedcomInspection)
}

/** Validates the path-free discard acknowledgement. */
export function parseGedcomDiscard(value: unknown): Readonly<GedcomDiscard> {
  const data = object(value, ['schema_version'])
  if (data.schema_version !== 1) fail()
  return freeze(data as unknown as GedcomDiscard)
}
