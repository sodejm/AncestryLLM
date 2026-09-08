/** Presents read-only, session-local GEDCOM sources without filesystem or provider authority. */
import { useEffect, useRef, useState } from 'react'
import type { AncestryBridge, JobSnapshot } from '../../shared-contract/desktop'
import type { GedcomInspection, GedcomRootCandidate, GedcomRootPage } from '../../shared-contract/gedcom'
import { Button } from './components/Button'
import { CodedErrorView } from './design-system/CodedErrorView'

const bridgeFromWindow = (): AncestryBridge => (window as unknown as { ancestry: AncestryBridge }).ancestry
const MAX_SOURCES = 8
const PAGE_SIZE = 25

interface Source {
  id: number
  active: boolean
  jobId?: string
  timer?: ReturnType<typeof setTimeout>
}
interface SourceView {
  id: number
  displayName?: string
  inspection?: GedcomInspection
  error?: string
}

function IntakeError({ code }: { code: string }) {
  return <CodedErrorView code={code} title="This GEDCOM source is unavailable."
    recovery="Remove this source and choose it again. No source file was changed." />
}

function FindingPreview({ bridge, jobId, label, code, subjectRef }: {
  bridge: AncestryBridge; jobId: string; label: string; code: string; subjectRef: string
}) {
  const [person, setPerson] = useState<GedcomRootCandidate | null>(null)
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)
  const generation = useRef(0)

  useEffect(() => () => { generation.current += 1 }, [bridge, jobId, subjectRef])

  async function review() {
    const request = ++generation.current
    setPending(true)
    setFailed(false)
    setPerson(null)
    try {
      const result = await bridge.queryGedcomRoots({ schema_version: 1, job_id: jobId,
        query: subjectRef, limit: 1, cursor: null })
      if (generation.current !== request) return
      const candidate = result.ok ? result.data.candidates[0] : undefined
      if (result.ok && result.data.candidates.length === 1 && result.data.total_count === 1
        && result.data.next_cursor === null && candidate?.person_ref === subjectRef) {
        setPerson(candidate)
      } else {
        setFailed(true)
      }
    } catch {
      if (generation.current === request) setFailed(true)
    } finally {
      if (generation.current === request) setPending(false)
    }
  }

  return <>
    <Button type="button" variant="quiet" disabled={pending} onClick={() => { void review() }}>
      Review affected person
    </Button>
    {pending && <p role="status">Finding affected person…</p>}
    {failed && <CodedErrorView code="GEDCOM_INTAKE_UNAVAILABLE" title="The affected person is unavailable."
      recovery="Try reviewing this finding again. Your root choice was not changed." />}
    {person && <section aria-label={`Affected person for ${code} in ${label}`} aria-live="polite">
      <p>{person.display_name || 'Unnamed individual'} · {person.source_identifier}</p>
      <p>Born: {person.birth_date || 'unknown'} · Died: {person.death_date || 'unknown'}</p>
      <p>{person.relationship_summary}</p>
      <p>Read-only preview. Your root choice has not changed.</p>
    </section>}
  </>
}

function RootSelector({ bridge, jobId, label }: { bridge: AncestryBridge; jobId: string; label: string }) {
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState<GedcomRootPage | null>(null)
  const [selection, setSelection] = useState<GedcomRootCandidate | 'none' | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)

  async function search(nextQuery: string, cursor: string | null) {
    const request = ++generation.current
    setPending(true)
    setError(null)
    try {
      const result = await bridge.queryGedcomRoots({ schema_version: 1, job_id: jobId,
        query: nextQuery, limit: PAGE_SIZE, cursor })
      if (generation.current !== request) return
      if (result.ok) {
        setPage(result.data)
        setQuery(nextQuery)
      } else {
        setPage(null)
        setSelection(null)
        setError(result.error.code)
      }
    } catch {
      if (generation.current !== request) return
      setPage(null)
      setSelection(null)
      setError('GEDCOM_INTAKE_UNAVAILABLE')
    } finally {
      if (generation.current === request) setPending(false)
    }
  }

  useEffect(() => {
    void search('', null)
    return () => { generation.current += 1 }
    // This component is keyed by source; draft queries never cause automatic requests.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bridge, jobId])

  return <section aria-label={`Root selection for ${label}`}>
    <h3>Choose a root</h3>
    <p>A root limits a future operation. Choosing one here does not import or modify records.</p>
    <form onSubmit={(event) => { event.preventDefault(); void search(draft, null) }}>
      <label htmlFor={`root-search-${jobId}`}>Find a root in {label}</label>
      <input id={`root-search-${jobId}`} type="search" maxLength={128} value={draft}
        onChange={(event) => setDraft(event.target.value)} />
      <Button type="submit" disabled={pending}>Search candidates</Button>
    </form>
    {pending && <p role="status">Finding candidates…</p>}
    {error && <IntakeError code={error} />}
    <fieldset disabled={pending || error !== null}>
      <legend>Root choice for {label}</legend>
      <label className="gedcom-root-choice">
        <input type="radio" name={`root-${jobId}`} checked={selection === 'none'}
          onChange={() => setSelection('none')} />
        Continue without a root
      </label>
      {page?.candidates.map((candidate) => <label key={candidate.person_ref} className="gedcom-root-choice">
        <input type="radio" name={`root-${jobId}`}
          checked={selection !== null && selection !== 'none' && selection.person_ref === candidate.person_ref}
          onChange={() => setSelection(candidate)} />
        {candidate.display_name || 'Unnamed individual'} · {candidate.source_identifier}
        {' · '}Born: {candidate.birth_date || 'unknown'} · Died: {candidate.death_date || 'unknown'}
        {' · '}{candidate.relationship_summary}
      </label>)}
    </fieldset>
    {page && <p>{page.total_count} matching individuals; at most {PAGE_SIZE} shown per page.</p>}
    {page?.candidates.length === 0 && <p>No matching individuals.</p>}
    {page?.next_cursor && <Button type="button" variant="quiet" disabled={pending}
      onClick={() => { void search(query, page.next_cursor) }}>Next candidates</Button>}
    <p role="status">{selection === null ? 'No root selected.' : selection === 'none'
      ? 'Explicit choice: continue without a root.'
      : `Selected root: ${selection.display_name || 'Unnamed individual'} · ${selection.source_identifier}`}</p>
  </section>
}

/** Owns at most eight ephemeral inspections; removal and navigation discard server-side results. */
export function GedcomIntakeWorkspace({ bridge = bridgeFromWindow() }: { bridge?: AncestryBridge }) {
  const [sources, setSources] = useState<SourceView[]>([])
  const [picking, setPicking] = useState(false)
  const entries = useRef(new Map<number, Source>())
  const nextId = useRef(0)
  const pickerBusy = useRef(false)
  const mounted = useRef(true)

  const discard = (jobId: string) => {
    void bridge.discardGedcomInspection({ schema_version: 1, job_id: jobId }).catch(() => undefined)
  }
  const release = (source: Source) => {
    source.active = false
    if (source.timer !== undefined) clearTimeout(source.timer)
    if (source.jobId) discard(source.jobId)
  }

  useEffect(() => {
    mounted.current = true
    const owned = entries.current
    return () => {
      mounted.current = false
      for (const source of owned.values()) release(source)
      owned.clear()
    }
    // Cleanup is bound to this bridge's source ownership, not to render state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bridge])

  function update(source: Source, patch: Partial<SourceView>) {
    if (!mounted.current || !source.active) return
    setSources((current) => current.map((item) => item.id === source.id ? { ...item, ...patch } : item))
  }
  function remove(source: Source) {
    release(source)
    entries.current.delete(source.id)
    if (mounted.current) setSources((current) => current.filter((item) => item.id !== source.id))
  }
  function fail(source: Source, code: string) {
    update(source, { error: code })
    release(source)
  }

  async function inspectProgress(source: Source, snapshot: JobSnapshot, attempt = 0): Promise<void> {
    if (!source.active || !mounted.current) return
    try {
      if (snapshot.state === 'completed') {
        const result = await bridge.getGedcomInspection({ schema_version: 1, job_id: snapshot.job_id })
        if (!source.active || !mounted.current) return
        if (result.ok) update(source, { inspection: result.data })
        else fail(source, result.error.code)
      } else if (snapshot.state === 'failed' || snapshot.state === 'cancelled' || attempt >= 300) {
        fail(source, snapshot.error_code ?? 'GEDCOM_INTAKE_UNAVAILABLE')
      } else {
        source.timer = setTimeout(() => {
          if (!source.active || !mounted.current) return
          void bridge.getJob({ schema_version: 1, job_id: snapshot.job_id }).then((result) => {
            if (!source.active || !mounted.current) return
            if (result.ok) void inspectProgress(source, result.data, attempt + 1)
            else fail(source, result.error.code)
          }).catch(() => fail(source, 'GEDCOM_INTAKE_UNAVAILABLE'))
        }, 1000)
      }
    } catch { fail(source, 'GEDCOM_INTAKE_UNAVAILABLE') }
  }

  async function add() {
    if (pickerBusy.current || entries.current.size >= MAX_SOURCES) return
    pickerBusy.current = true
    setPicking(true)
    const source: Source = { id: ++nextId.current, active: true }
    entries.current.set(source.id, source)
    setSources((current) => [...current, { id: source.id }])
    try {
      const picked = await bridge.requestOpenFileGrant({ purpose: 'gedcom-read' })
      if (!picked.ok) { fail(source, picked.error.code); return }
      const grant = picked.data
      if (!grant) { remove(source); return }
      try {
        if (!source.active || !mounted.current) return
        update(source, { displayName: grant.metadata.displayName })
        const result = await bridge.inspectGedcom(grant.grantId)
        if (!result.ok) { fail(source, result.error.code); return }
        source.jobId = result.data.job_id
        if (!source.active || !mounted.current) { discard(source.jobId); return }
        await inspectProgress(source, result.data)
      } finally {
        await bridge.revokeFileGrant(grant.grantId).catch(() => undefined)
      }
    } catch { fail(source, 'GEDCOM_INTAKE_UNAVAILABLE') }
    finally {
      pickerBusy.current = false
      if (mounted.current) setPicking(false)
    }
  }

  function move(index: number, delta: number) {
    setSources((current) => {
      const reordered = [...current]
      const other = index + delta
      if (other < 0 || other >= reordered.length) return current
      const selected = reordered[index]
      const adjacent = reordered[other]
      if (!selected || !adjacent) return current
      reordered[index] = adjacent
      reordered[other] = selected
      return reordered
    })
  }

  return <div className="gedcom-intake">
    <p>Read-only local intake. Nothing is uploaded and no output file is created. This source-level
      workspace does not establish packaged-release support.</p>
    <p>Sources and root choices are temporary. Removing a source or leaving this screen discards its inspection.</p>
    <Button type="button" disabled={picking || sources.length >= MAX_SOURCES} onClick={() => { void add() }}>
      Add GEDCOM source
    </Button>
    <p>{sources.length} of {MAX_SOURCES} source slots used. The list order is your chosen source order.</p>
    {sources.map((source, index) => {
      const label = `Source ${source.id}`
      const summary = source.inspection?.value.summary
      const jobId = entries.current.get(source.id)?.jobId
      return <article className="summary-card" key={source.id} aria-labelledby={`source-${source.id}`}>
        <h2 id={`source-${source.id}`}>{label}</h2>
        {source.displayName && <p>{source.displayName}</p>}
        <div className="home-actions">
          <Button type="button" variant="quiet" disabled={index === 0} aria-label={`Move ${label} up`}
            onClick={() => move(index, -1)}>Move up</Button>
          <Button type="button" variant="quiet" disabled={index === sources.length - 1} aria-label={`Move ${label} down`}
            onClick={() => move(index, 1)}>Move down</Button>
          <Button type="button" variant="quiet" aria-label={`Remove ${label}`}
            onClick={() => { const entry = entries.current.get(source.id); if (entry) remove(entry) }}>Remove</Button>
        </div>
        {source.error ? <IntakeError code={source.error} /> : !summary ? <p role="status">Inspecting source…</p> : <>
          <p>GEDCOM {summary.gedcom_version || 'unknown'} · {summary.encoding || 'unknown encoding'}</p>
          <p>{summary.individual_count} individuals · {summary.family_count} families · {summary.other_record_count} other records</p>
          <p className="gedcom-fingerprint">Source SHA-256: {summary.source.sha256}</p>
          <p>{summary.source.size_bytes} bytes. Inspection does not certify genealogy accuracy, complete
            GEDCOM conformance, or lossless conversion by other tools.</p>
          <ul aria-label={`Validation findings for ${label}`}>
            {source.inspection?.value.findings.map((finding, findingIndex) => <li key={findingIndex}>
              <span>{finding.code}</span> ({finding.severity})
              {finding.subject_ref === null ? <p>Source-wide finding.</p> : jobId &&
                <FindingPreview key={`${jobId}:${finding.subject_ref}`} bridge={bridge} jobId={jobId}
                  label={label} code={finding.code} subjectRef={finding.subject_ref} />}
            </li>)}
          </ul>
          <p>{source.inspection?.value.finding_count} findings; at most 100 shown.</p>
          {jobId && <RootSelector bridge={bridge} jobId={jobId} label={label} />}
        </>}
      </article>
    })}
  </div>
}
