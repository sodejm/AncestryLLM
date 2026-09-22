/** Verifies bounded, opaque RootsMagic source inspection, preset paging, and explicit export. */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AncestryBridge, BridgeResult, FileGrant, JobSnapshot } from '../../shared-contract/desktop'
import type {
  RootsMagicExportReceipt,
  RootsMagicJobResult,
  RootsMagicOutputSelection,
  RootsMagicPresetDefinitions,
  RootsMagicResultPage,
} from '../../shared-contract/rootsmagic'
import { RootsMagicWorkspace } from './RootsMagicWorkspace'

const success = <T,>(data: T): BridgeResult<T> => ({ ok: true, protocolVersion: '1', data })
const failure = <T,>(code: 'REQUEST_CANCELLED' | 'SIDECAR_UNAVAILABLE'): BridgeResult<T> => ({
  ok: false,
  protocolVersion: '1',
  error: { code, message: 'Internal bridge detail is not rendered.', remediation: 'Choose a source again.' },
})
const sourceRef = 'a'.repeat(64)
const fingerprint = 'b'.repeat(64)
const grant: FileGrant = {
  grantId: `grt_${'c'.repeat(64)}`, purpose: 'rootsmagic-read', access: 'read',
  scope: { originatingWindow: 'requesting-window', lifetime: 'app-session', redemption: 'single-use' },
  metadata: { displayName: 'fictional-family.rmtree', format: 'rootsmagic', sizeBytes: 256, validation: 'validated-input' },
}
const job: JobSnapshot = {
  schema_version: 1, sequence: 1, job_id: 'job_fixture_0001', name: 'Inspect RootsMagic', state: 'completed',
  submitted_at: '2026-09-21T12:00:00+00:00', started_at: '2026-09-21T12:00:01+00:00',
  finished_at: '2026-09-21T12:00:02+00:00', resource_refs: [], artifact: null, outcome_summary: null,
  next_action: null, error_code: null, error_message: null, error_remediation: null, progress: null,
  cancellation_requested_at: null, cancellation_deferred_by: null,
}
const definitions: RootsMagicPresetDefinitions = {
  schema_version: 1,
  queries: [
    { query_id: 'people', label: 'People', description: 'Find people by name.', maximum_rows: 25, parameters: [] },
    { query_id: 'family_links', label: 'Family links', description: 'Show parents, spouses, and children.', maximum_rows: 25, parameters: [] },
    { query_id: 'events', label: 'Events', description: 'Show recorded events.', maximum_rows: 25, parameters: [] },
  ],
}
const people: RootsMagicResultPage = {
  schema_version: 1, query_id: 'people', columns: ['person_id', 'display_name', 'sex', 'living'],
  rows: [{ values: [101, 'Alex Example', 'M', false] }, { values: [102, 'Jordan Example', 'F', true] }],
  offset: 0, returned_rows: 2, total_rows: 3, has_more: true, next_offset: 2,
}
const secondPeople: RootsMagicResultPage = {
  ...people, rows: [{ values: [103, 'Morgan Example', null, null] }], offset: 2, returned_rows: 1, has_more: false,
  next_offset: null,
}
const familyLinks: RootsMagicResultPage = {
  schema_version: 1, query_id: 'family_links', columns: ['person_id', 'display_name', 'relationship'],
  rows: [{ values: [102, 'Jordan Example', 'sibling'] }],
  offset: 0, returned_rows: 1, total_rows: null, has_more: false, next_offset: null,
}
const events: RootsMagicResultPage = {
  schema_version: 1, query_id: 'events', columns: ['event_type', 'date', 'place'],
  rows: [{ values: ['Birth', 'D.+19000101..+00000000..', 'Fictional Town'] }],
  offset: 0, returned_rows: 1, total_rows: null, has_more: false, next_offset: null,
}
const unsupportedPerson: RootsMagicResultPage = {
  ...people,
  rows: [{ values: ['9007199254740993', 'Older Fictional Person', null, null] }],
  returned_rows: 1, total_rows: 1, has_more: false, next_offset: null,
}
const inspection: RootsMagicJobResult = { schema_version: 1, kind: 'inspection', result: {
  schema_version: 1, source_ref: sourceRef, friendly_name: 'Fictional Family', fingerprint,
  detected_version: 'unknown', grant_status_code: 'active', immutable: true,
} }

function queryResult(result: RootsMagicResultPage): RootsMagicJobResult {
  return { schema_version: 1, kind: 'query', result }
}

function bridgeFor(results: readonly RootsMagicJobResult[] = [
  inspection,
  queryResult(people),
  queryResult(secondPeople),
  queryResult(familyLinks),
  queryResult(events),
  { schema_version: 1, kind: 'export', result: {
    schema_version: 1, artifact_id: 'art_fixture_export_0001', display_name: 'fictional-family export',
    source_ref: sourceRef, source_fingerprint: fingerprint, profile_code: 'portable', gedcom_version: '5.5.5',
  } },
]): AncestryBridge {
  const getRootsMagicJobResult = vi.fn()
  for (const result of results) getRootsMagicJobResult.mockResolvedValueOnce(success(result))
  return {
    requestOpenFileGrant: vi.fn().mockResolvedValue(success(grant)),
    revokeFileGrant: vi.fn().mockResolvedValue(success({ revoked: true })),
    inspectRootsMagicSource: vi.fn().mockResolvedValue(success(job)),
    getRootsMagicPresets: vi.fn().mockResolvedValue(success(definitions)),
    queryRootsMagic: vi.fn().mockResolvedValue(success(job)),
    requestRootsMagicOutput: vi.fn().mockResolvedValue(success({
      schema_version: 1, output_capability: 'd'.repeat(64), display_name: 'fictional-family export',
    })),
    exportRootsMagic: vi.fn().mockResolvedValue(success(job)),
    getRootsMagicJobResult,
    discardRootsMagicSource: vi.fn().mockResolvedValue(success({ schema_version: 1 })),
    revealRootsMagicArtifact: vi.fn().mockResolvedValue(success({ schema_version: 1, revealed: true })),
    getJob: vi.fn().mockResolvedValue(success(job)),
  } as unknown as AncestryBridge
}

describe('RootsMagic workspace', () => {
  it('uses opaque grants and source references for literal-filtered preset pages and explicit export', async () => {
    const bridge = bridgeFor()
    render(<RootsMagicWorkspace bridge={bridge} />)

    await userEvent.click(screen.getByRole('button', { name: 'Choose RootsMagic source' }))
    expect(await screen.findByText('Fictional Family')).toBeVisible()
    expect(screen.getByText(`Fingerprint: ${fingerprint}`)).toBeVisible()
    expect(screen.getByText('Version: unknown')).toBeVisible()
    expect(screen.getByText('Source access: active')).toBeVisible()
    expect(screen.getByText('This source is read-only and is never modified.')).toBeVisible()
    expect(bridge.inspectRootsMagicSource).toHaveBeenCalledWith(grant.grantId)
    expect(bridge.revokeFileGrant).toHaveBeenCalledWith(grant.grantId)

    const literalFilter = "%' OR 1=1 --"
    await userEvent.type(screen.getByRole('searchbox', { name: 'Find people by name' }), literalFilter)
    await userEvent.click(screen.getByRole('button', { name: 'People' }))
    expect(await screen.findByText('Alex Example')).toBeVisible()
    expect(bridge.queryRootsMagic).toHaveBeenLastCalledWith({ schema_version: 1, source_ref: sourceRef,
      query_id: 'people', person_id: null, name_filter: literalFilter, offset: 0, page_size: 25 })
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('Morgan Example')).toBeVisible()
    expect(bridge.queryRootsMagic).toHaveBeenLastCalledWith({ schema_version: 1, source_ref: sourceRef,
      query_id: 'people', person_id: null, name_filter: literalFilter, offset: 2, page_size: 25 })

    await userEvent.click(screen.getByRole('button', { name: 'Select Morgan Example' }))
    expect(screen.getByRole('status')).toHaveTextContent('Selected root: Morgan Example')
    expect(screen.getByRole('radio', { name: 'Exclude living people' })).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Family links' }))
    expect(await screen.findByText('sibling')).toBeVisible()
    expect(bridge.queryRootsMagic).toHaveBeenLastCalledWith({ schema_version: 1, source_ref: sourceRef,
      query_id: 'family_links', person_id: 103, name_filter: '', offset: 0, page_size: 25 })
    await userEvent.click(screen.getByRole('button', { name: 'Events' }))
    expect(await screen.findByText('Fictional Town')).toBeVisible()
    expect(bridge.queryRootsMagic).toHaveBeenLastCalledWith({ schema_version: 1, source_ref: sourceRef,
      query_id: 'events', person_id: 103, name_filter: '', offset: 0, page_size: 25 })
    await userEvent.click(screen.getByRole('radio', { name: 'Descendants' }))
    const generations = screen.getByRole('spinbutton', { name: 'Generations' })
    await userEvent.clear(generations)
    await userEvent.type(generations, '4')
    await userEvent.click(screen.getByRole('button', { name: 'Choose new export folder' }))
    expect(bridge.requestRootsMagicOutput).toHaveBeenCalledWith('Fictional Family export')
    expect(screen.getByRole('status')).toHaveTextContent(
      'New export folder selected: fictional-family export. Confirm the export scope to continue.',
    )
    expect(screen.getByText('New export folder: fictional-family export')).toBeVisible()
    await userEvent.click(screen.getByRole('checkbox', { name: /I confirm this export is limited to descendants/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Export portable GEDCOM' }))
    await waitFor(() => expect(bridge.exportRootsMagic).toHaveBeenCalledWith({ schema_version: 1,
      source_ref: sourceRef, output_capability: 'd'.repeat(64), root_person_id: 103, scope: 'descendants',
      generations: 4, living: 'exclude' }))
    expect(await screen.findByRole('button', { name: 'Reveal export folder' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Reveal export folder' }))
    await waitFor(() => expect(bridge.revealRootsMagicArtifact).toHaveBeenCalledWith({
      schema_version: 1,
      artifact_id: 'art_fixture_export_0001',
    }))
    expect(screen.getByRole('status')).toHaveTextContent('The export folder was revealed in your system file browser.')
    expect(screen.getByRole('checkbox', { name: /I confirm this export is limited to descendants/i })).not.toBeChecked()
  })

  it('shows an unsupported decimal person identifier without allowing it to become an export root', async () => {
    const bridge = bridgeFor([inspection, queryResult(unsupportedPerson)])
    render(<RootsMagicWorkspace bridge={bridge} />)

    await userEvent.click(screen.getByRole('button', { name: 'Choose RootsMagic source' }))
    await userEvent.click(await screen.findByRole('button', { name: 'People' }))

    expect(await screen.findByText('Older Fictional Person')).toBeVisible()
    expect(screen.getByText('This person ID is outside the desktop safe-integer range and cannot be selected for export.')).toBeVisible()
    expect(screen.queryByRole('button', { name: /Select Older Fictional Person/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose new export folder' })).toBeDisabled()
  })

  it('treats a dismissed export-folder picker as a neutral cancellation', async () => {
    const bridge = bridgeFor([inspection, queryResult(people)])
    vi.mocked(bridge.requestRootsMagicOutput).mockResolvedValueOnce(failure('REQUEST_CANCELLED'))
    render(<RootsMagicWorkspace bridge={bridge} />)

    await userEvent.click(screen.getByRole('button', { name: 'Choose RootsMagic source' }))
    await userEvent.click(await screen.findByRole('button', { name: 'People' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Select Alex Example' }))
    await userEvent.click(screen.getByRole('button', { name: 'Choose new export folder' }))

    expect(await screen.findByRole('status')).toHaveTextContent('No new export folder was chosen.')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose new export folder' })).toBeEnabled()
  })

  it('clears a stale query pending state when the active source is replaced', async () => {
    const bridge = bridgeFor([inspection, inspection])
    vi.mocked(bridge.queryRootsMagic).mockReturnValueOnce(new Promise<BridgeResult<JobSnapshot>>(() => undefined))
    render(<RootsMagicWorkspace bridge={bridge} />)

    await userEvent.click(screen.getByRole('button', { name: 'Choose RootsMagic source' }))
    await userEvent.click(await screen.findByRole('button', { name: 'People' }))
    expect(await screen.findByText('Loading the requested page…')).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Choose another RootsMagic source' }))
    await waitFor(() => expect(bridge.inspectRootsMagicSource).toHaveBeenCalledTimes(2))

    expect(screen.getByRole('button', { name: 'People' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Search people' })).toBeEnabled()
  })

  it('does not carry a completed output-folder selection to a replacement source', async () => {
    const bridge = bridgeFor([inspection, queryResult(people), inspection])
    let resolveOutput!: (value: BridgeResult<RootsMagicOutputSelection>) => void
    const outputChoice = new Promise<BridgeResult<RootsMagicOutputSelection>>((resolve) => { resolveOutput = resolve })
    vi.mocked(bridge.requestRootsMagicOutput).mockReturnValueOnce(outputChoice)
    render(<RootsMagicWorkspace bridge={bridge} />)

    await userEvent.click(screen.getByRole('button', { name: 'Choose RootsMagic source' }))
    await userEvent.click(await screen.findByRole('button', { name: 'People' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Select Alex Example' }))
    await userEvent.click(screen.getByRole('button', { name: 'Choose new export folder' }))
    expect(screen.getByRole('button', { name: 'Choosing export folder…' })).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Choose another RootsMagic source' }))
    await waitFor(() => expect(bridge.inspectRootsMagicSource).toHaveBeenCalledTimes(2))
    resolveOutput(success({ schema_version: 1, output_capability: 'd'.repeat(64), display_name: 'stale export folder' }))
    await Promise.resolve()
    await Promise.resolve()

    expect(screen.queryByText('New export folder: stale export folder')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose new export folder' })).toBeDisabled()
  })

  it('does not carry a completed export receipt to a replacement source', async () => {
    const exportReceipt: RootsMagicExportReceipt = {
      schema_version: 1, artifact_id: 'art_fixture_export_0001', display_name: 'stale export folder',
      source_ref: sourceRef, source_fingerprint: fingerprint, profile_code: 'portable', gedcom_version: '5.5.5',
    }
    const bridge = bridgeFor([inspection, queryResult(people), inspection, { schema_version: 1, kind: 'export', result: exportReceipt }])
    let resolveExport!: (value: BridgeResult<JobSnapshot>) => void
    const exportSubmission = new Promise<BridgeResult<JobSnapshot>>((resolve) => { resolveExport = resolve })
    vi.mocked(bridge.exportRootsMagic).mockReturnValueOnce(exportSubmission)
    render(<RootsMagicWorkspace bridge={bridge} />)

    await userEvent.click(screen.getByRole('button', { name: 'Choose RootsMagic source' }))
    await userEvent.click(await screen.findByRole('button', { name: 'People' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Select Alex Example' }))
    await userEvent.click(screen.getByRole('button', { name: 'Choose new export folder' }))
    await userEvent.click(screen.getByRole('checkbox', { name: /I confirm this export is limited to connected people/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Export portable GEDCOM' }))
    expect(screen.getByRole('button', { name: 'Exporting…' })).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Choose another RootsMagic source' }))
    await waitFor(() => expect(bridge.inspectRootsMagicSource).toHaveBeenCalledTimes(2))
    resolveExport(success(job))
    await Promise.resolve()
    await Promise.resolve()

    expect(screen.queryByRole('region', { name: 'Export receipt' })).not.toBeInTheDocument()
    expect(bridge.getRootsMagicJobResult).toHaveBeenCalledTimes(3)
  })

  it('discards the active source while a query is pending and keeps the stale result hidden', async () => {
    const bridge = bridgeFor([inspection])
    vi.mocked(bridge.queryRootsMagic).mockReturnValueOnce(new Promise<BridgeResult<JobSnapshot>>(() => undefined))
    render(<RootsMagicWorkspace bridge={bridge} />)

    await userEvent.click(screen.getByRole('button', { name: 'Choose RootsMagic source' }))
    await userEvent.click(await screen.findByRole('button', { name: 'People' }))
    expect(await screen.findByText('Loading the requested page…')).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Discard active source' }))

    await waitFor(() => expect(bridge.discardRootsMagicSource).toHaveBeenCalledWith({
      schema_version: 1,
      source_ref: sourceRef,
    }))
    expect(screen.queryByRole('heading', { name: 'Active source' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'People' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose RootsMagic source' })).toBeEnabled()
    expect(screen.getByRole('status')).toHaveTextContent('The RootsMagic source was discarded.')
  })

  it('focuses a stable coded error without rendering bridge details', async () => {
    const bridge = bridgeFor()
    vi.mocked(bridge.inspectRootsMagicSource).mockResolvedValueOnce(failure('SIDECAR_UNAVAILABLE'))
    render(<RootsMagicWorkspace bridge={bridge} />)

    await userEvent.click(screen.getByRole('button', { name: 'Choose RootsMagic source' }))

    const error = await screen.findByRole('alert')
    expect(error).toHaveFocus()
    expect(error).toHaveTextContent('Code: SIDECAR_UNAVAILABLE')
    expect(error).not.toHaveTextContent('Internal bridge detail is not rendered.')
  })

  it('discards an inspected source when preset definitions are unavailable', async () => {
    const bridge = bridgeFor([inspection])
    vi.mocked(bridge.getRootsMagicPresets).mockResolvedValueOnce(failure('SIDECAR_UNAVAILABLE'))
    render(<RootsMagicWorkspace bridge={bridge} />)

    await userEvent.click(screen.getByRole('button', { name: 'Choose RootsMagic source' }))

    await screen.findByRole('alert')
    await waitFor(() => expect(bridge.discardRootsMagicSource).toHaveBeenCalledWith({
      schema_version: 1,
      source_ref: sourceRef,
    }))
    expect(bridge.revokeFileGrant).toHaveBeenCalledWith(grant.grantId)
    expect(screen.queryByRole('heading', { name: 'Active source' })).not.toBeInTheDocument()
  })
})
