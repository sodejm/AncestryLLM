/** Defines the bounded, path-free RootsMagic desktop workbench protocol. */

/** Names one fixed, parameterized RootsMagic query. */
export type RootsMagicQueryId = 'people' | 'family_links' | 'events'
/** Limits an export to the root's connected component or one directional lineage. */
export type RootsMagicScope = 'connected' | 'ancestors' | 'descendants'
/** Controls how living people appear in a generated GEDCOM. */
export type RootsMagicLivingPolicy = 'exclude' | 'include' | 'anonymize'
/** Represents one path-free scalar returned in a fixed query result. */
export type RootsMagicScalar = string | number | boolean | null

/** Describes an immutable retained source without exposing its filesystem path. */
export interface RootsMagicSourceSummary {
  schema_version: 1
  source_ref: string
  friendly_name: string
  fingerprint: string
  detected_version: 'unknown'
  grant_status_code: 'active'
  immutable: true
}

/** Describes one bounded input accepted by a fixed query. */
export interface RootsMagicQueryParameterDefinition {
  parameter_id: 'person_id' | 'name_filter' | 'offset' | 'page_size'
  value_type_code: 'integer' | 'string'
  required: boolean
  minimum: number | null
  maximum: number | null
  allowed_values: readonly RootsMagicScalar[]
}

/** Describes a fixed query and its supported parameters. */
export interface RootsMagicQueryDefinition {
  query_id: RootsMagicQueryId
  label: string
  description: string
  parameters: readonly RootsMagicQueryParameterDefinition[]
  maximum_rows: number
}

/** Envelopes the fixed RootsMagic query definitions. */
export interface RootsMagicPresetDefinitions {
  schema_version: 1
  queries: readonly RootsMagicQueryDefinition[]
}

/** Requests one page from a fixed query against an owned source. */
export interface RootsMagicQueryRequest {
  schema_version: 1
  source_ref: string
  query_id: RootsMagicQueryId
  person_id: number | null
  name_filter: string
  offset: number
  page_size: number
}

/** Returns one path-free page from a fixed RootsMagic query. */
export interface RootsMagicResultPage {
  schema_version: 1
  query_id: RootsMagicQueryId
  columns: readonly string[]
  rows: readonly { values: readonly RootsMagicScalar[] }[]
  offset: number
  returned_rows: number
  total_rows: number | null
  has_more: boolean
  next_offset: number | null
}

/** Identifies an exact new export folder through an opaque capability. */
export interface RootsMagicOutputSelection {
  schema_version: 1
  output_capability: string
  display_name: string
}

/** Identifies an owned retained source for disposal. */
export interface RootsMagicSourceReferenceRequest {
  schema_version: 1
  source_ref: string
}

/** Identifies an owned completed artifact for native reveal. */
export interface RootsMagicArtifactRequest {
  schema_version: 1
  artifact_id: string
}

/** Confirms a successful operation without returning host details. */
export interface RootsMagicAcknowledgement { schema_version: 1 }

/** Requests one portable GEDCOM export from an owned source and destination. */
export interface RootsMagicExportRequest {
  schema_version: 1
  source_ref: string
  output_capability: string
  root_person_id: number
  scope: RootsMagicScope
  generations: number | null
  living: RootsMagicLivingPolicy
}

/** Describes one completed portable GEDCOM export without exposing its path. */
export interface RootsMagicExportReceipt {
  schema_version: 1
  artifact_id: string
  display_name: string
  source_ref: string
  source_fingerprint: string
  profile_code: 'portable'
  gedcom_version: '5.5.5'
}

/** Carries the typed terminal result for a RootsMagic workbench job. */
export type RootsMagicJobResult =
  | { schema_version: 1; kind: 'inspection'; result: RootsMagicSourceSummary }
  | { schema_version: 1; kind: 'query'; result: RootsMagicResultPage }
  | { schema_version: 1; kind: 'export'; result: RootsMagicExportReceipt }

function fail(): never { throw new Error('Invalid RootsMagic contract') }
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join(',') !== [...keys].sort().join(',')) fail()
  return value as Record<string, unknown>
}
function integer(value: unknown, minimum: number, maximum: number): asserts value is number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) fail()
}
function text(value: unknown, maximum: number, allowEmpty = true): asserts value is string {
  if (typeof value !== 'string' || (!allowEmpty && value.length === 0)
    || [...value].length > maximum || /[\p{C}]/u.test(value)) fail()
}
function opaque(value: unknown): asserts value is string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail()
}
function digest(value: unknown): asserts value is string { opaque(value) }
function displayName(value: unknown): asserts value is string {
  text(value, 255, false)
  if (/[/\\]/.test(value as string) || value === '.' || value === '..') fail()
}
function scalar(value: unknown): value is RootsMagicScalar {
  return value === null || typeof value === 'string' || typeof value === 'boolean'
    || (typeof value === 'number' && Number.isSafeInteger(value))
}
function freeze<T>(value: T): Readonly<T> {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const item of Object.values(value)) freeze(item)
    Object.freeze(value)
  }
  return value
}

/** Validates immutable source metadata without admitting host paths. */
export function parseRootsMagicSourceSummary(value: unknown): Readonly<RootsMagicSourceSummary> {
  const data = object(value, ['schema_version', 'source_ref', 'friendly_name', 'fingerprint',
    'detected_version', 'grant_status_code', 'immutable'])
  if (data.schema_version !== 1 || data.detected_version !== 'unknown'
    || data.grant_status_code !== 'active' || data.immutable !== true) fail()
  opaque(data.source_ref); digest(data.fingerprint); displayName(data.friendly_name)
  return freeze(data as unknown as RootsMagicSourceSummary)
}

/** Validates a fixed, typed preset query and rejects arbitrary SQL-shaped fields. */
export function parseRootsMagicQueryRequest(value: unknown): Readonly<RootsMagicQueryRequest> {
  const data = object(value, ['schema_version', 'source_ref', 'query_id', 'person_id', 'name_filter',
    'offset', 'page_size'])
  if (data.schema_version !== 1 || !['people', 'family_links', 'events'].includes(data.query_id as string)) fail()
  opaque(data.source_ref); text(data.name_filter, 200)
  integer(data.offset, 0, 1_000_000); integer(data.page_size, 1, 100)
  if (data.person_id !== null) integer(data.person_id, 1, Number.MAX_SAFE_INTEGER)
  if (data.query_id === 'people' && data.person_id !== null) fail()
  if (data.query_id !== 'people' && data.person_id === null) fail()
  if (data.query_id !== 'people' && data.name_filter !== '') fail()
  return freeze(data as unknown as RootsMagicQueryRequest)
}

/** Validates the fixed portable GEDCOM export choices. */
export function parseRootsMagicExportRequest(value: unknown): Readonly<RootsMagicExportRequest> {
  const data = object(value, ['schema_version', 'source_ref', 'output_capability', 'root_person_id',
    'scope', 'generations', 'living'])
  if (data.schema_version !== 1 || !['connected', 'ancestors', 'descendants'].includes(data.scope as string)
    || !['exclude', 'include', 'anonymize'].includes(data.living as string)) fail()
  opaque(data.source_ref); opaque(data.output_capability)
  integer(data.root_person_id, 1, Number.MAX_SAFE_INTEGER)
  if (data.generations !== null) integer(data.generations, 1, 100)
  if (data.scope === 'connected' && data.generations !== null) fail()
  return freeze(data as unknown as RootsMagicExportRequest)
}

/** Validates fixed query definitions fetched from the local adapter. */
export function parseRootsMagicPresetDefinitions(value: unknown): Readonly<RootsMagicPresetDefinitions> {
  const data = object(value, ['schema_version', 'queries'])
  if (data.schema_version !== 1 || !Array.isArray(data.queries) || data.queries.length !== 3) fail()
  const ids = new Set<string>()
  for (const queryValue of data.queries) {
    const query = object(queryValue, ['query_id', 'label', 'description', 'parameters', 'maximum_rows'])
    if (!['people', 'family_links', 'events'].includes(query.query_id as string)
      || ids.has(query.query_id as string) || !Array.isArray(query.parameters)) fail()
    ids.add(query.query_id as string); text(query.label, 64, false); text(query.description, 256, false)
    integer(query.maximum_rows, 1, 100)
    for (const parameterValue of query.parameters) {
      const parameter = object(parameterValue, ['parameter_id', 'value_type_code', 'required', 'minimum',
        'maximum', 'allowed_values'])
      if (!['person_id', 'name_filter', 'offset', 'page_size'].includes(parameter.parameter_id as string)
        || !['integer', 'string'].includes(parameter.value_type_code as string)
        || typeof parameter.required !== 'boolean' || !Array.isArray(parameter.allowed_values)
        || !parameter.allowed_values.every(scalar)) fail()
      if (parameter.minimum !== null) integer(parameter.minimum, 0, Number.MAX_SAFE_INTEGER)
      if (parameter.maximum !== null) integer(parameter.maximum, 1, Number.MAX_SAFE_INTEGER)
    }
  }
  return freeze(data as unknown as RootsMagicPresetDefinitions)
}

function parsePage(value: unknown): RootsMagicResultPage {
  const data = object(value, ['schema_version', 'query_id', 'columns', 'rows', 'offset', 'returned_rows', 'total_rows',
    'has_more', 'next_offset'])
  if (data.schema_version !== 1 || !['people', 'family_links', 'events'].includes(data.query_id as string)
    || !Array.isArray(data.columns) || data.columns.length > 64
    || !Array.isArray(data.rows) || data.rows.length > 100 || typeof data.has_more !== 'boolean') fail()
  for (const column of data.columns) text(column, 64, false)
  if (new Set(data.columns).size !== data.columns.length) fail()
  for (const rowValue of data.rows) {
    const row = object(rowValue, ['values'])
    if (!Array.isArray(row.values) || row.values.length !== data.columns.length || !row.values.every(scalar)) fail()
    for (const item of row.values) if (typeof item === 'string') text(item, 2048)
  }
  integer(data.offset, 0, 1_000_000); integer(data.returned_rows, 0, 100)
  if (data.returned_rows !== data.rows.length) fail()
  if (data.total_rows !== null) integer(data.total_rows, 0, 1_000_000)
  if (data.next_offset !== null) integer(data.next_offset, 1, 1_000_000)
  if (data.has_more !== (data.next_offset !== null)) fail()
  return data as unknown as RootsMagicResultPage
}

/** Validates inspection, query, or export completion data from an owned job. */
export function parseRootsMagicJobResult(value: unknown): Readonly<RootsMagicJobResult> {
  const envelope = object(value, ['schema_version', 'kind', 'result'])
  if (envelope.schema_version !== 1) fail()
  if (envelope.kind === 'inspection') parseRootsMagicSourceSummary(envelope.result)
  else if (envelope.kind === 'query') parsePage(envelope.result)
  else if (envelope.kind === 'export') {
    const result = object(envelope.result, ['schema_version', 'artifact_id', 'display_name', 'source_ref',
      'source_fingerprint', 'profile_code', 'gedcom_version'])
    if (result.schema_version !== 1 || result.profile_code !== 'portable' || result.gedcom_version !== '5.5.5'
      || typeof result.artifact_id !== 'string' || !/^art_[A-Za-z0-9._:-]{16,128}$/.test(result.artifact_id)) fail()
    displayName(result.display_name); opaque(result.source_ref); digest(result.source_fingerprint)
  } else fail()
  return freeze(envelope as unknown as RootsMagicJobResult)
}

/** Validates the renderer-safe result of a native output-folder selection. */
export function parseRootsMagicOutputSelection(value: unknown): Readonly<RootsMagicOutputSelection> {
  const data = object(value, ['schema_version', 'output_capability', 'display_name'])
  if (data.schema_version !== 1) fail()
  opaque(data.output_capability); displayName(data.display_name)
  return freeze(data as unknown as RootsMagicOutputSelection)
}

/** Validates a renderer-supplied source reference without admitting extra fields. */
export function parseRootsMagicSourceReferenceRequest(
  value: unknown,
): Readonly<RootsMagicSourceReferenceRequest> {
  const data = object(value, ['schema_version', 'source_ref'])
  if (data.schema_version !== 1) fail()
  opaque(data.source_ref)
  return freeze(data as unknown as RootsMagicSourceReferenceRequest)
}

/** Validates a renderer-supplied opaque artifact reference. */
export function parseRootsMagicArtifactRequest(value: unknown): Readonly<RootsMagicArtifactRequest> {
  const data = object(value, ['schema_version', 'artifact_id'])
  if (data.schema_version !== 1 || typeof data.artifact_id !== 'string'
    || !/^art_[A-Za-z0-9._:-]{16,128}$/.test(data.artifact_id)) fail()
  return freeze(data as unknown as RootsMagicArtifactRequest)
}

/** Validates a bounded suggested name before a native destination dialog opens. */
export function parseRootsMagicOutputDisplayName(value: unknown): string {
  displayName(value)
  return value as string
}

/** Validates a schema-only success acknowledgement. */
export function parseRootsMagicAcknowledgement(value: unknown): Readonly<RootsMagicAcknowledgement> {
  const data = object(value, ['schema_version'])
  if (data.schema_version !== 1) fail()
  return freeze(data as unknown as RootsMagicAcknowledgement)
}
