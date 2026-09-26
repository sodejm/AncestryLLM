/** Presents bounded RootsMagic presets without exposing source paths or database access. */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { AncestryBridge, FileGrant, JobSnapshot } from '../../shared-contract/desktop'
import type {
  RootsMagicExportRequest,
  RootsMagicExportReceipt,
  RootsMagicJobResult,
  RootsMagicLivingPolicy,
  RootsMagicOutputSelection,
  RootsMagicPresetDefinitions,
  RootsMagicQueryId,
  RootsMagicQueryRequest,
  RootsMagicResultPage,
  RootsMagicScope,
  RootsMagicSourceSummary,
} from '../../shared-contract/rootsmagic'
import { Button } from './components/Button'
import { FileGrantCard } from './components/FileGrantCard'
import { CodedErrorView } from './design-system/CodedErrorView'

const PAGE_SIZE = 25
const MAX_GENERATIONS = 100

type Source = Readonly<{ grant: FileGrant; summary: RootsMagicSourceSummary }>
type SelectedPerson = Readonly<{ personId: number; label: string }>
type QueryState = Readonly<{ id: RootsMagicQueryId; page: RootsMagicResultPage; previousOffsets: readonly number[] }>

const bridgeFromWindow = (): AncestryBridge => (window as unknown as { ancestry: AncestryBridge }).ancestry

function displayValue(value: string | number | boolean | null): string {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

function rowPerson(page: RootsMagicResultPage, values: readonly (string | number | boolean | null)[]): SelectedPerson | null {
  const idIndex = page.columns.indexOf('person_id')
  if (idIndex < 0 || !Number.isSafeInteger(values[idIndex]) || (values[idIndex] as number) < 1) return null
  const nameIndex = page.columns.indexOf('display_name')
  const name = nameIndex < 0 ? null : values[nameIndex]
  return { personId: values[idIndex] as number, label: typeof name === 'string' && name ? name : 'Unnamed individual' }
}

function hasUnsupportedPersonId(page: RootsMagicResultPage, values: readonly (string | number | boolean | null)[]): boolean {
  const idIndex = page.columns.indexOf('person_id')
  if (idIndex < 0) return false
  const identifier = values[idIndex]
  return typeof identifier === 'string'
    || (typeof identifier === 'number' && (!Number.isSafeInteger(identifier) || identifier < 1))
}

function scopeLabel(scope: RootsMagicScope): string {
  return scope === 'connected' ? 'connected people' : scope
}

/** Owns a single ephemeral RootsMagic source and pages only fixed native presets. */
export function RootsMagicWorkspace({ bridge = bridgeFromWindow() }: { bridge?: AncestryBridge }) {
  const rootsMagic = bridge
  const [source, setSource] = useState<Source | null>(null)
  const [definitions, setDefinitions] = useState<RootsMagicPresetDefinitions | null>(null)
  const [query, setQuery] = useState<QueryState | null>(null)
  const [selectedPerson, setSelectedPerson] = useState<SelectedPerson | null>(null)
  const [nameFilter, setNameFilter] = useState('')
  const [picking, setPicking] = useState(false)
  const [discarding, setDiscarding] = useState(false)
  const [queryPending, setQueryPending] = useState(false)
  const [outputPending, setOutputPending] = useState(false)
  const [exportPending, setExportPending] = useState(false)
  const [output, setOutput] = useState<RootsMagicOutputSelection | null>(null)
  const [scope, setScope] = useState<RootsMagicScope>('connected')
  const [generations, setGenerations] = useState('4')
  const [living, setLiving] = useState<RootsMagicLivingPolicy>('exclude')
  const [confirmed, setConfirmed] = useState(false)
  const [receipt, setReceipt] = useState<RootsMagicExportReceipt | null>(null)
  const [status, setStatus] = useState('Choose a RootsMagic source to begin.')
  const [failure, setFailure] = useState<string | null>(null)
  const mounted = useRef(true)
  const sourceRef = useRef<string | null>(null)
  const inspectionJob = useRef<string | null>(null)
  const abandonedInspections = useRef(new Set<string>())
  const sourceGeneration = useRef(0)
  const queryGeneration = useRef(0)
  const errorFocus = useRef<HTMLDivElement>(null)

  const abandonInspection = useCallback(async (jobId: string) => {
    if (abandonedInspections.current.has(jobId)) return
    abandonedInspections.current.add(jobId)
    const request = { schema_version: 1 as const, job_id: jobId }
    try {
      const cancelled = await rootsMagic.cancelJob(request)
      const initial = cancelled.ok ? cancelled : await rootsMagic.getJob(request)
      if (!initial.ok) return
      let snapshot = initial.data
      while (snapshot.state === 'queued' || snapshot.state === 'running') {
        await new Promise<void>((resolve) => window.setTimeout(resolve, 1000))
        const polled = await rootsMagic.getJob(request)
        if (!polled.ok) return
        snapshot = polled.data
      }
      if (snapshot.state !== 'completed') return
      const result = await rootsMagic.getRootsMagicJobResult(request)
      if (result.ok && result.data.kind === 'inspection') {
        await rootsMagic.discardRootsMagicSource({ schema_version: 1, source_ref: result.data.result.source_ref })
      }
    } catch { /* The sidecar may already have closed and revoked the source. */ }
  }, [rootsMagic])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      sourceGeneration.current += 1
      queryGeneration.current += 1
      const activeSource = sourceRef.current
      if (activeSource) void rootsMagic.discardRootsMagicSource({ schema_version: 1, source_ref: activeSource }).catch(() => undefined)
      const pendingInspection = inspectionJob.current
      if (pendingInspection) void abandonInspection(pendingInspection)
    }
  }, [rootsMagic, abandonInspection])

  useEffect(() => { if (failure) errorFocus.current?.focus() }, [failure])

  function fail(code: string) {
    if (!mounted.current) return
    setFailure(code)
    setStatus('RootsMagic workspace needs attention.')
  }

  async function awaitResult(
    snapshot: JobSnapshot,
    expected: RootsMagicJobResult['kind'],
    isCurrent: () => boolean = () => true,
  ): Promise<RootsMagicJobResult | null> {
    let current = snapshot
    while (mounted.current && isCurrent()) {
      if (current.state === 'completed') {
        if (expected === 'inspection' && inspectionJob.current === current.job_id) inspectionJob.current = null
        const result = await rootsMagic.getRootsMagicJobResult({ schema_version: 1, job_id: current.job_id })
        if (!mounted.current || !isCurrent()) {
          if (result.ok && result.data.kind === 'inspection') {
            await rootsMagic.discardRootsMagicSource({ schema_version: 1, source_ref: result.data.result.source_ref }).catch(() => undefined)
          }
          return null
        }
        if (!result.ok) { fail(result.error.code); return null }
        if (result.data.kind !== expected) { fail('ROOTSMAGIC_RESULT_UNAVAILABLE'); return null }
        return result.data
      }
      if (current.state === 'failed' || current.state === 'cancelled') {
        fail(current.error_code ?? 'ROOTSMAGIC_RESULT_UNAVAILABLE')
        return null
      }
      await new Promise<void>((resolve) => window.setTimeout(resolve, 1000))
      if (!mounted.current || !isCurrent()) return null
      const result = await rootsMagic.getJob({ schema_version: 1, job_id: current.job_id })
      if (!mounted.current || !isCurrent()) {
        if (expected === 'inspection') void abandonInspection(current.job_id)
        return null
      }
      if (!result.ok) { fail(result.error.code); return null }
      current = result.data
    }
    return null
  }

  function invalidateSourceWork() {
    sourceGeneration.current += 1
    queryGeneration.current += 1
    setQueryPending(false)
    setOutputPending(false)
    setExportPending(false)
  }

  function resetForSource() {
    invalidateSourceWork()
    setSource(null)
    setDefinitions(null)
    setQuery(null)
    setSelectedPerson(null)
    setOutput(null)
    setReceipt(null)
    setConfirmed(false)
    setStatus('Choose a RootsMagic source to begin.')
  }

  async function chooseSource() {
    if (picking || discarding) return
    setPicking(true)
    setFailure(null)
    const oldSource = sourceRef.current
    if (oldSource) {
      try {
        const disposed = await rootsMagic.discardRootsMagicSource({ schema_version: 1, source_ref: oldSource })
        if (!disposed.ok && disposed.error.code !== 'FILE_GRANT_FORBIDDEN') {
          fail(disposed.error.code)
          return
        }
      } catch { fail('ROOTSMAGIC_SOURCE_UNAVAILABLE'); return }
      sourceRef.current = null
      if (!mounted.current) return
    }
    resetForSource()
    const selectionGeneration = sourceGeneration.current
    try {
      const picked = await rootsMagic.requestOpenFileGrant({ purpose: 'rootsmagic-read' })
      if (!picked.ok) { fail(picked.error.code); return }
      const grant = picked.data
      if (!grant) { setStatus('No RootsMagic source was chosen.'); return }
      let inspectedSource: string | null = null
      try {
        const submitted = await rootsMagic.inspectRootsMagicSource(grant.grantId)
        if (!submitted.ok) { fail(submitted.error.code); return }
        if (!mounted.current || sourceGeneration.current !== selectionGeneration) {
          void abandonInspection(submitted.data.job_id)
          return
        }
        inspectionJob.current = submitted.data.job_id
        const complete = await awaitResult(submitted.data, 'inspection', () => sourceGeneration.current === selectionGeneration)
        if (inspectionJob.current === submitted.data.job_id) inspectionJob.current = null
        if (!complete || complete.kind !== 'inspection' || !mounted.current || sourceGeneration.current !== selectionGeneration) return
        inspectedSource = complete.result.source_ref
        const presets = await rootsMagic.getRootsMagicPresets()
        if (!presets.ok) {
          if (mounted.current && sourceGeneration.current === selectionGeneration) fail(presets.error.code)
          return
        }
        if (!mounted.current || sourceGeneration.current !== selectionGeneration) return
        sourceRef.current = inspectedSource
        inspectedSource = null
        setSource({ grant, summary: complete.result })
        setDefinitions(presets.data)
        setStatus(`Source active: ${complete.result.friendly_name}. Choose a fixed preset.`)
      } finally {
        if (inspectedSource) {
          await rootsMagic.discardRootsMagicSource({ schema_version: 1, source_ref: inspectedSource }).catch(() => undefined)
        }
        await rootsMagic.revokeFileGrant(grant.grantId).catch(() => undefined)
      }
    } catch { if (sourceGeneration.current === selectionGeneration) fail('ROOTSMAGIC_SOURCE_UNAVAILABLE') }
    finally { if (mounted.current) setPicking(false) }
  }

  async function discardSource() {
    const activeSource = sourceRef.current
    if (!activeSource || discarding || picking) return
    invalidateSourceWork()
    const discardGeneration = sourceGeneration.current
    setDiscarding(true)
    setFailure(null)
    setStatus('Discarding active RootsMagic source…')
    try {
      const result = await rootsMagic.discardRootsMagicSource({ schema_version: 1, source_ref: activeSource })
      if (!mounted.current || sourceGeneration.current !== discardGeneration) return
      if (!result.ok && result.error.code !== 'FILE_GRANT_FORBIDDEN') {
        fail(result.error.code)
        return
      }
      sourceRef.current = null
      resetForSource()
      setStatus('The RootsMagic source was discarded.')
    } catch {
      if (mounted.current && sourceGeneration.current === discardGeneration) {
        fail('ROOTSMAGIC_SOURCE_UNAVAILABLE')
      }
    } finally { if (mounted.current) setDiscarding(false) }
  }

  async function runQuery(queryId: RootsMagicQueryId, offset: number, previousOffsets: readonly number[]) {
    if (!source || queryPending || discarding || (queryId !== 'people' && !selectedPerson)) return
    const generation = ++queryGeneration.current
    const operationSourceGeneration = sourceGeneration.current
    setQueryPending(true)
    setFailure(null)
    setReceipt(null)
    if (queryId === 'people') {
      setSelectedPerson(null)
      setOutput(null)
      setConfirmed(false)
    }
    try {
      const request: RootsMagicQueryRequest = { schema_version: 1, source_ref: source.summary.source_ref,
        query_id: queryId, person_id: queryId === 'people' ? null : selectedPerson?.personId ?? null,
        name_filter: queryId === 'people' ? nameFilter : '', offset, page_size: PAGE_SIZE }
      const submitted = await rootsMagic.queryRootsMagic(request)
      if (!submitted.ok) {
        if (generation === queryGeneration.current && sourceGeneration.current === operationSourceGeneration) fail(submitted.error.code)
        return
      }
      const complete = await awaitResult(submitted.data, 'query', () =>
        generation === queryGeneration.current && sourceGeneration.current === operationSourceGeneration)
      if (!complete || complete.kind !== 'query' || generation !== queryGeneration.current
        || sourceGeneration.current !== operationSourceGeneration || !mounted.current) return
      setQuery({ id: queryId, page: complete.result, previousOffsets })
      setStatus(`${complete.result.returned_rows} rows shown for ${queryId.replace('_', ' ')}.`)
    } catch {
      if (generation === queryGeneration.current && sourceGeneration.current === operationSourceGeneration) fail('ROOTSMAGIC_QUERY_UNAVAILABLE')
    } finally {
      if (generation === queryGeneration.current && sourceGeneration.current === operationSourceGeneration && mounted.current) setQueryPending(false)
    }
  }

  async function chooseOutput() {
    if (!source || !selectedPerson || outputPending || discarding) return
    const operationSourceGeneration = sourceGeneration.current
    const operationQueryGeneration = queryGeneration.current
    setOutputPending(true)
    setFailure(null)
    try {
      const suggestedName = `${source.summary.friendly_name.slice(0, 255 - ' export'.length)
        .replace(/[\uD800-\uDBFF]$/u, '')} export`
      const result = await rootsMagic.requestRootsMagicOutput(suggestedName)
      if (sourceGeneration.current !== operationSourceGeneration
        || queryGeneration.current !== operationQueryGeneration || !mounted.current) return
      if (!result.ok) {
        if (result.error.code === 'REQUEST_CANCELLED') setStatus('No new export folder was chosen.')
        else fail(result.error.code)
        return
      }
      setOutput(result.data)
      setConfirmed(false)
      setStatus(`New export folder selected: ${result.data.display_name}. Confirm the export scope to continue.`)
    } catch {
      if (sourceGeneration.current === operationSourceGeneration
        && queryGeneration.current === operationQueryGeneration) fail('ROOTSMAGIC_OUTPUT_UNAVAILABLE')
    } finally {
      if (mounted.current && sourceGeneration.current === operationSourceGeneration) setOutputPending(false)
    }
  }

  async function exportGedcom() {
    if (!source || !selectedPerson || !output || !confirmed || exportPending || discarding) return
    const operationSourceGeneration = sourceGeneration.current
    const count = Number(generations)
    const generationsValue = scope === 'connected' ? null : Number.isSafeInteger(count) && count >= 1 && count <= MAX_GENERATIONS ? count : null
    if (scope !== 'connected' && generationsValue === null) { fail('ROOTSMAGIC_EXPORT_INVALID'); return }
    setExportPending(true)
    setConfirmed(false)
    setFailure(null)
    const destination = output
    setOutput(null)
    try {
      const request: RootsMagicExportRequest = { schema_version: 1, source_ref: source.summary.source_ref,
        output_capability: destination.output_capability, root_person_id: selectedPerson.personId, scope,
        generations: generationsValue, living }
      const submitted = await rootsMagic.exportRootsMagic(request)
      if (!submitted.ok) {
        if (sourceGeneration.current === operationSourceGeneration) fail(submitted.error.code)
        return
      }
      const complete = await awaitResult(submitted.data, 'export', () => sourceGeneration.current === operationSourceGeneration)
      if (!complete || complete.kind !== 'export' || !mounted.current || sourceGeneration.current !== operationSourceGeneration) return
      setReceipt(complete.result)
      setStatus('Export complete. The new export folder is ready to reveal.')
    } catch {
      if (sourceGeneration.current === operationSourceGeneration) fail('ROOTSMAGIC_EXPORT_UNAVAILABLE')
    } finally {
      if (mounted.current && sourceGeneration.current === operationSourceGeneration) setExportPending(false)
    }
  }

  async function revealArtifact() {
    if (!receipt) return
    const operationSourceGeneration = sourceGeneration.current
    setFailure(null)
    try {
      const result = await rootsMagic.revealRootsMagicArtifact({ schema_version: 1, artifact_id: receipt.artifact_id })
      if (!mounted.current || sourceGeneration.current !== operationSourceGeneration) return
      if (!result.ok) { fail(result.error.code); return }
      setStatus('The export folder was revealed in your system file browser.')
    } catch {
      if (sourceGeneration.current === operationSourceGeneration) fail('ROOTSMAGIC_ARTIFACT_UNAVAILABLE')
    }
  }

  return <main aria-labelledby="rootsmagic-workspace-title">
    <h2 id="rootsmagic-workspace-title">RootsMagic workspace</h2>
    <p>Inspect a local RootsMagic source through fixed, read-only presets. This workspace never changes the source database.</p>
    <Button type="button" disabled={picking || discarding} onClick={() => { void chooseSource() }}>
      {picking ? 'Opening source…' : source ? 'Choose another RootsMagic source' : 'Choose RootsMagic source'}
    </Button>
    <p role="status" aria-live="polite">{status}</p>
    {failure && <CodedErrorView focusRef={errorFocus} code={failure}
      title="The RootsMagic workspace is unavailable." recovery="Choose the local source again. The source database was not changed." />}

    {source && <section aria-labelledby="rootsmagic-source-title">
      <h3 id="rootsmagic-source-title">Active source</h3>
      <FileGrantCard grant={source.grant} />
      <p>{source.summary.friendly_name}</p>
      <p>Fingerprint: {source.summary.fingerprint}</p>
      <p>Version: {source.summary.detected_version}</p>
      <p>Source access: {source.summary.grant_status_code}</p>
      <p>This source is read-only and is never modified.</p>
      <Button type="button" variant="quiet" disabled={discarding || picking} onClick={() => { void discardSource() }}>
        {discarding ? 'Discarding active source…' : 'Discard active source'}
      </Button>
    </section>}

    {source && definitions && <section aria-labelledby="rootsmagic-presets-title">
      <h3 id="rootsmagic-presets-title">Preset queries</h3>
      <p>Each result is bounded to {PAGE_SIZE} rows. Family links include parents, spouses, siblings, and children when recorded.</p>
      <form onSubmit={(event) => { event.preventDefault(); void runQuery('people', 0, []) }}>
        <label htmlFor="rootsmagic-name-filter">Find people by name</label>
        <input id="rootsmagic-name-filter" type="search" maxLength={200} value={nameFilter}
          onChange={(event) => setNameFilter(event.currentTarget.value)} />
        <Button type="submit" disabled={queryPending || discarding}>Search people</Button>
      </form>
      <div role="group" aria-label="RootsMagic preset queries">
        {definitions.queries.map((definition) => <Button key={definition.query_id} type="button" variant="quiet"
          disabled={queryPending || discarding || (definition.query_id !== 'people' && !selectedPerson)}
          onClick={() => { void runQuery(definition.query_id, 0, []) }}>{definition.label}</Button>)}
      </div>
      {!selectedPerson && <p>Choose a person from People before using Family links or Events.</p>}
      {queryPending && <p role="status">Loading the requested page…</p>}
      {query && <section aria-labelledby="rootsmagic-results-title">
        <h4 id="rootsmagic-results-title">{query.id === 'family_links' ? 'Family links' : query.id === 'events' ? 'Events' : 'People'} results</h4>
        <table>
          <thead><tr>{query.page.columns.map((column) => <th key={column} scope="col">{column}</th>)}
            {query.id === 'people' && <th scope="col">Selection</th>}</tr></thead>
          <tbody>{query.page.rows.map((row, index) => {
            const person = query.id === 'people' ? rowPerson(query.page, row.values) : null
            return <tr key={`${query.page.offset}:${index}`}>
              {row.values.map((value, valueIndex) => <td key={`${query.page.columns[valueIndex]}:${valueIndex}`}>{displayValue(value)}</td>)}
              {query.id === 'people' && <td>{person ? <Button type="button" variant="quiet" disabled={discarding} onClick={() => {
                setSelectedPerson(person); setOutput(null); setReceipt(null); setConfirmed(false)
                setStatus(`Selected root: ${person.label}`)
              }}>Select {person.label}</Button> : hasUnsupportedPersonId(query.page, row.values) ?
                <span>This person ID is outside the desktop safe-integer range and cannot be selected for export.</span> : null}</td>}
            </tr>
          })}</tbody>
        </table>
        <p>{query.page.total_rows === null ? 'Total rows are not available.' : `${query.page.total_rows} total rows.`}</p>
        <Button type="button" variant="quiet" disabled={queryPending || discarding || query.previousOffsets.length === 0}
          onClick={() => { const prior = query.previousOffsets.at(-1); if (prior !== undefined) void runQuery(query.id, prior, query.previousOffsets.slice(0, -1)) }}>Previous page</Button>
        <Button type="button" variant="quiet" disabled={queryPending || discarding || !query.page.has_more || query.page.next_offset === null}
          onClick={() => { if (query.page.next_offset !== null) void runQuery(query.id, query.page.next_offset, [...query.previousOffsets, query.page.offset]) }}>Next page</Button>
      </section>}
    </section>}

    {source && <section aria-labelledby="rootsmagic-export-title">
      <h3 id="rootsmagic-export-title">Export selected root</h3>
      <p>Export creates a new folder using the portable GEDCOM 5.5.5 profile. Choose its name with the native desktop picker.</p>
      <fieldset disabled={!selectedPerson || exportPending || discarding}>
        <legend>Family scope</legend>
        {(['connected', 'ancestors', 'descendants'] as const).map((choice) => <label key={choice}>
          <input type="radio" name="rootsmagic-scope" checked={scope === choice} onChange={() => {
            setScope(choice); setConfirmed(false); setReceipt(null)
          }} />{choice === 'connected' ? 'Connected people' : choice.charAt(0).toUpperCase() + choice.slice(1)}
        </label>)}
        {scope !== 'connected' && <label>Generations
          <input type="number" min={1} max={MAX_GENERATIONS} step={1} value={generations}
            onChange={(event) => { setGenerations(event.currentTarget.value); setConfirmed(false) }} />
        </label>}
      </fieldset>
      <fieldset disabled={!selectedPerson || exportPending || discarding}>
        <legend>Living people</legend>
        <label><input type="radio" name="rootsmagic-living" checked={living === 'exclude'}
          onChange={() => { setLiving('exclude'); setConfirmed(false) }} />Exclude living people</label>
        <label><input type="radio" name="rootsmagic-living" checked={living === 'include'}
          onChange={() => { setLiving('include'); setConfirmed(false) }} />Include living people</label>
        <label><input type="radio" name="rootsmagic-living" checked={living === 'anonymize'}
          onChange={() => { setLiving('anonymize'); setConfirmed(false) }} />Anonymize living people</label>
      </fieldset>
      <Button type="button" variant="quiet" disabled={!selectedPerson || outputPending || exportPending || discarding}
        onClick={() => { void chooseOutput() }}>{outputPending ? 'Choosing export folder…' : 'Choose new export folder'}</Button>
      {output && <p>New export folder: {output.display_name}</p>}
      <label><input type="checkbox" checked={confirmed} disabled={!selectedPerson || !output || exportPending || discarding}
        onChange={(event) => setConfirmed(event.currentTarget.checked)} />I confirm this export is limited to {scopeLabel(scope)}.</label>
      <Button type="button" disabled={!selectedPerson || !output || !confirmed || exportPending || discarding}
        onClick={() => { void exportGedcom() }}>{exportPending ? 'Exporting…' : 'Export portable GEDCOM'}</Button>
      {receipt && <section aria-label="Export receipt"><p>Export complete using the portable GEDCOM {receipt.gedcom_version} profile.</p>
        <Button type="button" variant="quiet" disabled={discarding} onClick={() => { void revealArtifact() }}>Reveal export folder</Button></section>}
    </section>}
  </main>
}
