/** Exercises native capability ownership and revocation at asynchronous handoff boundaries. */
import { mkdtemp, open, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FileGrantId, JobSnapshot } from '../shared-contract/desktop'
import type { RootsMagicJobResult, RootsMagicQueryRequest } from '../shared-contract/rootsmagic'
import { FileGrantBroker } from './file-grant-broker'
import { RootsMagicWorkbenchBroker } from './rootsmagic-workbench-broker'

const directories: string[] = []
const grant: FileGrantId = `grt_${'a'.repeat(64)}`
const sourceRef = 'b'.repeat(64)
const inspection: RootsMagicJobResult = { schema_version: 1, kind: 'inspection', result: {
  schema_version: 1, source_ref: sourceRef, friendly_name: 'Fictional.rmtree', fingerprint: 'c'.repeat(64),
  detected_version: 'unknown', grant_status_code: 'active', immutable: true,
} }
const queryRequest: RootsMagicQueryRequest = { schema_version: 1, source_ref: sourceRef,
  query_id: 'people', person_id: null, name_filter: '', offset: 0, page_size: 25 }
const queryResult: RootsMagicJobResult = { schema_version: 1, kind: 'query', result: { schema_version: 1,
  query_id: 'people', columns: ['person_id'], rows: [], offset: 0, returned_rows: 0,
  total_rows: 0, has_more: false, next_offset: null,
} }
const exportResult: RootsMagicJobResult = { schema_version: 1, kind: 'export', result: {
  schema_version: 1, artifact_id: 'art_1234567890abcdef1234567890abcdef', display_name: 'Chosen export',
  source_ref: sourceRef, source_fingerprint: 'd'.repeat(64), profile_code: 'portable', gedcom_version: '5.5.5',
} }
const snapshot = (id: number): JobSnapshot => ({ job_id: `j${String(id).padStart(6, '0')}` }) as JobSnapshot
const terminalSnapshot = (job: JobSnapshot, state: 'failed' | 'cancelled' | 'completed'): JobSnapshot =>
  ({ ...job, state }) as JobSnapshot
const request = (job: JobSnapshot) => ({ schema_version: 1 as const, job_id: job.job_id })

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((finish) => { resolve = finish })
  return { promise, resolve }
}

async function fixture() {
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'ancestry-rootsmagic-broker-')))
  directories.push(directory)
  const path = join(directory, 'Fictional.rmtree')
  await writeFile(path, 'fictional immutable source bytes')
  const resolved = { grantId: grant, purpose: 'rootsmagic-read' as const, access: 'read' as const, path, maxBytes: 1024 }
  const files = { resolveReadGrant: vi.fn(async () => resolved), revokeGrant: vi.fn() }
  let jobId = 0
  const client = {
    inspect: vi.fn(async () => snapshot(++jobId)), presets: vi.fn(),
    query: vi.fn(async () => snapshot(++jobId)), export: vi.fn(async () => snapshot(++jobId)),
    result: vi.fn(async (): Promise<RootsMagicJobResult> => inspection),
    discard: vi.fn(async () => ({ schema_version: 1 as const })), cancel: vi.fn(async () => snapshot(99)),
  }
  const native = { selectNewOutputDirectory: vi.fn(async () => join(directory, 'Chosen export')),
    reveal: vi.fn(async () => undefined) }
  const broker = new RootsMagicWorkbenchBroker({ directory, files: files as unknown as FileGrantBroker, client, native })
  const owner = {}
  async function inspect() {
    const job = await broker.inspect(owner, grant)
    await broker.result(owner, request(job))
    return job
  }
  return { broker, client, files, native, owner, directory, path, inspect }
}

afterEach(async () => { await Promise.all(directories.splice(0).map((path) => rm(path, { recursive: true, force: true }))) })

describe('native RootsMagic workbench broker', () => {
  it('keeps a source and its query authority retryable when sidecar disposal fails', async () => {
    const { broker, client, owner, inspect } = await fixture()
    await inspect()
    client.discard.mockRejectedValueOnce(new Error('sidecar temporarily unavailable'))
    await expect(broker.discard(owner, { schema_version: 1, source_ref: sourceRef })).rejects.toThrow()
    await expect(broker.query(owner, queryRequest)).resolves.toHaveProperty('job_id')
    await expect(broker.discard(owner, { schema_version: 1, source_ref: sourceRef })).resolves.toEqual({ schema_version: 1 })
    await expect(broker.query(owner, queryRequest)).rejects.toThrow('FILE_GRANT_FORBIDDEN')
  })

  it('passes an opaque manifest capability, preserves source bytes, and reports the actual selected folder name', async () => {
    const { broker, client, owner, directory, path } = await fixture()
    const before = await readFile(path)
    client.inspect.mockImplementationOnce(async (...args: unknown[]) => {
      const capability = args[0] as string
      expect(capability).toMatch(/^[a-f0-9]{64}$/)
      const manifest = JSON.parse(await readFile(join(directory, `${capability}.rootsmagic-source.json`), 'utf8'))
      expect(manifest).toMatchObject({ schema_version: 1, path, friendly_name: 'Fictional.rmtree', size_bytes: before.length })
      return snapshot(1)
    })
    await broker.inspect(owner, grant)
    expect(await readdir(directory)).toEqual(['Fictional.rmtree'])
    expect(await readFile(path)).toEqual(before)
    expect(await broker.selectOutput(owner, 'Suggested')).toMatchObject({ display_name: 'Chosen export' })
  })

  it('rejects another window before reading a private result or submitting a query', async () => {
    const { broker, client, inspect } = await fixture()
    const job = await inspect()
    client.result.mockClear()
    await expect(broker.result({}, request(job))).rejects.toThrow('FILE_GRANT_FORBIDDEN')
    await expect(broker.query({}, queryRequest)).rejects.toThrow('FILE_GRANT_FORBIDDEN')
    expect(client.result).not.toHaveBeenCalled()
    expect(client.query).not.toHaveBeenCalled()
  })

  it('consumes the native picker grant once and retains the detached source after picker revocation', async () => {
    const { client, native, directory, path, owner } = await fixture()
    await writeFile(path, Buffer.concat([Buffer.from('SQLite format 3\0', 'ascii'), Buffer.alloc(128)]))
    const files = new FileGrantBroker({ selectOpenFile: async () => path,
      selectSaveFile: async () => null, confirmReplacement: async () => false })
    const selected = await files.requestOpenGrant(owner, { purpose: 'rootsmagic-read' })
    const broker = new RootsMagicWorkbenchBroker({ directory, files, client, native })
    const job = await broker.inspect(owner, selected!.grantId)
    await expect(broker.inspect(owner, selected!.grantId)).rejects.toThrow('FILE_GRANT_REVOKED')
    broker.revokeGrant(owner, selected!.grantId)
    files.revokeGrant(owner, selected!.grantId)
    expect(await broker.result(owner, request(job))).toEqual(inspection)
    await expect(broker.query(owner, queryRequest)).resolves.toHaveProperty('job_id')
  })

  it('cancels a late inspection submission after its window closes', async () => {
    const { broker, client, owner } = await fixture()
    const late = deferred<JobSnapshot>()
    client.inspect.mockReturnValueOnce(late.promise)
    const pending = broker.inspect(owner, grant)
    const rejected = expect(pending).rejects.toThrow('FILE_OPERATION_CANCELLED')
    await vi.waitFor(() => expect(client.inspect).toHaveBeenCalled())
    await broker.revokeOwner(owner)
    late.resolve(snapshot(7))
    await rejected
    expect(client.cancel).toHaveBeenCalledWith('j000007')
    await expect(broker.result(owner, request(snapshot(7)))).rejects.toThrow()
  })

  it('cancels a late query submission after all session grants expire', async () => {
    const { broker, client, owner, inspect } = await fixture()
    await inspect()
    const late = deferred<JobSnapshot>()
    client.query.mockReturnValueOnce(late.promise)
    const pending = broker.query(owner, queryRequest)
    const rejected = expect(pending).rejects.toThrow('FILE_OPERATION_CANCELLED')
    await vi.waitFor(() => expect(client.query).toHaveBeenCalled())
    await broker.revokeAll()
    late.resolve(snapshot(8))
    await rejected
    expect(client.cancel).toHaveBeenCalledWith('j000008')
    expect(client.discard).toHaveBeenCalledWith(sourceRef)
  })

  it('discards an inspected source when its result arrives after window revocation', async () => {
    const { broker, client, owner } = await fixture()
    const job = await broker.inspect(owner, grant)
    const late = deferred<RootsMagicJobResult>()
    client.result.mockReturnValueOnce(late.promise)
    const pending = broker.result(owner, request(job))
    const rejected = expect(pending).rejects.toThrow('FILE_OPERATION_CANCELLED')
    await broker.revokeOwner(owner)
    late.resolve(inspection)
    await rejected
    expect(client.discard).toHaveBeenCalledWith(sourceRef)
    await expect(broker.query(owner, queryRequest)).rejects.toThrow('FILE_GRANT_FORBIDDEN')
  })

  it('discards an inspection that completed before its window was revoked', async () => {
    const { broker, client, owner } = await fixture()
    const job = await broker.inspect(owner, grant)
    client.cancel.mockResolvedValueOnce(terminalSnapshot(job, 'completed'))
    await broker.revokeOwner(owner)
    expect(client.result).toHaveBeenCalledWith(job.job_id)
    expect(client.discard).toHaveBeenCalledWith(sourceRef)
  })

  it('stops reading source bytes when inspection is cancelled during hashing', async () => {
    const { broker, client, files, owner, path } = await fixture()
    await writeFile(path, Buffer.alloc(3 * 1024 * 1024))
    files.resolveReadGrant.mockResolvedValueOnce({ grantId: grant, purpose: 'rootsmagic-read',
      access: 'read', path, maxBytes: 8 * 1024 * 1024 * 1024 })
    const controller = new AbortController()
    const handle = await open(path, 'r')
    const prototype = Object.getPrototypeOf(handle) as { read: typeof handle.read }
    const originalRead = prototype.read
    await handle.close()
    let reads = 0
    const read = vi.spyOn(prototype, 'read').mockImplementation(async function (this: typeof handle, ...args) {
      const result = await originalRead.apply(this, args)
      reads += 1
      controller.abort()
      return result
    })
    try {
      await expect(broker.inspect(owner, grant, controller.signal)).rejects.toThrow('FILE_OPERATION_CANCELLED')
      expect(reads).toBe(1)
      expect(client.inspect).not.toHaveBeenCalled()
    } finally {
      read.mockRestore()
    }
  })

  it('cannot recreate source authority by replaying an inspection result after discard', async () => {
    const { broker, client, owner, inspect } = await fixture()
    const job = await inspect()
    await broker.discard(owner, { schema_version: 1, source_ref: sourceRef })
    client.result.mockClear()
    await expect(broker.result(owner, request(job))).rejects.toThrow('FILE_GRANT_FORBIDDEN')
    await expect(broker.query(owner, queryRequest)).rejects.toThrow('FILE_GRANT_FORBIDDEN')
    expect(client.result).not.toHaveBeenCalled()
  })

  it('rejects a query result that arrives after source disposal', async () => {
    const { broker, client, owner, inspect } = await fixture()
    await inspect()
    const job = await broker.query(owner, queryRequest)
    const late = deferred<RootsMagicJobResult>()
    client.result.mockReturnValueOnce(late.promise)
    const pending = broker.result(owner, request(job))
    const rejected = expect(pending).rejects.toThrow('FILE_OPERATION_CANCELLED')
    await broker.discard(owner, { schema_version: 1, source_ref: sourceRef })
    late.resolve(queryResult)
    await rejected
  })

  it('rejects a folder selection returned after its window was revoked', async () => {
    const { broker, native, owner, directory } = await fixture()
    const late = deferred<string>()
    native.selectNewOutputDirectory.mockReturnValueOnce(late.promise)
    const pending = broker.selectOutput(owner, 'Export')
    const rejected = expect(pending).rejects.toThrow('FILE_OPERATION_CANCELLED')
    await broker.revokeAll()
    late.resolve(join(directory, 'Late export'))
    await rejected
  })

  it('cancels a late export submission after its owner is revoked', async () => {
    const { broker, client, owner, inspect } = await fixture()
    await inspect()
    const output = await broker.selectOutput(owner, 'Export')
    const late = deferred<JobSnapshot>()
    client.export.mockReturnValueOnce(late.promise)
    const pending = broker.export(owner, { schema_version: 1, source_ref: sourceRef,
      output_capability: output!.output_capability, root_person_id: 1, scope: 'connected', generations: null, living: 'exclude' })
    const rejected = expect(pending).rejects.toThrow('FILE_OPERATION_CANCELLED')
    await vi.waitFor(() => expect(client.export).toHaveBeenCalled())
    await broker.revokeOwner(owner)
    late.resolve(snapshot(9))
    await rejected
    expect(client.cancel).toHaveBeenCalledWith('j000009')
  })

  it('reserves a directory grant before awaiting export submission', async () => {
    const { broker, client, owner, inspect } = await fixture()
    await inspect()
    const output = await broker.selectOutput(owner, 'Export')
    const exportRequest = { schema_version: 1 as const, source_ref: sourceRef,
      output_capability: output!.output_capability, root_person_id: 1, scope: 'connected' as const,
      generations: null, living: 'exclude' as const }
    const late = deferred<JobSnapshot>()
    client.export.mockReturnValueOnce(late.promise)
    const pending = broker.export(owner, exportRequest)
    await vi.waitFor(() => expect(client.export).toHaveBeenCalled())
    const duplicate = broker.export(owner, exportRequest)
    await expect(duplicate).rejects.toThrow('FILE_GRANT_FORBIDDEN')
    late.resolve(snapshot(10))
    await pending
    expect(client.export).toHaveBeenCalledTimes(1)
  })

  it('does not exhaust the workbench after sixty-four completed pages', async () => {
    const { broker, client, owner, inspect } = await fixture()
    await inspect()
    client.result.mockResolvedValue(queryResult)
    for (let page = 0; page < 70; page++) {
      const job = await broker.query(owner, { ...queryRequest, offset: page * 25 })
      expect(await broker.result(owner, request(job))).toEqual(queryResult)
    }
  })

  it('counts submitted inspections awaiting a result against the source bound', async () => {
    const { broker, client, owner } = await fixture()
    for (let index = 0; index < 8; index++) await broker.inspect(owner, grant)
    await expect(broker.inspect(owner, grant)).rejects.toThrow()
    expect(client.inspect).toHaveBeenCalledTimes(8)
  })

  it('releases failed and cancelled inspections while retaining live inspection capacity', async () => {
    const { broker, client, owner } = await fixture()
    const jobs = await Promise.all(Array.from({ length: 8 }, () => broker.inspect(owner, grant)))
    await expect(broker.inspect(owner, grant)).rejects.toThrow('FILE_SELECTION_INVALID')

    broker.observeJob(terminalSnapshot(jobs[0]!, 'failed'))
    broker.observeJob(terminalSnapshot(jobs[1]!, 'cancelled'))
    await expect(broker.inspect(owner, grant)).resolves.toHaveProperty('job_id')
    await expect(broker.inspect(owner, grant)).resolves.toHaveProperty('job_id')
    await expect(broker.inspect(owner, grant)).rejects.toThrow('FILE_SELECTION_INVALID')
    expect(client.inspect).toHaveBeenCalledTimes(10)
  })

  it('releases failed export output authority and retains completed artifact reveal authority', async () => {
    const { broker, native, owner, directory, inspect } = await fixture()
    await inspect()
    const firstOutput = await broker.selectOutput(owner, 'Export')
    const firstJob = await broker.export(owner, { schema_version: 1, source_ref: sourceRef,
      output_capability: firstOutput!.output_capability, root_person_id: 1, scope: 'connected',
      generations: null, living: 'exclude' })
    broker.observeJob(terminalSnapshot(firstJob, 'failed'))

    for (let index = 0; index < 16; index++) {
      native.selectNewOutputDirectory.mockResolvedValueOnce(join(directory, `Available ${index}`))
    }
    await expect(Promise.all(Array.from({ length: 16 }, () => broker.selectOutput(owner, 'Export')))).resolves.toHaveLength(16)

    const completed = await fixture()
    await completed.inspect()
    const output = await completed.broker.selectOutput(completed.owner, 'Export')
    const job = await completed.broker.export(completed.owner, { schema_version: 1, source_ref: sourceRef,
      output_capability: output!.output_capability, root_person_id: 1, scope: 'connected',
      generations: null, living: 'exclude' })
    completed.client.result.mockResolvedValueOnce(exportResult)
    const result = await completed.broker.result(completed.owner, request(job))
    completed.broker.observeJob(terminalSnapshot(job, 'completed'))
    await expect(completed.broker.reveal(completed.owner, { schema_version: 1,
      artifact_id: result.kind === 'export' ? result.result.artifact_id : '' })).resolves.toEqual({ schema_version: 1 })
    expect(completed.native.reveal).toHaveBeenCalledWith(join(completed.directory, 'Chosen export'))
  })

  it('releases dependent export output authority when its source is discarded', async () => {
    const { broker, client, native, owner, directory, inspect } = await fixture()
    await inspect()
    native.selectNewOutputDirectory.mockResolvedValueOnce(join(directory, 'Export 0'))
    const output = await broker.selectOutput(owner, 'Export')
    await broker.export(owner, { schema_version: 1, source_ref: sourceRef,
      output_capability: output!.output_capability, root_person_id: 1, scope: 'connected',
      generations: null, living: 'exclude' })
    for (let index = 1; index < 16; index++) {
      native.selectNewOutputDirectory.mockResolvedValueOnce(join(directory, `Export ${index}`))
      await broker.selectOutput({}, 'Export')
    }
    await expect(broker.selectOutput(owner, 'Full')).rejects.toThrow('FILE_SELECTION_INVALID')
    await broker.discard(owner, { schema_version: 1, source_ref: sourceRef })
    native.selectNewOutputDirectory.mockResolvedValueOnce(join(directory, 'Replacement'))
    await expect(broker.selectOutput(owner, 'Replacement')).resolves.toHaveProperty('display_name', 'Replacement')
    expect(client.discard).toHaveBeenCalledWith(sourceRef)
  })

  it('reserves all concurrent output selections before awaiting the native picker', async () => {
    const { broker, native, owner, directory } = await fixture()
    const selections = Array.from({ length: 16 }, () => deferred<string>())
    let index = 0
    native.selectNewOutputDirectory.mockImplementation(async () => selections[index++]!.promise)
    const pending = selections.map((_selection, selected) => broker.selectOutput(owner, `Export ${selected}`))
    await vi.waitFor(() => expect(native.selectNewOutputDirectory).toHaveBeenCalledTimes(16))
    await expect(broker.selectOutput(owner, 'Seventeenth')).rejects.toThrow('FILE_SELECTION_INVALID')
    selections.forEach((selection, selected) => selection.resolve(join(directory, `Export ${selected}`)))
    await expect(Promise.all(pending)).resolves.toHaveLength(16)
  })

  it('replaces unused destination authority as the owner chooses new folders', async () => {
    const { broker, native, owner, directory, inspect } = await fixture()
    await inspect()
    let prior: string | undefined
    for (let index = 0; index < 18; index++) {
      native.selectNewOutputDirectory.mockResolvedValueOnce(join(directory, `Choice ${index}`))
      const selected = await broker.selectOutput(owner, 'Export')
      expect(selected?.display_name).toBe(`Choice ${index}`)
      if (prior) {
        await expect(broker.export(owner, { schema_version: 1, source_ref: sourceRef,
          output_capability: prior, root_person_id: 1, scope: 'connected',
          generations: null, living: 'exclude' })).rejects.toThrow('FILE_GRANT_FORBIDDEN')
      }
      prior = selected!.output_capability
    }
    await expect(broker.export(owner, { schema_version: 1, source_ref: sourceRef,
      output_capability: prior!, root_person_id: 1, scope: 'connected',
      generations: null, living: 'exclude' })).resolves.toHaveProperty('job_id')
  })

  it('releases an output capability when export submission fails', async () => {
    const { broker, client, native, owner, directory, inspect } = await fixture()
    await inspect()
    const output = await broker.selectOutput(owner, 'Export')
    client.export.mockRejectedValueOnce(new Error('submission failed'))
    await expect(broker.export(owner, { schema_version: 1, source_ref: sourceRef,
      output_capability: output!.output_capability, root_person_id: 1, scope: 'connected',
      generations: null, living: 'exclude' })).rejects.toThrow('submission failed')
    for (let index = 0; index < 16; index++) {
      native.selectNewOutputDirectory.mockResolvedValueOnce(join(directory, `Retry ${index}`))
    }
    await expect(Promise.all(Array.from({ length: 16 }, () => broker.selectOutput(owner, 'Retry')))).resolves.toHaveLength(16)
  })
})
