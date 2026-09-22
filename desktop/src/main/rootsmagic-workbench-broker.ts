/** Keeps RootsMagic filesystem authority in Electron Main while the sidecar handles data work. */
import { createHash, randomBytes } from 'node:crypto'
import { constants } from 'node:fs'
import { lstat, open, realpath, unlink, type FileHandle } from 'node:fs/promises'
import { basename, dirname, isAbsolute, join, normalize, resolve } from 'node:path'
import type { FileGrantId, JobRequest, JobSnapshot } from '../shared-contract/desktop'
import type {
  RootsMagicAcknowledgement,
  RootsMagicArtifactRequest,
  RootsMagicExportRequest,
  RootsMagicJobResult,
  RootsMagicOutputSelection,
  RootsMagicPresetDefinitions,
  RootsMagicQueryRequest,
  RootsMagicSourceReferenceRequest,
} from '../shared-contract/rootsmagic'
import { FileGrantBrokerError, type FileGrantBroker } from './file-grant-broker'

const sourceLimit = 8
const outputLimit = 16
const jobLimit = 64
const opaque = () => randomBytes(32).toString('hex')

/** Defines the authenticated, fixed-route sidecar operations used by the native broker. */
export interface RootsMagicWorkbenchClient {
  /** Starts inspection of one private source manifest. */
  inspect(sourceCapability: string, signal?: AbortSignal): Promise<Readonly<JobSnapshot>>
  /** Returns the fixed query definitions. */
  presets(signal?: AbortSignal): Promise<Readonly<RootsMagicPresetDefinitions>>
  /** Starts one fixed query. */
  query(request: Readonly<RootsMagicQueryRequest>, signal?: AbortSignal): Promise<Readonly<JobSnapshot>>
  /** Starts one portable GEDCOM export. */
  export(request: Readonly<RootsMagicExportRequest>, signal?: AbortSignal): Promise<Readonly<JobSnapshot>>
  /** Gets the typed result for one completed job. */
  result(jobId: string, signal?: AbortSignal): Promise<Readonly<RootsMagicJobResult>>
  /** Discards one retained source session. */
  discard(sourceRef: string): Promise<Readonly<RootsMagicAcknowledgement>>
  /** Requests cancellation of one job. */
  cancel(jobId: string): Promise<Readonly<JobSnapshot>>
}

/** Defines trusted native operations for selecting and revealing an export directory. */
export interface RootsMagicNativePort {
  /** Selects the exact path of one new output directory without creating it. */
  selectNewOutputDirectory(owner: object, displayName: string, signal?: AbortSignal): Promise<string | null>
  /** Reveals a completed export directory in the platform file manager. */
  reveal(path: string): Promise<void>
}

interface SourceEntry {
  readonly owner: object
  readonly sourceRef: string
  readonly generation: number
}

interface OutputEntry {
  readonly owner: object
  readonly id: string
  readonly path: string
  readonly displayName: string
  readonly generation: number
  used: boolean
}

interface JobEntry {
  readonly owner: object
  readonly jobId: string
  readonly kind: RootsMagicJobResult['kind']
  readonly generation: number
  sourceRef?: string
  readonly outputId?: string
  artifactId?: string
  result?: Readonly<RootsMagicJobResult>
}

interface PendingInspection {
  readonly owner: object
  readonly grantId: FileGrantId
  readonly generation: number
  revoked: boolean
  manifestPath?: string
}

/** Supplies the private manifest directory and trusted ports required by the broker. */
export interface RootsMagicWorkbenchBrokerOptions {
  /** Private directory shared only with the local sidecar. */
  readonly directory: string
  /** Main-process file capability broker. */
  readonly files: FileGrantBroker
  /** Authenticated fixed-route sidecar client. */
  readonly client: Readonly<RootsMagicWorkbenchClient>
  /** Trusted native folder picker and reveal adapter. */
  readonly native: Readonly<RootsMagicNativePort>
}

function fail(code: 'FILE_GRANT_FORBIDDEN' | 'FILE_SELECTION_INVALID' | 'FILE_OPERATION_CANCELLED'): never {
  throw new FileGrantBrokerError(code)
}

function checkSignal(signal?: AbortSignal): void {
  if (signal?.aborted) fail('FILE_OPERATION_CANCELLED')
}

async function removeManifest(path: string | undefined): Promise<void> {
  if (path === undefined) return
  await unlink(path).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error
  })
}

async function writeManifest(directory: string, suffix: string, value: object): Promise<Readonly<{ id: string; path: string }>> {
  const id = opaque()
  const path = join(directory, `${id}.${suffix}.json`)
  let handle: FileHandle | undefined
  try {
    handle = await open(path, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY, 0o600)
    await handle.writeFile(JSON.stringify(value), 'utf8')
    await handle.sync()
    return Object.freeze({ id, path })
  } catch (error) {
    await removeManifest(path).catch(() => undefined)
    throw error
  } finally {
    await handle?.close()
  }
}

async function inspectSource(path: string, maximum: number): Promise<Readonly<{ size: number; sha256: string }>> {
  const before = await lstat(path)
  if (!before.isFile() || before.isSymbolicLink() || before.size > maximum) fail('FILE_SELECTION_INVALID')
  const canonical = await realpath(path)
  if (canonical !== resolve(path)) fail('FILE_SELECTION_INVALID')
  const handle = await open(path, constants.O_RDONLY)
  const hash = createHash('sha256')
  const buffer = Buffer.allocUnsafe(1024 * 1024)
  try {
    let position = 0
    while (position < before.size) {
      const { bytesRead } = await handle.read(buffer, 0, Math.min(buffer.length, before.size - position), position)
      if (bytesRead === 0) fail('FILE_SELECTION_INVALID')
      hash.update(buffer.subarray(0, bytesRead))
      position += bytesRead
    }
  } finally {
    await handle.close()
  }
  const after = await lstat(path)
  if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size
    || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) fail('FILE_SELECTION_INVALID')
  return Object.freeze({ size: before.size, sha256: hash.digest('hex') })
}

function selectedOutput(value: string): string {
  if (!isAbsolute(value) || value.includes('\0') || normalize(value) !== value) fail('FILE_SELECTION_INVALID')
  const name = basename(value)
  // eslint-disable-next-line no-control-regex
  if (name === '.' || name === '..' || name.length === 0 || name.length > 255 || /[/\\\u0000-\u001f\u007f]/.test(name)) {
    fail('FILE_SELECTION_INVALID')
  }
  return resolve(value)
}

/** Owns path-free RootsMagic source, output, result, and reveal authority per application window. */
export class RootsMagicWorkbenchBroker {
  private readonly directory: string
  private readonly files: FileGrantBroker
  private readonly client: Readonly<RootsMagicWorkbenchClient>
  private readonly native: Readonly<RootsMagicNativePort>
  private readonly generations = new WeakMap<object, number>()
  private readonly owners = new Set<object>()
  private readonly sources = new Map<string, SourceEntry>()
  private readonly outputs = new Map<string, OutputEntry>()
  private readonly jobs = new Map<string, JobEntry>()
  private readonly pending = new Set<PendingInspection>()
  private outputSelections = 0

  /** Creates a broker without publishing any authority. */
  constructor(options: Readonly<RootsMagicWorkbenchBrokerOptions>) {
    this.directory = options.directory
    this.files = options.files
    this.client = options.client
    this.native = options.native
  }

  private generation(owner: object): number { return this.generations.get(owner) ?? 0 }

  private active(owner: object, generation: number): boolean {
    return this.generation(owner) === generation
  }

  private requireActive(owner: object, generation: number, signal?: AbortSignal): void {
    checkSignal(signal)
    if (!this.active(owner, generation)) fail('FILE_OPERATION_CANCELLED')
  }

  private ownedSource(owner: object, sourceRef: string): SourceEntry {
    const entry = this.sources.get(sourceRef)
    if (!entry || entry.owner !== owner || !this.active(owner, entry.generation)) fail('FILE_GRANT_FORBIDDEN')
    return entry
  }

  private inspectionCapacity(): number {
    let submitted = 0
    for (const job of this.jobs.values()) {
      if (job.kind === 'inspection' && job.sourceRef === undefined) submitted += 1
    }
    return this.sources.size + this.pending.size + submitted
  }

  /** Starts immutable source inspection and detaches the resulting session from the picker grant. */
  async inspect(owner: object, grantId: FileGrantId, signal?: AbortSignal): Promise<Readonly<JobSnapshot>> {
    if (this.inspectionCapacity() >= sourceLimit || this.jobs.size >= jobLimit) fail('FILE_SELECTION_INVALID')
    const generation = this.generation(owner)
    this.owners.add(owner)
    const pending: PendingInspection = { owner, grantId, generation, revoked: false }
    this.pending.add(pending)
    try {
      const grant = await this.files.resolveReadGrant(owner, grantId, 'rootsmagic-read')
      this.requireActive(owner, generation, signal)
      if (pending.revoked) fail('FILE_OPERATION_CANCELLED')
      const inspected = await inspectSource(grant.path, grant.maxBytes)
      this.requireActive(owner, generation, signal)
      if (pending.revoked) fail('FILE_OPERATION_CANCELLED')
      const manifest = await writeManifest(this.directory, 'rootsmagic-source', {
        schema_version: 1,
        path: grant.path,
        size_bytes: inspected.size,
        sha256: inspected.sha256,
        friendly_name: basename(grant.path),
      })
      pending.manifestPath = manifest.path
      this.requireActive(owner, generation, signal)
      if (pending.revoked) fail('FILE_OPERATION_CANCELLED')
      const snapshot = await this.client.inspect(manifest.id, signal)
      if (!this.active(owner, generation) || pending.revoked || signal?.aborted) {
        await this.client.cancel(snapshot.job_id).catch(() => undefined)
        fail('FILE_OPERATION_CANCELLED')
      }
      this.jobs.set(snapshot.job_id, { owner, jobId: snapshot.job_id, kind: 'inspection', generation })
      return snapshot
    } finally {
      this.pending.delete(pending)
      await removeManifest(pending.manifestPath).catch(() => undefined)
    }
  }

  /** Returns the sidecar's fixed query definitions. */
  presets(signal?: AbortSignal): Promise<Readonly<RootsMagicPresetDefinitions>> {
    return this.client.presets(signal)
  }

  /** Starts one fixed query for an owned retained source. */
  async query(owner: object, request: Readonly<RootsMagicQueryRequest>, signal?: AbortSignal): Promise<Readonly<JobSnapshot>> {
    const source = this.ownedSource(owner, request.source_ref)
    if (this.jobs.size >= jobLimit) fail('FILE_SELECTION_INVALID')
    const snapshot = await this.client.query(request, signal)
    if (!this.active(owner, source.generation) || signal?.aborted || this.sources.get(source.sourceRef) !== source) {
      await this.client.cancel(snapshot.job_id).catch(() => undefined)
      fail('FILE_OPERATION_CANCELLED')
    }
    this.jobs.set(snapshot.job_id, { owner, jobId: snapshot.job_id, kind: 'query', generation: source.generation,
      sourceRef: source.sourceRef })
    return snapshot
  }

  /** Selects one exact, nonexistent destination path and returns only an opaque capability. */
  async selectOutput(owner: object, displayName: string, signal?: AbortSignal): Promise<Readonly<RootsMagicOutputSelection> | null> {
    if (this.outputs.size + this.outputSelections >= outputLimit) fail('FILE_SELECTION_INVALID')
    this.outputSelections += 1
    const generation = this.generation(owner)
    this.owners.add(owner)
    try {
      const selected = await this.native.selectNewOutputDirectory(owner, displayName, signal)
      this.requireActive(owner, generation, signal)
      if (selected === null) fail('FILE_OPERATION_CANCELLED')
      const path = selectedOutput(selected)
      try {
        await lstat(path)
        fail('FILE_SELECTION_INVALID')
      } catch (error) {
        if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error
      }
      const parent = await lstat(dirname(path))
      if (!parent.isDirectory() || parent.isSymbolicLink()) fail('FILE_SELECTION_INVALID')
      const canonicalParent = await realpath(dirname(path))
      if (canonicalParent !== resolve(dirname(path))) fail('FILE_SELECTION_INVALID')
      this.requireActive(owner, generation, signal)
      const id = opaque()
      const actualName = basename(path)
      this.outputs.set(id, { owner, id, path, displayName: actualName, generation, used: false })
      return Object.freeze({ schema_version: 1, output_capability: id, display_name: actualName })
    } finally {
      this.outputSelections -= 1
    }
  }

  /** Retires terminal failed or cancelled jobs and their unusable output authority. */
  observeJob(snapshot: Readonly<JobSnapshot>): void {
    if (snapshot.state !== 'failed' && snapshot.state !== 'cancelled') return
    const job = this.jobs.get(snapshot.job_id)
    if (job === undefined) return
    this.jobs.delete(job.jobId)
    if (job.outputId !== undefined) this.outputs.delete(job.outputId)
  }

  /** Starts one portable GEDCOM export using owned source and output capabilities. */
  async export(owner: object, request: Readonly<RootsMagicExportRequest>, signal?: AbortSignal): Promise<Readonly<JobSnapshot>> {
    const source = this.ownedSource(owner, request.source_ref)
    const output = this.outputs.get(request.output_capability)
    if (!output || output.owner !== owner || output.used || output.generation !== source.generation
      || this.jobs.size >= jobLimit) fail('FILE_GRANT_FORBIDDEN')
    output.used = true
    let manifest: Readonly<{ id: string; path: string }> | undefined
    let submitted = false
    try {
      manifest = await writeManifest(this.directory, 'rootsmagic-output', {
        schema_version: 1,
        path: output.path,
      })
      this.requireActive(owner, source.generation, signal)
      if (this.sources.get(source.sourceRef) !== source || this.outputs.get(output.id) !== output) {
        fail('FILE_OPERATION_CANCELLED')
      }
      const snapshot = await this.client.export({ ...request, output_capability: manifest.id }, signal)
      if (!this.active(owner, source.generation) || signal?.aborted || this.sources.get(source.sourceRef) !== source
        || this.outputs.get(output.id) !== output) {
        await this.client.cancel(snapshot.job_id).catch(() => undefined)
        fail('FILE_OPERATION_CANCELLED')
      }
      this.jobs.set(snapshot.job_id, { owner, jobId: snapshot.job_id, kind: 'export', generation: source.generation,
        sourceRef: source.sourceRef, outputId: output.id })
      submitted = true
      return snapshot
    } finally {
      if (!submitted) this.outputs.delete(output.id)
      await removeManifest(manifest?.path).catch(() => undefined)
    }
  }

  /** Returns one owned job result and publishes source or artifact authority only after validation. */
  async result(owner: object, request: Readonly<JobRequest>, signal?: AbortSignal): Promise<Readonly<RootsMagicJobResult>> {
    const job = this.jobs.get(request.job_id)
    if (!job || job.owner !== owner || !this.active(owner, job.generation)) fail('FILE_GRANT_FORBIDDEN')
    if (job.result !== undefined) return job.result
    const result = await this.client.result(job.jobId, signal)
    if (!this.active(owner, job.generation) || signal?.aborted || this.jobs.get(job.jobId) !== job) {
      if (result.kind === 'inspection') {
        await this.client.discard(result.result.source_ref).catch(() => undefined)
      }
      fail('FILE_OPERATION_CANCELLED')
    }
    this.requireActive(owner, job.generation, signal)
    if (this.jobs.get(job.jobId) !== job || result.kind !== job.kind) fail('FILE_OPERATION_CANCELLED')
    if (result.kind === 'inspection') {
      if (this.sources.size >= sourceLimit) {
        await this.client.discard(result.result.source_ref).catch(() => undefined)
        fail('FILE_SELECTION_INVALID')
      }
      this.sources.set(result.result.source_ref, {
        owner,
        sourceRef: result.result.source_ref,
        generation: job.generation,
      })
      job.sourceRef = result.result.source_ref
      job.result = result
    } else if (result.kind === 'query') {
      if (!job.sourceRef || !this.sources.has(job.sourceRef)) fail('FILE_OPERATION_CANCELLED')
      this.jobs.delete(job.jobId)
    } else {
      const output = job.outputId === undefined ? undefined : this.outputs.get(job.outputId)
      if (!output || !job.sourceRef || result.result.source_ref !== job.sourceRef
        || result.result.display_name !== output.displayName) fail('FILE_OPERATION_CANCELLED')
      job.artifactId = result.result.artifact_id
      job.result = result
    }
    return result
  }

  /** Discards one owned source and revokes its dependent result authority. */
  async discard(owner: object, request: Readonly<RootsMagicSourceReferenceRequest>): Promise<Readonly<RootsMagicAcknowledgement>> {
    const source = this.ownedSource(owner, request.source_ref)
    this.sources.delete(source.sourceRef)
    for (const [id, job] of this.jobs) {
      if (job.owner !== owner || job.sourceRef !== source.sourceRef) continue
      if (job.outputId !== undefined) this.outputs.delete(job.outputId)
      this.jobs.delete(id)
    }
    return this.client.discard(source.sourceRef)
  }

  /** Reveals the directory for one completed export owned by the requesting window. */
  async reveal(owner: object, request: Readonly<RootsMagicArtifactRequest>): Promise<Readonly<RootsMagicAcknowledgement>> {
    const job = [...this.jobs.values()].find((entry) => entry.owner === owner && entry.artifactId === request.artifact_id
      && this.active(owner, entry.generation))
    const output = job?.outputId === undefined ? undefined : this.outputs.get(job.outputId)
    if (!job || !output || output.owner !== owner) fail('FILE_GRANT_FORBIDDEN')
    await this.native.reveal(output.path)
    if (!this.active(owner, job.generation)) fail('FILE_OPERATION_CANCELLED')
    return Object.freeze({ schema_version: 1 })
  }

  /** Revokes a picker grant only while its inspection handoff remains in flight. */
  revokeGrant(owner: object, grantId: FileGrantId): void {
    for (const entry of this.pending) if (entry.owner === owner && entry.grantId === grantId) entry.revoked = true
  }

  /** Revokes every source, output, pending operation, and job owned by one window. */
  async revokeOwner(owner: object): Promise<void> {
    this.generations.set(owner, this.generation(owner) + 1)
    this.owners.delete(owner)
    const sources = [...this.sources.values()].filter((entry) => entry.owner === owner)
    const jobs = [...this.jobs.values()].filter((entry) => entry.owner === owner)
    const pending = [...this.pending].filter((entry) => entry.owner === owner)
    for (const entry of pending) entry.revoked = true
    for (const entry of sources) this.sources.delete(entry.sourceRef)
    for (const [id, entry] of this.outputs) if (entry.owner === owner) this.outputs.delete(id)
    for (const entry of jobs) this.jobs.delete(entry.jobId)
    await Promise.allSettled([
      ...sources.map((entry) => this.client.discard(entry.sourceRef)),
      ...jobs.map((entry) => this.client.cancel(entry.jobId)),
      ...pending.map((entry) => removeManifest(entry.manifestPath)),
    ])
  }

  /** Revokes all window sessions, including after a sidecar restart. */
  async revokeAll(): Promise<void> {
    await Promise.allSettled([...this.owners].map((owner) => this.revokeOwner(owner)))
  }
}
