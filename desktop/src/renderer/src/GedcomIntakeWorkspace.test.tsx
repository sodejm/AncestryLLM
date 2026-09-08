/** Verifies bounded, ephemeral GEDCOM intake and explicit keyboard-accessible root selection. */
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createMockAncestryBridge } from '../../mock-bridge/desktop'
import type { AncestryBridge, BridgeResult, FileGrant, JobSnapshot } from '../../shared-contract/desktop'
import type { GedcomInspection, GedcomRootCandidate, GedcomRootPage } from '../../shared-contract/gedcom'
import { GedcomIntakeWorkspace } from './GedcomIntakeWorkspace'

const success = <T,>(data: T): BridgeResult<T> => ({ ok: true, protocolVersion: '1', data })
const grant: FileGrant = {
  grantId: `grt_${'a'.repeat(64)}`, purpose: 'gedcom-read', access: 'read',
  scope: { originatingWindow: 'requesting-window', lifetime: 'app-session', redemption: 'single-use' },
  metadata: { displayName: 'fictional.ged', format: 'gedcom', sizeBytes: 256, validation: 'validated-input' },
}
const job: JobSnapshot = {
  schema_version: 1, sequence: 1, job_id: 'j123456', name: 'Inspect GEDCOM', state: 'completed',
  submitted_at: '2026-08-12T12:00:00+00:00', started_at: '2026-08-12T12:00:01+00:00',
  finished_at: '2026-08-12T12:00:02+00:00', resource_refs: [], artifact: null,
  outcome_summary: null, next_action: null, error_code: null, error_message: null,
  error_remediation: null, progress: null, cancellation_requested_at: null, cancellation_deferred_by: null,
}
const inspection: GedcomInspection = {
  schema_version: 1, operation: 'gedcom.inspect', value: {
    summary: { source: { artifact_id: `art_${'a'.repeat(64)}`, artifact_type: 'gedcom',
      media_type: 'text/vnd.gedcom', size_bytes: 256, status: 'ready', sha256: 'b'.repeat(64) },
    gedcom_version: '5.5.5', encoding: 'UTF-8', individual_count: 2, family_count: 1, other_record_count: 2 },
    findings: [{ code: 'gedcom-quality-limits', severity: 'info', subject_ref: null }],
    finding_count: 1, root_candidate_count: 2,
  },
}
const page: GedcomRootPage = {
  schema_version: 1, total_count: 2, next_cursor: null,
  candidates: ['a', 'b'].map((letter, index) => ({
    person_ref: `person:${letter.repeat(32)}`, reason_code: 'individual-record',
    display_name: 'Alex Example', source_identifier: `@I${index + 1}@`,
    birth_date: index === 0 ? '1900' : '1930', death_date: '', relationship_summary: '1 parent; 0 spouses; 0 children',
  })),
}
const affectedPerson: GedcomRootCandidate = {
  person_ref: `person:${'c'.repeat(32)}`, reason_code: 'individual-record',
  display_name: 'Jordan Fixture', source_identifier: '@I3@', birth_date: '', death_date: '',
  relationship_summary: '0 parents; 0 partners; 0 children',
}
function bridgeFor(): AncestryBridge {
  let nextJob = 123456
  return {
    ...createMockAncestryBridge('success'),
    requestOpenFileGrant: vi.fn().mockResolvedValue(success(grant)),
    revokeFileGrant: vi.fn().mockResolvedValue(success({ revoked: true })),
    inspectGedcom: vi.fn(async () => success({ ...job, job_id: `j${nextJob++}` })),
    getJob: vi.fn().mockResolvedValue(success(job)),
    getGedcomInspection: vi.fn().mockResolvedValue(success(inspection)),
    queryGedcomRoots: vi.fn().mockResolvedValue(success(page)),
    discardGedcomInspection: vi.fn().mockResolvedValue(success({ schema_version: 1 })),
  }
}
function bridgeWithFinding(): AncestryBridge {
  const bridge = bridgeFor()
  vi.mocked(bridge.getGedcomInspection).mockResolvedValue(success({ ...inspection, value: {
    ...inspection.value, findings: [...inspection.value.findings,
      { code: 'gedcom-date-invalid', severity: 'warning', subject_ref: affectedPerson.person_ref }],
    finding_count: 2, root_candidate_count: 3,
  } }))
  vi.mocked(bridge.queryGedcomRoots).mockImplementation(async (request) => success(
    request.query === affectedPerson.person_ref
      ? { schema_version: 1, candidates: [affectedPerson], total_count: 1, next_cursor: null }
      : page,
  ))
  return bridge
}
async function addSource() {
  await userEvent.click(screen.getByRole('button', { name: 'Add GEDCOM source' }))
}

describe('GEDCOM intake workspace', () => {
  it('opens a bounded finding anchor by keyboard without changing the chosen root', async () => {
    const bridge = bridgeWithFinding()
    render(<GedcomIntakeWorkspace bridge={bridge} />)
    await addSource()
    const selected = await screen.findByRole('radio', { name: /Alex Example.*@I1@/ })
    await userEvent.click(selected)
    const findings = screen.getByRole('list', { name: 'Validation findings for Source 1' })
    expect(within(findings).getByText('Source-wide finding.')).toBeVisible()
    expect(within(findings).queryByText(/Jordan Fixture/)).not.toBeInTheDocument()
    expect(bridge.queryGedcomRoots).toHaveBeenCalledTimes(1)
    const review = within(findings).getByRole('button', { name: 'Review affected person' })
    review.focus()
    await userEvent.keyboard('{Enter}')
    const preview = await screen.findByRole('region', { name: 'Affected person for gedcom-date-invalid in Source 1' })
    expect(within(preview).getByText(/Jordan Fixture.*@I3@/)).toBeVisible()
    expect(within(preview).getByText(/0 parents; 0 partners; 0 children/)).toBeVisible()
    expect(bridge.queryGedcomRoots).toHaveBeenLastCalledWith({ schema_version: 1, job_id: job.job_id,
      query: affectedPerson.person_ref, limit: 1, cursor: null })
    expect(selected).toBeChecked()
    expect(screen.getByText('Selected root: Alex Example · @I1@')).toBeVisible()
    expect(screen.queryByRole('radio', { name: /Jordan Fixture/ })).not.toBeInTheDocument()
  })

  it.each(['missing', 'mismatched', 'ambiguous', 'paginated', 'failure'] as const)(
    'fails closed for a %s finding anchor without clearing an explicit no-root choice', async (response) => {
      const bridge = bridgeWithFinding()
      render(<GedcomIntakeWorkspace bridge={bridge} />)
      await addSource()
      const noRoot = await screen.findByRole('radio', { name: 'Continue without a root' })
      await userEvent.click(noRoot)
      if (response === 'failure') {
        vi.mocked(bridge.queryGedcomRoots).mockRejectedValueOnce(new Error('/private/family/secret.ged'))
      } else {
        vi.mocked(bridge.queryGedcomRoots).mockResolvedValueOnce(success({ schema_version: 1,
          candidates: response === 'missing' ? [] : response === 'mismatched' ? page.candidates.slice(0, 1) : [affectedPerson],
          total_count: response === 'missing' ? 0 : response === 'ambiguous' ? 2 : 1,
          next_cursor: response === 'paginated' ? `c1_00000001_${'d'.repeat(64)}` : null }))
      }
      await userEvent.click(screen.getByRole('button', { name: 'Review affected person' }))
      expect(await screen.findByText('Code: GEDCOM_INTAKE_UNAVAILABLE')).toBeVisible()
      expect(noRoot).toBeChecked()
      expect(document.body.textContent).not.toContain('secret.ged')
      expect(screen.queryByRole('region', { name: /Affected person for/ })).not.toBeInTheDocument()
    },
  )

  it('drops a late finding preview when its source is removed', async () => {
    const bridge = bridgeWithFinding()
    render(<GedcomIntakeWorkspace bridge={bridge} />)
    await addSource()
    await screen.findByRole('radio', { name: 'Continue without a root' })
    let resolve!: (result: BridgeResult<GedcomRootPage>) => void
    vi.mocked(bridge.queryGedcomRoots).mockImplementationOnce(() => new Promise((done) => { resolve = done }))
    await userEvent.click(screen.getByRole('button', { name: 'Review affected person' }))
    expect(screen.getByRole('button', { name: 'Review affected person' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Remove Source 1' }))
    await act(async () => { resolve(success({ schema_version: 1, candidates: [affectedPerson],
      total_count: 1, next_cursor: null })) })
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('Jordan Fixture')
  })

  it('presents metadata, bounded distinct candidates, and no guessed root with keyboard selection', async () => {
    const bridge = bridgeFor()
    render(<GedcomIntakeWorkspace bridge={bridge} />)
    await addSource()
    expect(await screen.findByText('GEDCOM 5.5.5 · UTF-8')).toBeVisible()
    expect(screen.getByText('2 individuals · 1 families · 2 other records')).toBeVisible()
    expect(screen.getByText('gedcom-quality-limits')).toBeVisible()
    expect(screen.getByText('No root selected.')).toBeVisible()
    const candidates = await screen.findAllByRole('radio')
    expect(candidates).toHaveLength(3)
    expect(candidates.every((candidate) => !(candidate as HTMLInputElement).checked)).toBe(true)
    const second = screen.getByRole('radio', { name: /Alex Example.*@I2@.*1930/ })
    second.focus()
    await userEvent.keyboard(' ')
    expect(second).toBeChecked()
    const noRoot = screen.getByRole('radio', { name: 'Continue without a root' })
    await userEvent.click(noRoot)
    expect(noRoot).toBeChecked()
    expect(second).not.toBeChecked()
    expect(bridge.queryGedcomRoots).toHaveBeenCalledWith({ schema_version: 1, job_id: job.job_id,
      query: '', limit: 25, cursor: null })
    expect(bridge.revokeFileGrant).toHaveBeenCalledWith(grant.grantId)
    expect(screen.queryByRole('button', { name: /upload|merge|export|import/i })).not.toBeInTheDocument()
  })

  it('replaces bounded search pages without losing an explicit choice', async () => {
    const bridge = bridgeFor()
    const cursor = `c1_00000019_${'c'.repeat(64)}`
    vi.mocked(bridge.queryGedcomRoots).mockResolvedValueOnce(success({ ...page, next_cursor: cursor }))
    render(<GedcomIntakeWorkspace bridge={bridge} />)
    await addSource()
    await userEvent.click(await screen.findByRole('radio', { name: /Alex Example.*@I1@/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Next candidates' }))
    expect(bridge.queryGedcomRoots).toHaveBeenLastCalledWith({ schema_version: 1, job_id: job.job_id,
      query: '', limit: 25, cursor })
    const search = screen.getByRole('searchbox', { name: 'Find a root in Source 1' })
    expect(search).toHaveAttribute('maxlength', '128')
    await userEvent.type(search, 'Alex')
    expect(bridge.queryGedcomRoots).toHaveBeenCalledTimes(2)
    vi.mocked(bridge.queryGedcomRoots).mockResolvedValueOnce(success({ schema_version: 1,
      candidates: [], total_count: 0, next_cursor: null }))
    await userEvent.click(screen.getByRole('button', { name: 'Search candidates' }))
    expect(await screen.findByText('No matching individuals.')).toBeVisible()
    expect(screen.getByText(/Selected root: Alex Example · @I1@/)).toBeVisible()
    expect(screen.queryByRole('radio', { name: /@I2@/ })).not.toBeInTheDocument()
    expect(bridge.queryGedcomRoots).toHaveBeenLastCalledWith({ schema_version: 1, job_id: job.job_id,
      query: 'Alex', limit: 25, cursor: null })
  })

  it('reorders sources, preserves their choices, and discards removed and unmounted sources', async () => {
    const bridge = bridgeFor()
    const view = render(<GedcomIntakeWorkspace bridge={bridge} />)
    await addSource()
    await userEvent.click(await screen.findByRole('radio', { name: 'Continue without a root' }))
    await addSource()
    await screen.findByRole('heading', { name: 'Source 2' })
    await userEvent.click(screen.getByRole('button', { name: 'Move Source 2 up' }))
    expect(screen.getAllByRole('article').map((article) => within(article).getByRole('heading', { level: 2 }).textContent))
      .toEqual(['Source 2', 'Source 1'])
    const sourceOne = screen.getByRole('article', { name: 'Source 1' })
    expect(within(sourceOne).getByRole('radio', { name: 'Continue without a root' })).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Remove Source 1' }))
    expect(bridge.discardGedcomInspection).toHaveBeenCalledWith({ schema_version: 1, job_id: 'j123456' })
    view.unmount()
    expect(bridge.discardGedcomInspection).toHaveBeenCalledWith({ schema_version: 1, job_id: 'j123457' })
  })

  it('discards a late inspection response after removal without reviving its labels', async () => {
    const bridge = bridgeFor()
    let resolve!: (result: BridgeResult<JobSnapshot>) => void
    vi.mocked(bridge.inspectGedcom).mockImplementation(() => new Promise((done) => { resolve = done }))
    render(<GedcomIntakeWorkspace bridge={bridge} />)
    await addSource()
    await waitFor(() => expect(bridge.inspectGedcom).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('button', { name: 'Remove Source 1' }))
    await act(async () => { resolve(success(job)) })
    expect(bridge.discardGedcomInspection).toHaveBeenCalledWith({ schema_version: 1, job_id: job.job_id })
    expect(bridge.getGedcomInspection).not.toHaveBeenCalled()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
  })

  it('preserves valid sources on picker cancellation or coded failure without rendering raw errors', async () => {
    const bridge = bridgeFor()
    render(<GedcomIntakeWorkspace bridge={bridge} />)
    await addSource()
    await screen.findByText('GEDCOM 5.5.5 · UTF-8')
    vi.mocked(bridge.requestOpenFileGrant).mockResolvedValueOnce(success(null))
    await addSource()
    expect(screen.getAllByRole('article')).toHaveLength(1)
    vi.mocked(bridge.requestOpenFileGrant).mockRejectedValueOnce(new Error('/private/family/secret.ged'))
    await addSource()
    expect(await screen.findByText('Code: GEDCOM_INTAKE_UNAVAILABLE')).toBeVisible()
    expect(document.body.textContent).not.toContain('secret.ged')
    expect(screen.getAllByRole('article')).toHaveLength(2)
  })
})
