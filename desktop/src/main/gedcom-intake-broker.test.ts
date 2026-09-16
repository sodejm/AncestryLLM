/** Verifies owner-bound intake and revocation across asynchronous native staging. */
import { mkdtemp, realpath, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FileGrantId, JobSnapshot } from '../shared-contract/desktop'
import { GedcomIntakeBroker } from './gedcom-intake-broker'

const roots: string[] = []
const grant: FileGrantId = `grt_${'a'.repeat(64)}`
const job = { job_id: 'j000001' } as JobSnapshot

async function fixture() {
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'ancestryllm-intake-test-')))
  roots.push(directory)
  const files = { stageReadGrant: vi.fn(async () => ({ grantId: grant, purpose: 'gedcom-read' as const,
    sizeBytes: 90, sha256: 'b'.repeat(64) })) }
  const client = { submit: vi.fn(async () => job), result: vi.fn(), roots: vi.fn(),
    discard: vi.fn(async () => ({ schema_version: 1 as const })) }
  return { directory, files, client, broker: new GedcomIntakeBroker({ directory, files, client }) }
}

afterEach(async () => { await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true }))) })

describe('native GEDCOM intake broker', () => {
  it('aborts staging only for the owner and grant being revoked', async () => {
    const { broker, files, client } = await fixture()
    const owner = {}
    let stagingSignal!: AbortSignal
    files.stageReadGrant.mockImplementationOnce((...args: unknown[]) => new Promise((_resolve, reject) => {
      stagingSignal = args[4] as AbortSignal
      stagingSignal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true })
    }))
    const pending = broker.inspect(owner, grant)
    const rejected = expect(pending).rejects.toThrow('cancelled')
    broker.revokeGrant({}, grant)
    broker.revokeGrant(owner, `grt_${'b'.repeat(64)}`)
    expect(stagingSignal.aborted).toBe(false)
    broker.revokeGrant(owner, grant)
    expect(stagingSignal.aborted).toBe(true)
    await rejected
    expect(client.submit).not.toHaveBeenCalled()
    await expect(broker.inspect(owner, grant)).resolves.toEqual(job)
    broker.revokeGrant(owner, grant)
    await broker.result(owner, { schema_version: 1, job_id: job.job_id })
    expect(client.discard).not.toHaveBeenCalled()
    await broker.revokeAll()
  })

  it('discards a late submission when its pending grant is revoked', async () => {
    const { broker, client } = await fixture()
    let finish!: (value: JobSnapshot) => void
    client.submit.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const owner = {}
    const pending = broker.inspect(owner, grant)
    const rejected = expect(pending).rejects.toThrow('FILE_OPERATION_CANCELLED')
    await vi.waitFor(() => expect(client.submit).toHaveBeenCalled())
    broker.revokeGrant(owner, grant)
    finish(job)
    await rejected
    expect(client.discard).toHaveBeenCalledWith(job.job_id)
  })

  it('stages only a native read grant and never sends its path over HTTP', async () => {
    const { broker, files, client, directory } = await fixture()
    const owner = {}
    expect(await broker.inspect(owner, grant)).toEqual(job)
    expect(files.stageReadGrant.mock.calls[0]).toEqual([owner, grant, 'gedcom-read',
      expect.stringMatching(new RegExp(`^${directory}/[a-f0-9]{64}\\.ged$`)), expect.any(AbortSignal)])
    expect(client.submit).toHaveBeenCalledWith({ schema_version: 1, stage_id: expect.stringMatching(/^[a-f0-9]{64}$/),
      size_bytes: 90, sha256: 'b'.repeat(64) })
    await expect(broker.result({}, { schema_version: 1, job_id: job.job_id })).rejects.toThrow()
    expect(client.result).not.toHaveBeenCalled()
    await broker.discard(owner, { schema_version: 1, job_id: job.job_id })
    expect(client.discard).toHaveBeenCalledWith(job.job_id)
    await expect(broker.result(owner, { schema_version: 1, job_id: job.job_id })).rejects.toThrow()
  })

  it('discards a late submit after its owner has navigated away', async () => {
    const { broker, client } = await fixture()
    let finish!: (value: JobSnapshot) => void
    client.submit.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const owner = {}
    const pending = broker.inspect(owner, grant)
    const rejected = expect(pending).rejects.toThrow()
    await vi.waitFor(() => expect(client.submit).toHaveBeenCalled())
    const revoked = broker.revokeOwner(owner)
    finish(job)
    await Promise.all([rejected, revoked])
    expect(client.discard).toHaveBeenCalledWith(job.job_id)
  })

  it('bounds retained inspections including submissions still in flight', async () => {
    const { broker, client, files } = await fixture()
    client.submit.mockImplementation(async () => ({ ...job, job_id: `j${String(client.submit.mock.calls.length).padStart(6, '0')}` }))
    const owner = {}
    for (let index = 0; index < 8; index++) await broker.inspect(owner, grant)
    await expect(broker.inspect(owner, grant)).rejects.toThrow('FILE_GRANT_CONFLICT')
    expect(files.stageReadGrant).toHaveBeenCalledTimes(8)
    await broker.revokeAll()
    expect(client.discard).toHaveBeenCalledTimes(8)
  })

  it('retains failed cleanup for retry without restoring source access', async () => {
    const { broker, client } = await fixture()
    const owner = {}
    const request = { schema_version: 1 as const, job_id: job.job_id }
    await broker.inspect(owner, grant)
    client.discard.mockRejectedValueOnce(new Error('temporary transport failure'))
    await expect(broker.discard(owner, request)).rejects.toThrow('temporary transport failure')
    await expect(broker.result(owner, request)).rejects.toThrow('FILE_GRANT_FORBIDDEN')
    await broker.revokeOwner(owner)
    expect(client.discard).toHaveBeenCalledTimes(2)
    await broker.revokeAll()
    expect(client.discard).toHaveBeenCalledTimes(2)
  })

  it('does not count revoked retry entries against the eight-source intake bound', async () => {
    const { broker, client } = await fixture()
    const owner = {}
    const request = { schema_version: 1 as const, job_id: job.job_id }
    for (let index = 0; index < 8; index++) {
      await broker.inspect(owner, grant)
      client.discard.mockRejectedValueOnce(new Error('temporary transport failure'))
      await expect(broker.discard(owner, request)).rejects.toThrow('temporary transport failure')
    }
    await expect(broker.inspect(owner, grant)).resolves.toEqual(job)
    await broker.revokeOwner(owner)
  })

  it('coalesces overlapping cleanup requests', async () => {
    const { broker, client } = await fixture()
    const owner = {}
    const request = { schema_version: 1 as const, job_id: job.job_id }
    await broker.inspect(owner, grant)
    let finish!: () => void
    client.discard.mockImplementationOnce(() => new Promise((resolve) => {
      finish = () => resolve({ schema_version: 1 })
    }))
    const discarded = broker.discard(owner, request)
    const revoked = broker.revokeOwner(owner)
    await vi.waitFor(() => expect(client.discard).toHaveBeenCalledTimes(1))
    finish()
    await Promise.all([discarded, revoked])
    expect(client.discard).toHaveBeenCalledTimes(1)
  })
})
