/** Coordinates bounded, path-free host-file operations across trusted execution adapters. */

import { constants } from 'node:fs'
import { createHash, randomBytes } from 'node:crypto'
import { open, rm, unlink, type FileHandle } from 'node:fs/promises'
import { join } from 'node:path'
import type {
  ArtifactRef,
  FileGrantId,
  FileReadPurpose,
  FileWritePurpose,
  MediatedOperationRequest,
  MediatedOperationResult,
} from '../shared-contract/desktop'
import { parseMediatedOperationRequest } from '../shared-contract/runtime'
import type { HostRealizedContainerMount } from './container-supervisor'
import {
  FileGrantBroker,
  FileGrantBrokerError,
} from './file-grant-broker'
import {
  cleanupStaleMediatedOperationMounts,
  MediatedMountPolicyError,
  prepareMediatedOperationMounts,
  validateRealizedMediatedMounts,
  type MediatedOperationMountPlan,
} from './container-operation-mount-policy'

/** Enumerates stable failures without exposing host paths or adapter diagnostics. */
export type MediatedOperationBrokerFailureCode =
  | 'INVALID_REQUEST'
  | 'OPERATION_REPLAYED'
  | 'OPERATION_CONFLICT'
  | 'LIMIT_EXCEEDED'
  | 'CANCELLED'
  | 'TIMED_OUT'
  | 'GRANT_REJECTED'
  | 'ADAPTER_FAILED'
  | 'OUTPUT_INVALID'
  | 'MOUNT_MISMATCH'
  | 'CLEANUP_FAILED'

const ERROR_MESSAGES: Readonly<Record<MediatedOperationBrokerFailureCode, string>> = Object.freeze({
  INVALID_REQUEST: 'The mediated operation request is invalid.',
  OPERATION_REPLAYED: 'The mediated operation identifier has already been used.',
  OPERATION_CONFLICT: 'The mediated operation concurrency limit has been reached.',
  LIMIT_EXCEEDED: 'The mediated operation exceeded an allowed resource limit.',
  CANCELLED: 'The mediated operation was cancelled.',
  TIMED_OUT: 'The mediated operation timed out.',
  GRANT_REJECTED: 'A mediated file grant was rejected.',
  ADAPTER_FAILED: 'The trusted operation adapter failed.',
  OUTPUT_INVALID: 'A staged operation output was invalid.',
  MOUNT_MISMATCH: 'The realized operation mounts were not authorized.',
  CLEANUP_FAILED: 'The private operation staging area could not be cleaned safely.',
})

/** Carries a stable, path-free mediation failure. */
export class MediatedOperationBrokerError extends Error {
  constructor(readonly code: MediatedOperationBrokerFailureCode) {
    super(ERROR_MESSAGES[code])
    this.name = 'MediatedOperationBrokerError'
  }
}

/** Reports structural operation progress without host paths or payload content. */
export interface MediatedOperationProgress {
  readonly operation_id: string
  readonly phase: 'staging' | 'executing' | 'validating' | 'publishing' | 'completed'
  readonly completed: number
  readonly total: number
}

/** Gives the trusted local adapter immutable staged-input metadata and trusted-only paths. */
export interface TrustedLocalStagedInput {
  readonly grant_id: string
  readonly purpose: FileReadPurpose
  readonly size_bytes: number
  readonly sha256: string
  readonly hostPath: string
  readonly containerPath: string
}

/** Gives the trusted local adapter one private output destination and its container path. */
export interface TrustedLocalStagedOutput {
  readonly grant_id: string
  readonly purpose: FileWritePurpose
  readonly hostPath: string
  readonly containerPath: string
}

/** Defines trusted local-container execution state that must never cross the renderer bridge. */
export interface TrustedLocalOperationContext {
  readonly request: Readonly<MediatedOperationRequest>
  readonly mountPlan: Readonly<MediatedOperationMountPlan>
  readonly inputs: readonly Readonly<TrustedLocalStagedInput>[]
  readonly outputs: readonly Readonly<TrustedLocalStagedOutput>[]
}

/** Holds a prepared worker whose complete mounts can be checked before execution. */
export interface PreparedLocalMediatedOperation {
  readonly realizedMounts: readonly HostRealizedContainerMount[]
  execute(signal: AbortSignal): Promise<void>
  dispose(): Promise<void>
}

/** Prepares, but does not execute, a local worker for broker-side mount validation. */
export interface LocalMediatedOperationAdapter {
  prepare(
    context: Readonly<TrustedLocalOperationContext>,
    signal: AbortSignal,
  ): Promise<Readonly<PreparedLocalMediatedOperation>>
}

/** Exposes a single-use, bounded input stream without any host path. */
export interface RemoteMediatedInput {
  readonly grant_id: string
  readonly purpose: FileReadPurpose
  readonly size_bytes: number
  readonly sha256: string
  read(): AsyncIterable<Uint8Array>
}

/** Accepts a single-use, bounded output stream without any host path. */
export interface RemoteMediatedOutput {
  readonly grant_id: string
  readonly purpose: FileWritePurpose
  write(chunks: AsyncIterable<Uint8Array>): Promise<void>
}

/** Defines the path-free context available to a trusted remote-service adapter. */
export interface RemoteMediatedOperationContext {
  readonly request: Readonly<MediatedOperationRequest>
  readonly inputs: readonly Readonly<RemoteMediatedInput>[]
  readonly outputs: readonly Readonly<RemoteMediatedOutput>[]
}

/** Executes a remote operation using only bounded streams and opaque grant metadata. */
export interface RemoteMediatedOperationAdapter {
  execute(
    context: Readonly<RemoteMediatedOperationContext>,
    signal: AbortSignal,
  ): Promise<void>
}

/** Configures the trusted broker, adapters, and resource ceilings. */
export interface MediatedOperationBrokerOptions {
  readonly runtimeProfileRoot: string
  readonly fileGrants: FileGrantBroker
  readonly localAdapter: LocalMediatedOperationAdapter
  readonly remoteAdapter: RemoteMediatedOperationAdapter
  readonly maxConcurrent?: number
  readonly maxDurationMs?: number
  readonly maxTotalInputBytes?: number
  readonly removeOperationRoot?: (path: string) => Promise<void>
}

interface ArtifactPolicy {
  readonly artifactType: string
  readonly mediaType: string
}

interface OperationPolicy {
  readonly minimumInputs: number
  readonly maximumInputs: number
  readonly inputPurpose: FileReadPurpose
  readonly outputPurposes: readonly FileWritePurpose[]
  readonly artifacts: readonly ArtifactPolicy[]
}

type OperationPhase = 'initializing' | 'staging' | 'executing' | 'validating' | 'publishing'

const operationPolicies: Readonly<Record<string, OperationPolicy>> = Object.freeze({
  'rootsmagic.export': Object.freeze({
    minimumInputs: 1,
    maximumInputs: 1,
    inputPurpose: 'rootsmagic-read',
    outputPurposes: Object.freeze(['gedcom-write', 'markdown-write'] as const),
    artifacts: Object.freeze([
      Object.freeze({ artifactType: 'gedcom_export', mediaType: 'text/vnd.familysearch.gedcom' }),
      Object.freeze({ artifactType: 'export_report', mediaType: 'text/markdown' }),
    ]),
  }),
  'gedcom.merge': Object.freeze({
    minimumInputs: 2,
    maximumInputs: 16,
    inputPurpose: 'gedcom-read',
    outputPurposes: Object.freeze(['gedcom-write', 'markdown-write'] as const),
    artifacts: Object.freeze([
      Object.freeze({ artifactType: 'gedcom_merge', mediaType: 'text/vnd.familysearch.gedcom' }),
      Object.freeze({ artifactType: 'quality_report', mediaType: 'text/markdown' }),
    ]),
  }),
  'gedcom.subtree': Object.freeze({
    minimumInputs: 1,
    maximumInputs: 1,
    inputPurpose: 'gedcom-read',
    outputPurposes: Object.freeze(['gedcom-write'] as const),
    artifacts: Object.freeze([
      Object.freeze({ artifactType: 'gedcom_subtree', mediaType: 'text/vnd.familysearch.gedcom' }),
    ]),
  }),
  'gedcom.quality': Object.freeze({
    minimumInputs: 1,
    maximumInputs: 1,
    inputPurpose: 'gedcom-read',
    outputPurposes: Object.freeze(['markdown-write'] as const),
    artifacts: Object.freeze([
      Object.freeze({ artifactType: 'quality_report', mediaType: 'text/markdown' }),
    ]),
  }),
})

const MAX_CONCURRENT = 2
const MAX_DURATION_MS = 300_000
const MAX_TOTAL_INPUT_BYTES = 8_589_934_592
const STREAM_CHUNK_BYTES = 1_048_576
const outputByteLimits: Readonly<Record<FileWritePurpose, number>> = Object.freeze({
  'gedcom-write': 536_870_912,
  'json-write': 67_108_864,
  'markdown-write': 67_108_864,
})
const readExtensions: Readonly<Record<FileReadPurpose, string>> = Object.freeze({
  'gedcom-read': '.ged',
  'rootsmagic-read': '.rmtree',
})
const writeExtensions: Readonly<Record<FileWritePurpose, string>> = Object.freeze({
  'gedcom-write': '.ged',
  'json-write': '.json',
  'markdown-write': '.md',
})

function fail(code: MediatedOperationBrokerFailureCode): never {
  throw new MediatedOperationBrokerError(code)
}

function requirePositiveInteger(value: number, fallback: number): number {
  return Number.isSafeInteger(value) && value > 0 ? value : fallback
}

function requirePolicy(request: Readonly<MediatedOperationRequest>): Readonly<OperationPolicy> {
  const policy = operationPolicies[request.operation]
  if (!policy
    || request.inputs.length < policy.minimumInputs
    || request.inputs.length > policy.maximumInputs
    || request.outputs.length !== policy.outputPurposes.length) fail('INVALID_REQUEST')
  if (request.inputs.some((grant) => grant.access !== 'read')
    || request.outputs.some((grant) => grant.access !== 'write')) fail('INVALID_REQUEST')
  if (request.outputs.some((_grant, index) => policy.outputPurposes[index] === undefined)) {
    fail('INVALID_REQUEST')
  }
  return policy
}

function checkSignal(signal: AbortSignal, timedOut: boolean): void {
  if (signal.aborted) fail(timedOut ? 'TIMED_OUT' : 'CANCELLED')
}

function awaitAdapterOperation<T>(
  operation: Promise<T>,
  signal: AbortSignal,
  timedOut: () => boolean,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false
    const finish = (callback: () => void): void => {
      if (settled) return
      settled = true
      signal.removeEventListener('abort', abort)
      callback()
    }
    const abort = (): void => {
      finish(() => reject(new MediatedOperationBrokerError(
        timedOut() ? 'TIMED_OUT' : 'CANCELLED',
      )))
    }
    signal.addEventListener('abort', abort, { once: true })
    operation.then(
      (value) => finish(() => resolve(value)),
      (error: unknown) => finish(() => reject(error)),
    )
    if (signal.aborted) abort()
  })
}

function optionalOpenFlag(name: 'O_CLOEXEC' | 'O_NOFOLLOW' | 'O_NONBLOCK'): number {
  return (constants as typeof constants & Readonly<Record<string, number | undefined>>)[name] ?? 0
}

async function removeIfPresent(path: string): Promise<void> {
  try {
    await unlink(path)
  } catch (error) {
    if (typeof error !== 'object' || error === null || (error as { code?: unknown }).code !== 'ENOENT') throw error
  }
}

async function writeAll(handle: FileHandle, chunk: Uint8Array): Promise<void> {
  let offset = 0
  while (offset < chunk.byteLength) {
    const written = await handle.write(chunk, offset, chunk.byteLength - offset, null)
    if (written.bytesWritten <= 0) fail('ADAPTER_FAILED')
    offset += written.bytesWritten
  }
}

function mapFailure(
  error: unknown,
  phase: OperationPhase,
  signal: AbortSignal,
  timedOut: boolean,
): MediatedOperationBrokerError {
  if (signal.aborted) return new MediatedOperationBrokerError(timedOut ? 'TIMED_OUT' : 'CANCELLED')
  if (error instanceof MediatedOperationBrokerError) return error
  if (error instanceof MediatedMountPolicyError) {
    if (error.code === 'MOUNT_MISMATCH') return new MediatedOperationBrokerError('MOUNT_MISMATCH')
    return new MediatedOperationBrokerError(phase === 'initializing' ? 'CLEANUP_FAILED' : 'GRANT_REJECTED')
  }
  if (error instanceof FileGrantBrokerError) {
    if (error.code === 'FILE_TOO_LARGE') return new MediatedOperationBrokerError('LIMIT_EXCEEDED')
    if (error.code === 'FILE_OPERATION_CANCELLED') {
      return new MediatedOperationBrokerError(timedOut ? 'TIMED_OUT' : 'CANCELLED')
    }
    if (phase === 'validating') return new MediatedOperationBrokerError('OUTPUT_INVALID')
    return new MediatedOperationBrokerError('GRANT_REJECTED')
  }
  if (phase === 'executing') return new MediatedOperationBrokerError('ADAPTER_FAILED')
  if (phase === 'validating') return new MediatedOperationBrokerError('OUTPUT_INVALID')
  if (phase === 'initializing') return new MediatedOperationBrokerError('CLEANUP_FAILED')
  return new MediatedOperationBrokerError('GRANT_REJECTED')
}

/** Owns single-use operation state, staging, validation, publication, and cleanup. */
export class MediatedOperationBroker {
  private readonly runtimeProfileRoot: string
  private readonly fileGrants: FileGrantBroker
  private readonly localAdapter: LocalMediatedOperationAdapter
  private readonly remoteAdapter: RemoteMediatedOperationAdapter
  private readonly maxConcurrent: number
  private readonly maxDurationMs: number
  private readonly maxTotalInputBytes: number
  private readonly removeOperationRoot: (path: string) => Promise<void>
  private readonly seenOperationIds = new Set<string>()
  private initialization: Promise<void> | null = null
  private activeOperations = 0

  constructor(options: Readonly<MediatedOperationBrokerOptions>) {
    this.runtimeProfileRoot = options.runtimeProfileRoot
    this.fileGrants = options.fileGrants
    this.localAdapter = options.localAdapter
    this.remoteAdapter = options.remoteAdapter
    this.maxConcurrent = requirePositiveInteger(options.maxConcurrent ?? MAX_CONCURRENT, MAX_CONCURRENT)
    this.maxDurationMs = requirePositiveInteger(options.maxDurationMs ?? MAX_DURATION_MS, MAX_DURATION_MS)
    this.maxTotalInputBytes = requirePositiveInteger(
      options.maxTotalInputBytes ?? MAX_TOTAL_INPUT_BYTES,
      MAX_TOTAL_INPUT_BYTES,
    )
    this.removeOperationRoot = options.removeOperationRoot
      ?? (async (path) => rm(path, { recursive: true, force: false }))
  }

  /** Cleans exact stale operation directories once before accepting staged work. */
  async initialize(): Promise<void> {
    if (this.initialization === null) {
      this.initialization = cleanupStaleMediatedOperationMounts(this.runtimeProfileRoot).then(() => undefined)
    }
    try {
      await this.initialization
    } catch {
      fail('CLEANUP_FAILED')
    }
  }

  /** Executes one allowlisted operation and returns only validated, published artifact references. */
  async execute(
    owner: object,
    value: unknown,
    onProgress?: (progress: Readonly<MediatedOperationProgress>) => void,
    externalSignal?: AbortSignal,
  ): Promise<Readonly<MediatedOperationResult>> {
    let request: Readonly<MediatedOperationRequest>
    try {
      request = parseMediatedOperationRequest(value)
    } catch {
      fail('INVALID_REQUEST')
    }
    const policy = requirePolicy(request)
    if (this.seenOperationIds.has(request.operation_id)) fail('OPERATION_REPLAYED')
    if (this.activeOperations >= this.maxConcurrent) fail('OPERATION_CONFLICT')

    this.seenOperationIds.add(request.operation_id)
    this.activeOperations += 1
    const controller = new AbortController()
    let timedOut = false
    const abortFromCaller = (): void => controller.abort()
    if (externalSignal?.aborted) controller.abort()
    else externalSignal?.addEventListener('abort', abortFromCaller, { once: true })
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, this.maxDurationMs)
    timeout.unref()

    let phase: OperationPhase = 'initializing'
    let mountPlan: Readonly<MediatedOperationMountPlan> | null = null
    let operationFailure: MediatedOperationBrokerError | null = null
    let operationResult: Readonly<MediatedOperationResult> | null = null
    let cleanupFailed = false
    const emit = (
      progressPhase: MediatedOperationProgress['phase'],
      completed: number,
      total: number,
    ): void => {
      try {
        onProgress?.(Object.freeze({
          operation_id: request.operation_id,
          phase: progressPhase,
          completed,
          total,
        }))
      } catch {
        // Progress delivery is observational and must not change file-operation state.
      }
    }

    try {
      checkSignal(controller.signal, timedOut)
      await this.initialize()
      checkSignal(controller.signal, timedOut)
      mountPlan = await prepareMediatedOperationMounts(this.runtimeProfileRoot, request.operation_id)

      phase = 'staging'
      emit('staging', 0, request.inputs.length)
      const localInputs: TrustedLocalStagedInput[] = []
      let totalInputBytes = 0
      for (const [index, grant] of request.inputs.entries()) {
        checkSignal(controller.signal, timedOut)
        const purpose = policy.inputPurpose
        const hostPath = join(
          mountPlan.inputRoot,
          `input-${String(index + 1).padStart(3, '0')}${readExtensions[purpose]}`,
        )
        const staged = await this.fileGrants.stageReadGrant(
          owner,
          grant.grant_id as FileGrantId,
          purpose,
          hostPath,
          controller.signal,
        )
        totalInputBytes += staged.sizeBytes
        if (!Number.isSafeInteger(totalInputBytes) || totalInputBytes > this.maxTotalInputBytes) {
          fail('LIMIT_EXCEEDED')
        }
        localInputs.push(Object.freeze({
          grant_id: grant.grant_id,
          purpose,
          size_bytes: staged.sizeBytes,
          sha256: staged.sha256,
          hostPath,
          containerPath: `${mountPlan.mounts[0].target}/input-${String(index + 1).padStart(3, '0')}${readExtensions[purpose]}`,
        }))
        emit('staging', index + 1, request.inputs.length)
      }

      const localOutputs: TrustedLocalStagedOutput[] = request.outputs.map((grant, index) => {
        const purpose = policy.outputPurposes[index]
        if (purpose === undefined) fail('INVALID_REQUEST')
        const name = `output-${String(index + 1).padStart(3, '0')}${writeExtensions[purpose]}`
        return Object.freeze({
          grant_id: grant.grant_id,
          purpose,
          hostPath: join(mountPlan!.outputRoot, name),
          containerPath: `${mountPlan!.mounts[1].target}/${name}`,
        })
      })

      phase = 'executing'
      emit('executing', 0, 1)
      checkSignal(controller.signal, timedOut)
      if (request.transport === 'local-container') {
        const preparation = this.localAdapter.prepare(Object.freeze({
          request,
          mountPlan,
          inputs: Object.freeze(localInputs),
          outputs: Object.freeze(localOutputs),
        }), controller.signal)
        let prepared: Readonly<PreparedLocalMediatedOperation>
        try {
          prepared = await awaitAdapterOperation(preparation, controller.signal, () => timedOut)
        } catch (error) {
          if (controller.signal.aborted) {
            void preparation.then(
              async (latePreparation) => latePreparation.dispose().catch(() => undefined),
              () => undefined,
            )
          }
          throw error
        }
        try {
          checkSignal(controller.signal, timedOut)
          validateRealizedMediatedMounts(mountPlan, prepared.realizedMounts)
          checkSignal(controller.signal, timedOut)
          await awaitAdapterOperation(
            prepared.execute(controller.signal),
            controller.signal,
            () => timedOut,
          )
          checkSignal(controller.signal, timedOut)
        } finally {
          try {
            await prepared.dispose()
          } catch {
            fail('CLEANUP_FAILED')
          }
        }
      } else {
        const remoteInputs = localInputs.map((input) => this.remoteInput(input, controller.signal, () => timedOut))
        const remoteOutputs = localOutputs.map((output) => this.remoteOutput(output, controller.signal, () => timedOut))
        await awaitAdapterOperation(
          this.remoteAdapter.execute(Object.freeze({
            request,
            inputs: Object.freeze(remoteInputs),
            outputs: Object.freeze(remoteOutputs),
          }), controller.signal),
          controller.signal,
          () => timedOut,
        )
        checkSignal(controller.signal, timedOut)
      }
      emit('executing', 1, 1)

      phase = 'validating'
      emit('validating', 0, localOutputs.length)
      const validated = []
      for (const [index, output] of localOutputs.entries()) {
        checkSignal(controller.signal, timedOut)
        validated.push(await this.fileGrants.validateStagedOutput(
          output.purpose,
          output.hostPath,
          controller.signal,
        ))
        emit('validating', index + 1, localOutputs.length)
      }

      phase = 'publishing'
      emit('publishing', 0, localOutputs.length)
      const artifacts: ArtifactRef[] = []
      for (const [index, output] of localOutputs.entries()) {
        checkSignal(controller.signal, timedOut)
        const published = await this.fileGrants.publishWriteGrant(
          owner,
          output.grant_id as FileGrantId,
          output.purpose,
          output.hostPath,
          controller.signal,
        )
        const expected = validated[index]
        const artifactPolicy = policy.artifacts[index]
        if (expected === undefined || artifactPolicy === undefined
          || expected.sha256 !== published.sha256
          || expected.sizeBytes !== published.sizeBytes) fail('OUTPUT_INVALID')
        artifacts.push(Object.freeze({
          artifact_id: `art_${randomBytes(32).toString('hex')}`,
          artifact_type: artifactPolicy.artifactType,
          media_type: artifactPolicy.mediaType,
          sha256: published.sha256,
          size_bytes: published.sizeBytes,
          status: 'ready',
        }))
        emit('publishing', index + 1, localOutputs.length)
      }
      emit('completed', artifacts.length, artifacts.length)
      operationResult = Object.freeze({
        operation_id: request.operation_id,
        outputs: Object.freeze(artifacts),
        cleanup_status: 'complete',
      })
    } catch (error) {
      operationFailure = mapFailure(error, phase, controller.signal, timedOut)
    } finally {
      clearTimeout(timeout)
      externalSignal?.removeEventListener('abort', abortFromCaller)
      for (const grant of [...request.inputs, ...request.outputs]) {
        this.fileGrants.revokeGrant(owner, grant.grant_id)
      }
      if (mountPlan !== null) {
        try {
          await this.removeOperationRoot(mountPlan.operationRoot)
        } catch {
          cleanupFailed = true
        }
      }
      this.activeOperations -= 1
    }
    if (operationFailure !== null) {
      if (cleanupFailed) fail('CLEANUP_FAILED')
      throw operationFailure
    }
    if (operationResult === null) fail('ADAPTER_FAILED')
    if (!cleanupFailed) return operationResult
    return Object.freeze({ ...operationResult, cleanup_status: 'recovery-required' })
  }

  private remoteInput(
    input: Readonly<TrustedLocalStagedInput>,
    signal: AbortSignal,
    timedOut: () => boolean,
  ): Readonly<RemoteMediatedInput> {
    let redeemed = false
    const read = (): AsyncIterable<Uint8Array> => {
      if (redeemed) fail('GRANT_REJECTED')
      redeemed = true
      return this.readStagedInput(input, signal, timedOut)
    }
    return Object.freeze({
      grant_id: input.grant_id,
      purpose: input.purpose,
      size_bytes: input.size_bytes,
      sha256: input.sha256,
      read,
    })
  }

  private async *readStagedInput(
    input: Readonly<TrustedLocalStagedInput>,
    signal: AbortSignal,
    timedOut: () => boolean,
  ): AsyncIterable<Uint8Array> {
    checkSignal(signal, timedOut())
    const flags = constants.O_RDONLY
      | optionalOpenFlag('O_CLOEXEC')
      | optionalOpenFlag('O_NOFOLLOW')
      | optionalOpenFlag('O_NONBLOCK')
    let handle: FileHandle
    try {
      handle = await open(input.hostPath, flags)
    } catch {
      return fail('GRANT_REJECTED')
    }
    const digest = createHash('sha256')
    let total = 0
    try {
      const stat = await handle.stat()
      if (!stat.isFile() || stat.nlink !== 1 || stat.size !== input.size_bytes) fail('GRANT_REJECTED')
      while (true) {
        checkSignal(signal, timedOut())
        const chunk = Buffer.alloc(Math.min(STREAM_CHUNK_BYTES, input.size_bytes - total || STREAM_CHUNK_BYTES))
        const result = await handle.read(chunk, 0, chunk.length, null)
        if (result.bytesRead === 0) break
        const bytes = chunk.subarray(0, result.bytesRead)
        total += bytes.byteLength
        if (total > input.size_bytes) fail('GRANT_REJECTED')
        digest.update(bytes)
        yield bytes
      }
      if (total !== input.size_bytes || digest.digest('hex') !== input.sha256) fail('GRANT_REJECTED')
    } finally {
      await handle.close().catch(() => undefined)
    }
  }

  private remoteOutput(
    output: Readonly<TrustedLocalStagedOutput>,
    signal: AbortSignal,
    timedOut: () => boolean,
  ): Readonly<RemoteMediatedOutput> {
    let redeemed = false
    const write = async (chunks: AsyncIterable<Uint8Array>): Promise<void> => {
      if (redeemed) fail('GRANT_REJECTED')
      redeemed = true
      checkSignal(signal, timedOut())
      const flags = constants.O_WRONLY
        | constants.O_CREAT
        | constants.O_EXCL
        | optionalOpenFlag('O_CLOEXEC')
        | optionalOpenFlag('O_NOFOLLOW')
      let handle: FileHandle | null = null
      let complete = false
      try {
        handle = await open(output.hostPath, flags, 0o600)
        let total = 0
        for await (const chunk of chunks) {
          checkSignal(signal, timedOut())
          if (!ArrayBuffer.isView(chunk)
            || (chunk as Uint8Array).BYTES_PER_ELEMENT !== 1) fail('ADAPTER_FAILED')
          total += chunk.byteLength
          if (!Number.isSafeInteger(total) || total > outputByteLimits[output.purpose]) fail('LIMIT_EXCEEDED')
          await writeAll(handle, chunk)
        }
        checkSignal(signal, timedOut())
        await handle.sync()
        complete = true
      } finally {
        await handle?.close().catch(() => undefined)
        if (!complete) await removeIfPresent(output.hostPath).catch(() => undefined)
      }
    }
    return Object.freeze({
      grant_id: output.grant_id,
      purpose: output.purpose,
      write,
    })
  }
}
