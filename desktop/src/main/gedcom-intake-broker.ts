/** Owns native GEDCOM staging and private results for one authorized renderer generation. */
import { randomBytes } from 'node:crypto'
import { unlink } from 'node:fs/promises'
import { join } from 'node:path'
import type { FileGrantId, JobRequest, JobSnapshot } from '../shared-contract/desktop'
import type { GedcomDiscard, GedcomInspection, GedcomRootPage, GedcomRootQuery } from '../shared-contract/gedcom'
import { FileGrantBrokerError, type FileGrantBroker } from './file-grant-broker'

/** Native-only metadata identifying a private staged copy, not a selected host path. */
export interface GedcomStageRequest { schema_version: 1; stage_id: string; size_bytes: number; sha256: string }

/** Narrow authenticated native intake port; no provider, output or generic route access. */
export interface GedcomIntakeClient {
  submit(request: GedcomStageRequest): Promise<Readonly<JobSnapshot>>
  result(jobId: string, signal?: AbortSignal): Promise<Readonly<GedcomInspection>>
  roots(request: GedcomRootQuery, signal?: AbortSignal): Promise<Readonly<GedcomRootPage>>
  discard(jobId: string): Promise<Readonly<GedcomDiscard>>
}

interface Entry {
  owner: object
  path: string
  controller: AbortController
  revoked: boolean
  disposed: boolean
  jobId?: string
  pending?: Promise<Readonly<JobSnapshot>>
  releasing?: Promise<void>
}

/** Limits intake to eight owner-bound read-only inspections, including pending submissions. */
export class GedcomIntakeBroker {
  private readonly entries = new Set<Entry>()

  constructor(private readonly options: Readonly<{
    directory: string
    files: Pick<FileGrantBroker, 'stageReadGrant'>
    client: Readonly<GedcomIntakeClient>
  }>) {}

  /** Consumes a native read grant and binds its inspection to the originating renderer. */
  async inspect(owner: object, grantId: FileGrantId, signal?: AbortSignal): Promise<Readonly<JobSnapshot>> {
    if (signal?.aborted) throw new FileGrantBrokerError('FILE_OPERATION_CANCELLED')
    if (this.entries.size >= 8) throw new FileGrantBrokerError('FILE_GRANT_CONFLICT')
    const stageId = randomBytes(32).toString('hex')
    const entry: Entry = { owner, path: join(this.options.directory, `${stageId}.ged`),
      controller: new AbortController(), revoked: false, disposed: false }
    const abort = (): void => { entry.revoked = true; entry.controller.abort() }
    signal?.addEventListener('abort', abort, { once: true })
    this.entries.add(entry)
    entry.pending = (async () => {
      try {
        const staged = await this.options.files.stageReadGrant(owner, grantId, 'gedcom-read', entry.path, entry.controller.signal)
        this.requireActive(entry)
        // Keep this bounded HTTP submission alive on navigation so a late job can be discarded.
        const job = await this.options.client.submit({ schema_version: 1, stage_id: stageId,
          size_bytes: staged.sizeBytes, sha256: staged.sha256 })
        entry.jobId = job.job_id
        this.requireActive(entry)
        return job
      } catch (error) {
        await this.release(entry)
        throw error
      } finally {
        signal?.removeEventListener('abort', abort)
      }
    })()
    return entry.pending
  }

  /** Reads metadata only from an inspection retained by this owner. */
  async result(owner: object, request: JobRequest, signal?: AbortSignal): Promise<Readonly<GedcomInspection>> {
    const entry = this.owned(owner, request.job_id)
    const result = await this.options.client.result(request.job_id, signal)
    this.requireActive(entry)
    return result
  }

  /** Searches only the explicitly owned source; query text is never retained by this broker. */
  async roots(owner: object, request: GedcomRootQuery, signal?: AbortSignal): Promise<Readonly<GedcomRootPage>> {
    const entry = this.owned(owner, request.job_id)
    const result = await this.options.client.roots(request, signal)
    this.requireActive(entry)
    return result
  }

  /** Revokes one source and releases its parser results and remaining private staged bytes. */
  async discard(owner: object, request: JobRequest): Promise<Readonly<GedcomDiscard>> {
    const entry = this.owned(owner, request.job_id)
    entry.revoked = true
    entry.controller.abort()
    await this.release(entry)
    return { schema_version: 1 }
  }

  /** Invalidates all sources immediately, then drains late submissions for one renderer. */
  async revokeOwner(owner: object): Promise<void> {
    const entries = [...this.entries].filter((entry) => entry.owner === owner)
    for (const entry of entries) { entry.revoked = true; entry.controller.abort() }
    await Promise.all(entries.map(async (entry) => {
      await entry.pending?.catch(() => undefined)
      await this.release(entry)
    }))
  }

  /** Clears private source ownership on sidecar restart or application shutdown. */
  async revokeAll(): Promise<void> {
    await Promise.all([...new Set([...this.entries].map((entry) => entry.owner))].map((owner) => this.revokeOwner(owner)))
  }

  private requireActive(entry: Entry): void {
    if (entry.revoked || entry.controller.signal.aborted || !this.entries.has(entry)) {
      throw new FileGrantBrokerError('FILE_OPERATION_CANCELLED')
    }
  }

  private owned(owner: object, jobId: string): Entry {
    const entry = [...this.entries].find((item) => item.owner === owner && item.jobId === jobId && !item.revoked)
    if (!entry) throw new FileGrantBrokerError('FILE_GRANT_FORBIDDEN')
    this.requireActive(entry)
    return entry
  }

  private async release(entry: Entry): Promise<void> {
    if (entry.disposed) return
    if (entry.releasing) return entry.releasing
    entry.revoked = true
    entry.controller.abort()
    entry.releasing = (async () => {
      try {
        if (entry.jobId) await this.options.client.discard(entry.jobId)
      } finally {
        // Unlink only our generated leaf; the sidecar normally removes it on completion.
        await unlink(entry.path).catch((error: NodeJS.ErrnoException) => { if (error.code !== 'ENOENT') throw error })
      }
      // Retain failed cleanup for owner/session revocation to retry. It stays inaccessible.
      entry.disposed = true
      this.entries.delete(entry)
    })().finally(() => { delete entry.releasing })
    return entry.releasing
  }
}
