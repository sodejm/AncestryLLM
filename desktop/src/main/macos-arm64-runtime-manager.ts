/** Owns verified, resumable installation and lifecycle state for the macOS ARM64 runtime. */

import { availableParallelism, homedir, totalmem } from 'node:os'
import { createHash, randomUUID, timingSafeEqual } from 'node:crypto'
import { constants, createReadStream } from 'node:fs'
import {
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  rename,
  rm,
  statfs,
  unlink,
  writeFile,
} from 'node:fs/promises'
import type { IncomingMessage } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { dirname, isAbsolute, join, resolve, sep } from 'node:path'
import type {
  LocalRuntimeApplyRequest,
  LocalRuntimeOperation,
  LocalRuntimePreview,
  LocalRuntimeRequest,
  LocalRuntimeResult,
  LocalRuntimeStatus,
} from '../shared-contract/desktop'
import {
  runBoundedHostProcess,
  type RunHostProcess,
} from './container-process'
import {
  extractReviewedTarGzip,
  runtimePolicyDigest,
  type MacosArm64RuntimePolicy,
  type RuntimeComponentPolicy,
  type RuntimeInstallEntry,
  type RuntimeVmImagePolicy,
} from './macos-arm64-runtime-policy'

const GIB = 1024 ** 3
const PROCESS_TIMEOUT_MS = 5 * 60 * 1000
const PROCESS_OUTPUT_BYTES = 256 * 1024
const MARKER_FILE = 'ownership.json'
const ENGINE_IDENTITY_FILE = 'engine-identity.json'
const RECEIPT_FILE = 'verification-receipt.json'
const INSTALLATION_OVERHEAD_BYTES = 64 * 1024 ** 2

/**
 * Reuses the renderer-visible operation vocabulary for the native runtime manager.
 */
export type MacosRuntimeOperation = LocalRuntimeOperation

/**
 * Enumerates fail-closed installation, ownership, integrity, and health failures.
 */
export type MacosRuntimeErrorCode =
  | 'RUNTIME_REQUEST_INVALID'
  | 'RUNTIME_HOST_UNSUPPORTED'
  | 'RUNTIME_PLAN_STALE'
  | 'RUNTIME_CONFIRMATION_REQUIRED'
  | 'RUNTIME_OFFLINE_UNAVAILABLE'
  | 'RUNTIME_DOWNLOAD_FAILED'
  | 'RUNTIME_ARTIFACT_INTEGRITY'
  | 'RUNTIME_COMPONENT_INTEGRITY'
  | 'RUNTIME_STORAGE_UNSAFE'
  | 'RUNTIME_NOT_INSTALLED'
  | 'RUNTIME_OWNERSHIP_INVALID'
  | 'RUNTIME_PROCESS_FAILED'
  | 'RUNTIME_HEALTH_FAILED'

const ERROR_MESSAGES: Readonly<Record<MacosRuntimeErrorCode, string>> = {
  RUNTIME_REQUEST_INVALID: 'The local runtime request is invalid.',
  RUNTIME_HOST_UNSUPPORTED: 'This host cannot run the supported local runtime.',
  RUNTIME_PLAN_STALE: 'The local runtime plan changed and must be reviewed again.',
  RUNTIME_CONFIRMATION_REQUIRED: 'The exact local runtime confirmation phrase is required.',
  RUNTIME_OFFLINE_UNAVAILABLE: 'The reviewed local runtime files are not available offline.',
  RUNTIME_DOWNLOAD_FAILED: 'A reviewed local runtime file could not be downloaded.',
  RUNTIME_ARTIFACT_INTEGRITY: 'A downloaded local runtime file failed integrity verification.',
  RUNTIME_COMPONENT_INTEGRITY: 'An installed local runtime component failed integrity verification.',
  RUNTIME_STORAGE_UNSAFE: 'The local runtime storage boundary is unsafe.',
  RUNTIME_NOT_INSTALLED: 'The app-owned local runtime is not installed.',
  RUNTIME_OWNERSHIP_INVALID: 'The app-owned local runtime ownership marker is invalid.',
  RUNTIME_PROCESS_FAILED: 'An app-owned local runtime process failed.',
  RUNTIME_HEALTH_FAILED: 'The app-owned local runtime failed its health check.',
}

/**
 * Reports a stable coded failure from reviewed macOS ARM64 runtime artifact installation without leaking sensitive host details.
 */
export class MacosRuntimeError extends Error {
  constructor(readonly code: MacosRuntimeErrorCode) {
    super(ERROR_MESSAGES[code])
    this.name = 'MacosRuntimeError'
  }
}

/**
 * Reports the host capacity and virtualization facts used to accept or reject an install plan.
 */
export interface MacosRuntimeHostInspection {
  readonly macosMajor: number
  readonly virtualizationAvailable: boolean
  readonly logicalCpus: number
  readonly totalMemoryBytes: number
  readonly freeBytes: number
  readonly existingDockerContexts: number
}

/**
 * Supplies the platform identity and bounded capacity inspection required before installation.
 */
export interface MacosRuntimeHost {
  readonly platform: string
  readonly architecture: string
  inspect(signal?: AbortSignal): Promise<MacosRuntimeHostInspection>
}

/**
 * Describes one resumable transfer of the policy-selected macOS runtime artifact.
 */
export interface DownloadRuntimeFileRequest {
  readonly sourceUrl: string
  readonly targetPath: string
  readonly offsetBytes: number
  readonly expectedSizeBytes: number
  readonly signal?: AbortSignal
}

/**
 * Transfers one reviewed artifact into a resumable temporary file without interpreting its contents.
 */
export type DownloadRuntimeFile = (request: DownloadRuntimeFileRequest) => Promise<void>

/**
 * Reuses the transport-neutral status returned to the renderer after local verification.
 */
export type MacosRuntimeStatus = LocalRuntimeStatus
/**
 * Reuses the transport-neutral preview containing the exact policy digest and planned changes.
 */
export type MacosRuntimePreview = LocalRuntimePreview
/**
 * Reuses the transport-neutral result emitted after a verified runtime operation.
 */
export type MacosRuntimeResult = LocalRuntimeResult

/**
 * Supplies the reviewed policy, storage root, and injectable host operations for runtime installation.
 */
export interface RuntimeManagerOptions {
  readonly rootDirectory: string
  readonly policy: MacosArm64RuntimePolicy
  readonly host: MacosRuntimeHost
  readonly download?: DownloadRuntimeFile
  readonly runProcess?: RunHostProcess
  readonly now?: () => Date
}

type RuntimeRequest = LocalRuntimeRequest
type RuntimeApplyRequest = LocalRuntimeApplyRequest

interface OwnershipMarker {
  readonly schema_version: 1
  readonly policy_sha256: string
  readonly profile: string
  readonly context: string
  readonly installed_at: string
}

type OwnershipState =
  | Readonly<{ readonly kind: 'missing' }>
  | Readonly<{ readonly kind: 'invalid' }>
  | Readonly<{
    readonly kind: 'valid'
    readonly marker: OwnershipMarker
    readonly currentPolicy: boolean
  }>

interface EngineIdentity {
  readonly schema_version: 1
  readonly policy_sha256: string
  readonly profile: string
  readonly context: string
  readonly endpoint: string
  readonly engine_id: string
}

type EngineIdentityState =
  | Readonly<{ readonly kind: 'missing' }>
  | Readonly<{ readonly kind: 'invalid' }>
  | Readonly<{ readonly kind: 'valid'; readonly identity: EngineIdentity }>

interface RuntimeIntegrity {
  readonly components: MacosRuntimeStatus['components']
  readonly vmImageInstalled: boolean
}

interface RuntimeStatusSnapshot {
  readonly status: MacosRuntimeStatus
  readonly inspection: MacosRuntimeHostInspection
  readonly integrity: RuntimeIntegrity
  readonly ownership: OwnershipState
}

interface RuntimePreviewSnapshot extends RuntimeStatusSnapshot {
  readonly preview: MacosRuntimePreview
}

const ACTIONS: Readonly<Record<MacosRuntimeOperation, readonly string[]>> = {
  setup: [
    'VERIFY_HOST',
    'DOWNLOAD_PINNED_COMPONENTS',
    'CREATE_APP_PROFILE',
    'START_RUNTIME',
    'VERIFY_RUNTIME',
  ],
  start: ['VERIFY_HOST', 'START_RUNTIME', 'VERIFY_RUNTIME'],
  stop: ['STOP_APP_RUNTIME'],
  repair: [
    'VERIFY_HOST',
    'VERIFY_PINNED_COMPONENTS',
    'REPAIR_APP_PROFILE',
    'VERIFY_RUNTIME',
  ],
  'uninstall-preserve': [
    'STOP_APP_RUNTIME',
    'REMOVE_APP_PROFILE',
    'REMOVE_VERIFIED_COMPONENTS',
    'PRESERVE_APP_DATA',
  ],
  'uninstall-delete': [
    'STOP_APP_RUNTIME',
    'REMOVE_APP_PROFILE',
    'REMOVE_VERIFIED_COMPONENTS',
    'DELETE_APP_DATA',
  ],
}

const CONFIRMATIONS: Readonly<Record<MacosRuntimeOperation, string>> = {
  setup: 'SET UP LOCAL RUNTIME',
  start: 'START LOCAL RUNTIME',
  stop: 'STOP LOCAL RUNTIME',
  repair: 'REPAIR LOCAL RUNTIME',
  'uninstall-preserve': 'REMOVE LOCAL RUNTIME',
  'uninstall-delete': 'DELETE LOCAL RUNTIME DATA',
}

function runtimeFail(code: MacosRuntimeErrorCode): never {
  throw new MacosRuntimeError(code)
}

function requireActive(signal?: AbortSignal): void {
  if (signal?.aborted) throw signal.reason
}

function exactRecord(value: unknown, expectedKeys: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    runtimeFail('RUNTIME_REQUEST_INVALID')
  }
  const result = value as Record<string, unknown>
  const actual = Object.keys(result).sort()
  const expected = [...expectedKeys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    runtimeFail('RUNTIME_REQUEST_INVALID')
  }
  return result
}

function operation(value: unknown): MacosRuntimeOperation {
  if (
    value !== 'setup'
    && value !== 'start'
    && value !== 'stop'
    && value !== 'repair'
    && value !== 'uninstall-preserve'
    && value !== 'uninstall-delete'
  ) runtimeFail('RUNTIME_REQUEST_INVALID')
  return value
}

function parseRequest(value: unknown): RuntimeRequest {
  const input = exactRecord(value, ['schema_version', 'operation', 'offline'])
  if (input.schema_version !== 1 || typeof input.offline !== 'boolean') {
    runtimeFail('RUNTIME_REQUEST_INVALID')
  }
  return {
    schema_version: 1,
    operation: operation(input.operation),
    offline: input.offline,
  }
}

function parseApplyRequest(value: unknown): RuntimeApplyRequest {
  const input = exactRecord(value, [
    'schema_version',
    'operation',
    'offline',
    'plan_revision',
    'confirmation',
  ])
  if (
    input.schema_version !== 1
    || typeof input.offline !== 'boolean'
    || typeof input.plan_revision !== 'string'
    || !/^[0-9a-f]{64}$/.test(input.plan_revision)
    || typeof input.confirmation !== 'string'
    || input.confirmation.length > 64
  ) runtimeFail('RUNTIME_REQUEST_INVALID')
  return {
    schema_version: 1,
    operation: operation(input.operation),
    offline: input.offline,
    plan_revision: input.plan_revision,
    confirmation: input.confirmation,
  }
}

function sha256(bytes: Buffer | string): string {
  return createHash('sha256').update(bytes).digest('hex')
}

/** Compares a downloaded artifact digest against policy without timing-dependent equality. */
function digestMatches(bytes: Buffer, expected: string): boolean {
  return timingSafeEqual(
    createHash('sha256').update(bytes).digest(),
    Buffer.from(expected, 'hex'),
  )
}

function missing(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as NodeJS.ErrnoException).code === 'ENOENT'
}

async function regularFile(path: string): Promise<boolean> {
  try {
    const status = await lstat(path)
    return status.isFile() && !status.isSymbolicLink()
  } catch (error) {
    if (missing(error)) return false
    throw error
  }
}

async function partialDownloadOffset(path: string, expectedSize: number): Promise<number> {
  const status = await lstat(path).catch((error: unknown) => {
    if (missing(error)) return undefined
    throw error
  })
  if (status === undefined) return 0
  if (!status.isFile() || status.isSymbolicLink() || status.nlink !== 1) {
    runtimeFail('RUNTIME_STORAGE_UNSAFE')
  }
  if (status.size > expectedSize) runtimeFail('RUNTIME_ARTIFACT_INTEGRITY')
  if (status.size === 0) {
    await unlink(path)
    return 0
  }
  return status.size
}

async function verifiedBytes(path: string, size: number, digest: string): Promise<Buffer | undefined> {
  if (!await regularFile(path)) return undefined
  const status = await lstat(path)
  if (status.size !== size) return undefined
  const bytes = await readFile(path)
  return digestMatches(bytes, digest) ? bytes : undefined
}

async function verifiedFile(
  path: string,
  size: number,
  sha256: string,
  sha512?: string,
): Promise<boolean> {
  if (!await regularFile(path)) return false
  const status = await lstat(path)
  if (status.size !== size) return false
  const hash256 = createHash('sha256')
  const hash512 = sha512 === undefined ? undefined : createHash('sha512')
  try {
    for await (const chunk of createReadStream(path)) {
      hash256.update(chunk as Buffer)
      hash512?.update(chunk as Buffer)
    }
  } catch {
    return false
  }
  const valid256 = timingSafeEqual(hash256.digest(), Buffer.from(sha256, 'hex'))
  const expectedSha512 = sha512
  const valid512 = hash512 === undefined || expectedSha512 === undefined
    || timingSafeEqual(hash512.digest(), Buffer.from(expectedSha512, 'hex'))
  return valid256 && valid512
}

/** Restricts runtime ownership to the normalized dedicated macOS ARM64 storage root. */
function safeRoot(rootDirectory: string): string {
  if (
    !isAbsolute(rootDirectory)
    || rootDirectory.includes('\0')
    || rootDirectory.includes('\n')
    || rootDirectory.includes('\r')
    || resolve(rootDirectory) !== rootDirectory
    || rootDirectory === sep
    || rootDirectory === homedir()
    || !rootDirectory.endsWith(`${sep}macos-arm64-runtime`)
  ) runtimeFail('RUNTIME_STORAGE_UNSAFE')
  return rootDirectory
}

/** Rejects symlinks and boundary escapes anywhere in a runtime storage ancestry. */
async function safeDirectory(path: string, boundary: string = path): Promise<void> {
  const resolvedPath = resolve(path)
  const resolvedBoundary = resolve(boundary)
  if (
    resolvedPath !== resolvedBoundary
    && !resolvedPath.startsWith(`${resolvedBoundary}${sep}`)
  ) runtimeFail('RUNTIME_STORAGE_UNSAFE')
  let ancestor = resolve(path)
  const ancestry: string[] = []
  while (true) {
    ancestry.push(ancestor)
    if (ancestor === resolvedBoundary) break
    const parent = dirname(ancestor)
    if (parent === ancestor) runtimeFail('RUNTIME_STORAGE_UNSAFE')
    ancestor = parent
  }
  for (const candidate of ancestry.reverse()) {
    const status = await lstat(candidate).catch((error: unknown) => {
      if (missing(error)) return undefined
      throw error
    })
    if (status?.isSymbolicLink()) runtimeFail('RUNTIME_STORAGE_UNSAFE')
  }
  await mkdir(path, { recursive: true, mode: 0o700 })
  const status = await lstat(path)
  if (!status.isDirectory() || status.isSymbolicLink()) runtimeFail('RUNTIME_STORAGE_UNSAFE')
  await chmod(path, 0o700)
}

async function existingDirectoryAtOrAbove(path: string): Promise<string> {
  let candidate = resolve(path)
  while (true) {
    const status = await lstat(candidate).catch((error: unknown) => {
      if (missing(error)) return undefined
      throw error
    })
    if (status !== undefined) {
      if (!status.isDirectory() || status.isSymbolicLink()) runtimeFail('RUNTIME_STORAGE_UNSAFE')
      return candidate
    }
    const parent = dirname(candidate)
    if (parent === candidate) runtimeFail('RUNTIME_STORAGE_UNSAFE')
    candidate = parent
  }
}

/** Persists runtime ownership evidence through a private temporary file and atomic rename. */
async function atomicJson(path: string, value: unknown): Promise<void> {
  const temporary = `${path}.${randomUUID()}.tmp`
  const serialized = `${JSON.stringify(value)}\n`
  let handle
  try {
    handle = await open(temporary, 'wx', 0o600)
    await handle.writeFile(serialized, 'utf8')
    await handle.sync()
    await handle.close()
    handle = undefined
    await rename(temporary, path)
  } finally {
    await handle?.close()
    await unlink(temporary).catch((error: unknown) => {
      if (!missing(error)) throw error
    })
  }
}

function allocation(policy: MacosArm64RuntimePolicy, host: MacosRuntimeHostInspection) {
  const cpus = Math.min(
    policy.resources.maximumCpus,
    Math.max(policy.resources.minimumCpus, Math.floor(host.logicalCpus / 2)),
  )
  const hostMemoryGib = Math.max(0, Math.floor(host.totalMemoryBytes / GIB))
  const memoryGib = Math.min(
    policy.resources.maximumMemoryGib,
    Math.max(policy.resources.minimumMemoryGib, Math.floor(hostMemoryGib / 2)),
  )
  return { cpus, memory_gib: memoryGib, disk_gib: policy.resources.diskGib }
}

function supported(
  policy: MacosArm64RuntimePolicy,
  hostPort: MacosRuntimeHost,
  inspection: MacosRuntimeHostInspection,
): boolean {
  return hostCapable(policy, hostPort, inspection)
    && inspection.freeBytes >= policy.target.minimumFreeGib * GIB
}

function hostCapable(
  policy: MacosArm64RuntimePolicy,
  hostPort: MacosRuntimeHost,
  inspection: MacosRuntimeHostInspection,
): boolean {
  return hostPort.platform === policy.target.platform
    && hostPort.architecture === policy.target.architecture
    && inspection.macosMajor >= policy.target.minimumMacosMajor
    && inspection.virtualizationAvailable
    && inspection.logicalCpus >= policy.resources.minimumCpus
    && inspection.totalMemoryBytes >= policy.resources.minimumMemoryGib * GIB
}

function installationFootprint(policy: MacosArm64RuntimePolicy): number {
  return INSTALLATION_OVERHEAD_BYTES
    + policy.vmImage.sizeBytes
    + policy.components.reduce((total, component) => total
      + component.artifact.sizeBytes
      + component.license.sizeBytes
      + component.license.sizeBytes
      + component.artifact.install.reduce((installed, entry) => installed + entry.sizeBytes, 0), 0)
}

function statusHost(
  policy: MacosArm64RuntimePolicy,
  hostPort: MacosRuntimeHost,
  inspection: MacosRuntimeHostInspection,
) {
  return {
    operating_system: hostPort.platform === 'darwin' ? 'macos' as const : 'unsupported' as const,
    architecture: hostPort.architecture,
    macos_major: inspection.macosMajor,
    virtualization: inspection.virtualizationAvailable ? 'available' as const : 'unavailable' as const,
    free_space: inspection.freeBytes >= policy.target.minimumFreeGib * GIB
      ? 'sufficient' as const
      : 'insufficient' as const,
    existing_docker_contexts: inspection.existingDockerContexts,
  }
}

/** Accepts ownership evidence only when its exact schema and policy identities are valid. */
function parseOwnership(value: unknown, policy: MacosArm64RuntimePolicy): OwnershipMarker | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return undefined
  const input = value as Record<string, unknown>
  const keys = Object.keys(input).sort()
  const expected = ['context', 'installed_at', 'policy_sha256', 'profile', 'schema_version'].sort()
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return undefined
  if (
    input.schema_version !== 1
    || typeof input.policy_sha256 !== 'string'
    || !/^[0-9a-f]{64}$/.test(input.policy_sha256)
    || input.profile !== policy.ownership.profile
    || input.context !== policy.ownership.context
    || typeof input.installed_at !== 'string'
    || Number.isNaN(Date.parse(input.installed_at))
  ) return undefined
  return input as unknown as OwnershipMarker
}

function validEngineId(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 1 || value.length > 256) return false
  return [...value].every((character) => {
    const codePoint = character.codePointAt(0)
    return codePoint !== undefined && codePoint >= 0x20 && codePoint !== 0x7f
  })
}

function parseEngineIdentity(
  value: unknown,
  policy: MacosArm64RuntimePolicy,
  endpoint: string,
): EngineIdentity | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return undefined
  const input = value as Record<string, unknown>
  const keys = Object.keys(input).sort()
  const expected = [
    'context',
    'endpoint',
    'engine_id',
    'policy_sha256',
    'profile',
    'schema_version',
  ].sort()
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) {
    return undefined
  }
  if (
    input.schema_version !== 1
    || input.policy_sha256 !== runtimePolicyDigest(policy)
    || input.profile !== policy.ownership.profile
    || input.context !== policy.ownership.context
    || input.endpoint !== endpoint
    || !validEngineId(input.engine_id)
  ) return undefined
  return input as unknown as EngineIdentity
}

/** Allows HTTPS redirects only within an origin or from GitHub to reviewed asset hosts. */
function allowedRedirect(source: URL, destination: URL): boolean {
  if (destination.protocol !== 'https:') return false
  if (source.origin === destination.origin) return true
  return source.hostname === 'github.com'
    && (
      destination.hostname === 'objects.githubusercontent.com'
      || destination.hostname === 'release-assets.githubusercontent.com'
    )
}

/** Starts one cancellable HTTPS artifact request with bounded resume semantics. */
function requestDownload(
  source: URL,
  offsetBytes: number,
  signal?: AbortSignal,
): Promise<IncomingMessage> {
  return new Promise((resolveResponse, reject) => {
    requireActive(signal)
    const headers: Record<string, string> = {
      Accept: 'application/octet-stream',
      'User-Agent': 'AncestryLLM-runtime-bootstrap/1',
    }
    if (offsetBytes > 0) headers.Range = `bytes=${offsetBytes}-`
    let response: IncomingMessage | undefined
    const cleanup = (): void => signal?.removeEventListener('abort', abort)
    const abort = (): void => {
      const error = signal?.reason instanceof Error
        ? signal.reason
        : new Error('RUNTIME_DOWNLOAD_CANCELLED')
      response?.destroy(error)
      request.destroy(error)
    }
    const request = httpsRequest(source, {
      headers,
      method: 'GET',
    }, (value) => {
      response = value
      value.once('close', cleanup)
      value.once('end', cleanup)
      resolveResponse(value)
    })
    request.setTimeout(30_000, () => request.destroy(new Error('RUNTIME_DOWNLOAD_TIMEOUT')))
    request.once('error', (error) => {
      cleanup()
      reject(error)
    })
    signal?.addEventListener('abort', abort, { once: true })
    request.end()
    if (signal?.aborted) abort()
  })
}

/** Follows a bounded chain of allowlisted HTTPS redirects for a runtime artifact. */
async function downloadResponse(
  sourceUrl: string,
  offsetBytes: number,
  signal?: AbortSignal,
): Promise<IncomingMessage> {
  requireActive(signal)
  let current = new URL(sourceUrl)
  if (current.protocol !== 'https:' || current.username !== '' || current.password !== '') {
    runtimeFail('RUNTIME_DOWNLOAD_FAILED')
  }
  for (let redirect = 0; redirect <= 5; redirect += 1) {
    const response = await requestDownload(current, offsetBytes, signal)
    requireActive(signal)
    const status = response.statusCode ?? 0
    if (![301, 302, 303, 307, 308].includes(status)) return response
    const location = response.headers.location
    response.destroy()
    if (location === undefined) runtimeFail('RUNTIME_DOWNLOAD_FAILED')
    const destination = new URL(location, current)
    if (!allowedRedirect(current, destination)) runtimeFail('RUNTIME_DOWNLOAD_FAILED')
    current = destination
  }
  return runtimeFail('RUNTIME_DOWNLOAD_FAILED')
}

/**
 * Downloads or resumes the exact pinned artifact while enforcing status, size, cancellation, and fsync.
 */
export const downloadPinnedRuntimeFile: DownloadRuntimeFile = async ({
  sourceUrl,
  targetPath,
  offsetBytes,
  expectedSizeBytes,
  signal,
}) => {
  try {
    requireActive(signal)
    const response = await downloadResponse(sourceUrl, offsetBytes, signal)
    try {
      if (
        (offsetBytes === 0 && response.statusCode !== 200)
        || (offsetBytes > 0 && response.statusCode !== 206)
      ) runtimeFail('RUNTIME_DOWNLOAD_FAILED')
      const declaredLength = response.headers['content-length']
      if (
        declaredLength !== undefined
        && Number.parseInt(declaredLength, 10) !== expectedSizeBytes - offsetBytes
      ) runtimeFail('RUNTIME_DOWNLOAD_FAILED')

      const flags = constants.O_WRONLY
        | constants.O_NOFOLLOW
        | (offsetBytes === 0
          ? constants.O_CREAT | constants.O_EXCL
          : constants.O_APPEND)
      const handle = await open(targetPath, flags, 0o600)
      let received = 0
      try {
        const status = await handle.stat()
        if (!status.isFile() || status.nlink !== 1 || status.size !== offsetBytes) {
          runtimeFail('RUNTIME_STORAGE_UNSAFE')
        }
        for await (const value of response) {
          requireActive(signal)
          const bytes = Buffer.isBuffer(value) ? value : Buffer.from(value as Uint8Array)
          received += bytes.length
          if (offsetBytes + received > expectedSizeBytes) runtimeFail('RUNTIME_DOWNLOAD_FAILED')
          await handle.write(bytes)
        }
        requireActive(signal)
        await handle.sync()
      } finally {
        await handle.close()
      }
      if (offsetBytes + received !== expectedSizeBytes) runtimeFail('RUNTIME_DOWNLOAD_FAILED')
    } finally {
      response.destroy()
    }
  } catch (error) {
    requireActive(signal)
    if (error instanceof MacosRuntimeError) throw error
    runtimeFail('RUNTIME_DOWNLOAD_FAILED')
  }
}

/**
 * Creates the macOS host adapter that invokes fixed system tools through the bounded process runner.
 */
export function createMacosRuntimeHost(
  storageDirectory: string,
  runProcess: RunHostProcess = runBoundedHostProcess,
): MacosRuntimeHost {
  const inspect = async (signal?: AbortSignal): Promise<MacosRuntimeHostInspection> => {
    requireActive(signal)
    const workingDirectory = await existingDirectoryAtOrAbove(storageDirectory)
    const [macos, virtualization, filesystem, contexts] = await Promise.all([
      runProcess({
        executablePath: '/usr/bin/sw_vers',
        arguments: ['-productVersion'],
        workingDirectory,
        environment: {},
        standardInput: undefined,
        timeoutMs: 10_000,
        maxInputBytes: 1,
        maxOutputBytes: 1024,
        ...(signal === undefined ? {} : { signal }),
      }),
      runProcess({
        executablePath: '/usr/sbin/sysctl',
        arguments: ['-n', 'kern.hv_support'],
        workingDirectory,
        environment: {},
        standardInput: undefined,
        timeoutMs: 10_000,
        maxInputBytes: 1,
        maxOutputBytes: 1024,
        ...(signal === undefined ? {} : { signal }),
      }),
      statfs(workingDirectory, { bigint: true }),
      readdir(join(homedir(), '.docker', 'contexts', 'meta'), { withFileTypes: true })
        .then((entries) => entries.filter((entry) => entry.isDirectory() && !entry.isSymbolicLink()).length)
        .catch((error: unknown) => {
          if (missing(error)) return 0
          throw error
        }),
    ])
    requireActive(signal)
    const major = Number.parseInt(macos.stdout.trim().split('.')[0] ?? '', 10)
    if (!Number.isSafeInteger(major) || major < 1) runtimeFail('RUNTIME_HOST_UNSUPPORTED')
    const freeBytes = Number(filesystem.bavail * filesystem.bsize)
    if (!Number.isSafeInteger(freeBytes) || freeBytes < 0) runtimeFail('RUNTIME_HOST_UNSUPPORTED')
    return {
      macosMajor: major,
      virtualizationAvailable: virtualization.stdout.trim() === '1',
      logicalCpus: availableParallelism(),
      totalMemoryBytes: totalmem(),
      freeBytes,
      existingDockerContexts: contexts,
    }
  }
  return { platform: process.platform, architecture: process.arch, inspect }
}

/**
 * Owns macos arm64 runtime manager state transitions while enforcing reviewed macOS ARM64 runtime artifact installation.
 */
export class MacosArm64RuntimeManager {
  private readonly root: string
  private readonly storageBoundary: string
  private readonly policy: MacosArm64RuntimePolicy
  private readonly host: MacosRuntimeHost
  private readonly download: DownloadRuntimeFile
  private readonly runProcess: RunHostProcess
  private readonly now: () => Date
  private pending: Promise<void> = Promise.resolve()

  constructor(options: RuntimeManagerOptions) {
    this.root = safeRoot(options.rootDirectory)
    this.storageBoundary = dirname(dirname(this.root))
    this.policy = options.policy
    this.host = options.host
    this.download = options.download ?? downloadPinnedRuntimeFile
    this.runProcess = options.runProcess ?? runBoundedHostProcess
    this.now = options.now ?? (() => new Date())
  }

  status(signal?: AbortSignal): Promise<MacosRuntimeStatus> {
    return this.serialized(() => this.inspectStatus(signal), signal)
  }

  preview(value: unknown, signal?: AbortSignal): Promise<MacosRuntimePreview> {
    const request = parseRequest(value)
    return this.serialized(() => this.createPreview(request, signal), signal)
  }

  apply(value: unknown, signal?: AbortSignal): Promise<MacosRuntimeResult> {
    const request = parseApplyRequest(value)
    return this.serialized(async () => {
      requireActive(signal)
      const previewSnapshot = await this.createPreviewSnapshot(request, signal)
      const { preview } = previewSnapshot
      if (preview.plan_revision !== request.plan_revision) runtimeFail('RUNTIME_PLAN_STALE')
      if (preview.confirmation_phrase !== request.confirmation) {
        runtimeFail('RUNTIME_CONFIRMATION_REQUIRED')
      }
      switch (request.operation) {
        case 'setup':
        case 'repair':
          await this.install(request.offline, signal)
          {
            const integrity = await this.inspectIntegrity(signal)
            const ownership = await this.readOwnership(signal)
            await this.startRuntime(integrity, ownership, signal)
            await this.verifyHealth(integrity, ownership, true, signal)
          }
          await this.writeReceipt(request.operation, signal)
          return { schema_version: 1, operation: request.operation, state: 'ready', code: 'RUNTIME_READY' }
        case 'start':
          this.requireInstalled(previewSnapshot.integrity, previewSnapshot.ownership)
          await this.startRuntime(
            previewSnapshot.integrity,
            previewSnapshot.ownership,
            signal,
          )
          await this.verifyHealth(
            previewSnapshot.integrity,
            previewSnapshot.ownership,
            false,
            signal,
          )
          return { schema_version: 1, operation: request.operation, state: 'ready', code: 'RUNTIME_READY' }
        case 'stop':
          this.requireLifecycleInstalled(previewSnapshot.integrity, previewSnapshot.ownership)
          await this.runColima(['stop', '--profile', this.policy.ownership.profile], signal)
          return { schema_version: 1, operation: request.operation, state: 'stopped', code: 'RUNTIME_STOPPED' }
        case 'uninstall-preserve':
        case 'uninstall-delete':
          await this.uninstall(
            request.operation === 'uninstall-delete',
            previewSnapshot.integrity,
            previewSnapshot.ownership,
            signal,
          )
          return { schema_version: 1, operation: request.operation, state: 'not-installed', code: 'RUNTIME_REMOVED' }
      }
    }, signal)
  }

  private serialized<T>(operation: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    const run = (): Promise<T> => {
      requireActive(signal)
      return operation()
    }
    const result = this.pending.then(run, run)
    this.pending = result.then(() => undefined, () => undefined)
    return result
  }

  private async inspectStatus(signal?: AbortSignal): Promise<MacosRuntimeStatus> {
    return (await this.inspectStatusSnapshot(signal)).status
  }

  private async inspectIntegrity(signal?: AbortSignal): Promise<RuntimeIntegrity> {
    const components = await Promise.all(this.policy.components.map(async (component) => ({
      name: component.name,
      version: component.version,
      installed: await this.componentInstalled(component, signal),
    })))
    return {
      components,
      vmImageInstalled: await this.vmImageInstalled(signal),
    }
  }

  private async inspectStatusSnapshot(signal?: AbortSignal): Promise<RuntimeStatusSnapshot> {
    requireActive(signal)
    const inspection = this.host.platform === this.policy.target.platform
      && this.host.architecture === this.policy.target.architecture
      ? await this.host.inspect(signal)
      : {
          macosMajor: 0,
          virtualizationAvailable: false,
          logicalCpus: 0,
          totalMemoryBytes: 0,
          freeBytes: 0,
          existingDockerContexts: 0,
        }
    const isSupported = supported(this.policy, this.host, inspection)
    const integrity = await this.inspectIntegrity(signal)
    const ownership = await this.readOwnership(signal)
    let state: MacosRuntimeStatus['state'] = 'not-installed'
    let code = 'RUNTIME_NOT_INSTALLED'
    if (ownership.kind === 'invalid') {
      state = 'unhealthy'
      code = 'RUNTIME_OWNERSHIP_INVALID'
    } else if (ownership.kind === 'valid') {
      if (
        ownership.currentPolicy
        && integrity.components.every((component) => component.installed)
        && integrity.vmImageInstalled
      ) {
        try {
          await this.verifyHealth(integrity, ownership, false, signal)
          state = 'ready'
          code = 'RUNTIME_READY'
        } catch (error) {
          requireActive(signal)
          if (
            error instanceof MacosRuntimeError
            && error.code === 'RUNTIME_COMPONENT_INTEGRITY'
          ) {
            state = 'unhealthy'
            code = 'RUNTIME_COMPONENT_INTEGRITY'
          } else {
            state = 'stopped'
            code = 'RUNTIME_STOPPED'
          }
        }
      } else {
        state = 'unhealthy'
        code = 'RUNTIME_COMPONENT_INTEGRITY'
      }
    }
    const status: MacosRuntimeStatus = {
      schema_version: 1,
      state,
      code,
      supported: isSupported,
      host: statusHost(this.policy, this.host, inspection),
      allocation: allocation(this.policy, inspection),
      components: integrity.components,
      vm_image: {
        version: this.policy.vmImage.version,
        installed: integrity.vmImageInstalled,
      },
    }
    return { status, inspection, integrity, ownership }
  }

  private async createPreview(
    request: RuntimeRequest,
    signal?: AbortSignal,
  ): Promise<MacosRuntimePreview> {
    return (await this.createPreviewSnapshot(request, signal)).preview
  }

  private async createPreviewSnapshot(
    request: RuntimeRequest,
    signal?: AbortSignal,
  ): Promise<RuntimePreviewSnapshot> {
    const snapshot = await this.inspectStatusSnapshot(signal)
    const { status, inspection } = snapshot
    if (request.operation === 'setup' || request.operation === 'repair') {
      const requiredFreeBytes = this.policy.target.minimumFreeGib * GIB
        + installationFootprint(this.policy)
      if (
        !hostCapable(this.policy, this.host, inspection)
        || inspection.freeBytes < requiredFreeBytes
      ) runtimeFail('RUNTIME_HOST_UNSUPPORTED')
    } else if (request.operation === 'start' && !status.supported) {
      runtimeFail('RUNTIME_HOST_UNSUPPORTED')
    }
    const review: MacosRuntimePreview['review'] = {
      artifacts: this.policy.components.map((component) => ({
        name: component.name,
        version: component.version,
        repository: component.repository,
        asset_name: component.artifact.assetName,
        source_url: component.artifact.url,
        sha256: component.artifact.sha256,
        size_bytes: component.artifact.sizeBytes,
        license: component.license.spdxId,
        license_url: component.license.url,
        license_sha256: component.license.sha256,
      })),
      vm_image: {
        version: this.policy.vmImage.version,
        repository: this.policy.vmImage.repository,
        asset_name: this.policy.vmImage.assetName,
        source_url: this.policy.vmImage.url,
        sha256: this.policy.vmImage.sha256,
        size_bytes: this.policy.vmImage.sizeBytes,
      },
      ownership: {
        profile: this.policy.ownership.profile,
        context: this.policy.ownership.context,
      },
      isolation: {
        loopback_only: true,
        kubernetes: false,
        privileged_containers: false,
        renderer_socket_access: false,
        container_socket_access: false,
        cross_profile_socket_access: false,
      },
    }
    const stable = {
      schema_version: 1,
      policy_sha256: runtimePolicyDigest(this.policy),
      operation: request.operation,
      offline: request.offline,
      actions: ACTIONS[request.operation],
      status,
      review,
    }
    const preview: MacosRuntimePreview = {
      schema_version: 1,
      operation: request.operation,
      offline: request.offline,
      actions: ACTIONS[request.operation].map((code) => ({ code })),
      confirmation_phrase: CONFIRMATIONS[request.operation],
      preserves_data: request.operation !== 'uninstall-delete',
      deletes_data: request.operation === 'uninstall-delete',
      plan_revision: sha256(JSON.stringify(stable)),
      status,
      review,
    }
    return { ...snapshot, preview }
  }

  private async ensureRoot(signal?: AbortSignal): Promise<void> {
    requireActive(signal)
    await safeDirectory(this.root, this.storageBoundary)
    for (const child of ['downloads', 'data', 'home', 'colima', 'lima']) {
      requireActive(signal)
      await safeDirectory(join(this.root, child), this.root)
    }
  }

  private async acquire(
    component: RuntimeComponentPolicy,
    kind: 'artifact' | 'license',
    offline: boolean,
    signal?: AbortSignal,
  ): Promise<Buffer> {
    requireActive(signal)
    const item = component[kind]
    const downloads = join(this.root, 'downloads')
    const finalPath = join(downloads, `${component.name}.${kind}`)
    const partPath = `${finalPath}.part`
    const cached = await verifiedBytes(finalPath, item.sizeBytes, item.sha256)
    if (cached !== undefined) return cached

    let offset = await partialDownloadOffset(partPath, item.sizeBytes)
    if (offset === item.sizeBytes) {
      const complete = await verifiedBytes(partPath, item.sizeBytes, item.sha256)
      if (complete !== undefined) {
        await rename(partPath, finalPath)
        return complete
      }
      if (offline) runtimeFail('RUNTIME_ARTIFACT_INTEGRITY')
      requireActive(signal)
      await rm(partPath, { force: true })
      offset = 0
    }
    if (offline) runtimeFail('RUNTIME_OFFLINE_UNAVAILABLE')
    await this.download({
      sourceUrl: item.url,
      targetPath: partPath,
      offsetBytes: offset,
      expectedSizeBytes: item.sizeBytes,
      ...(signal === undefined ? {} : { signal }),
    })
    requireActive(signal)
    const complete = await verifiedBytes(partPath, item.sizeBytes, item.sha256)
    if (complete === undefined) {
      await rm(partPath, { force: true })
      runtimeFail('RUNTIME_ARTIFACT_INTEGRITY')
    }
    await rename(partPath, finalPath)
    return complete
  }

  private async install(offline: boolean, signal?: AbortSignal): Promise<void> {
    await this.ensureRoot(signal)
    const verified: Array<Readonly<{
      component: RuntimeComponentPolicy
      artifact: Buffer
      license: Buffer
    }>> = []
    for (const component of this.policy.components) {
      requireActive(signal)
      const artifact = await this.acquire(component, 'artifact', offline, signal)
      const license = await this.acquire(component, 'license', offline, signal)
      verified.push({ component, artifact, license })
    }
    await this.acquireVmImage(this.policy.vmImage, offline, signal)

    const staging = await mkdtemp(join(this.root, '.install-'))
    await chmod(staging, 0o700)
    try {
      for (const { component, artifact, license } of verified) {
        requireActive(signal)
        if (component.artifact.archiveFormat === 'tar.gz') {
          await extractReviewedTarGzip(
            artifact,
            component.artifact.install,
            component.artifact.excludedMembers,
            staging,
          )
        } else {
          await this.installBinary(artifact, component.artifact.install[0]!, staging)
        }
        await this.installLicense(component, license, staging)
      }
      requireActive(signal)
      const tools = join(this.root, 'tools')
      const previous = join(this.root, '.tools-previous')
      await rm(previous, { recursive: true, force: true })
      if (await this.directoryExists(tools)) await rename(tools, previous)
      try {
        await rename(staging, tools)
      } catch (error) {
        if (await this.directoryExists(previous)) await rename(previous, tools)
        throw error
      }
      await rm(previous, { recursive: true, force: true })
    } finally {
      await rm(staging, { recursive: true, force: true })
    }

    requireActive(signal)
    const marker: OwnershipMarker = {
      schema_version: 1,
      policy_sha256: runtimePolicyDigest(this.policy),
      profile: this.policy.ownership.profile,
      context: this.policy.ownership.context,
      installed_at: this.now().toISOString(),
    }
    await atomicJson(join(this.root, MARKER_FILE), marker)
    await rm(join(this.root, ENGINE_IDENTITY_FILE), { force: true })
  }

  private async installBinary(
    bytes: Buffer,
    entry: RuntimeInstallEntry,
    staging: string,
  ): Promise<void> {
    if (bytes.length !== entry.sizeBytes || !digestMatches(bytes, entry.sha256)) {
      runtimeFail('RUNTIME_ARTIFACT_INTEGRITY')
    }
    const destination = resolve(staging, ...entry.installPath.split('/'))
    if (!destination.startsWith(`${staging}${sep}`)) runtimeFail('RUNTIME_STORAGE_UNSAFE')
    await safeDirectory(dirname(destination), staging)
    await writeFile(destination, bytes, { mode: entry.executable ? 0o700 : 0o600, flag: 'wx' })
    await chmod(destination, entry.executable ? 0o700 : 0o600)
  }

  private async installLicense(
    component: RuntimeComponentPolicy,
    bytes: Buffer,
    staging: string,
  ): Promise<void> {
    if (
      bytes.length !== component.license.sizeBytes
      || !digestMatches(bytes, component.license.sha256)
    ) runtimeFail('RUNTIME_ARTIFACT_INTEGRITY')
    const licenses = join(staging, 'licenses')
    await safeDirectory(licenses, staging)
    const destination = join(licenses, `${component.name}.LICENSE`)
    await writeFile(destination, bytes, { mode: 0o600, flag: 'wx' })
    await chmod(destination, 0o600)
  }

  private async componentInstalled(
    component: RuntimeComponentPolicy,
    signal?: AbortSignal,
  ): Promise<boolean> {
    for (const entry of component.artifact.install) {
      requireActive(signal)
      if (await verifiedBytes(join(this.root, 'tools', ...entry.installPath.split('/')), entry.sizeBytes, entry.sha256) === undefined) {
        return false
      }
    }
    return true
  }

  private async readOwnership(signal?: AbortSignal): Promise<OwnershipState> {
    requireActive(signal)
    const markerPath = join(this.root, MARKER_FILE)
    const markerStatus = await lstat(markerPath).catch((error: unknown) => {
      if (missing(error)) return undefined
      throw error
    })
    if (markerStatus === undefined) return { kind: 'missing' }
    if (!markerStatus.isFile() || markerStatus.isSymbolicLink()) return { kind: 'invalid' }
    try {
      requireActive(signal)
      const marker = parseOwnership(JSON.parse(await readFile(markerPath, 'utf8')), this.policy)
      return marker === undefined
        ? { kind: 'invalid' }
        : {
            kind: 'valid',
            marker,
            currentPolicy: marker.policy_sha256 === runtimePolicyDigest(this.policy),
          }
    } catch {
      requireActive(signal)
      return { kind: 'invalid' }
    }
  }

  private requireInstalled(integrity: RuntimeIntegrity, ownership: OwnershipState): void {
    if (ownership.kind === 'missing') runtimeFail('RUNTIME_NOT_INSTALLED')
    if (ownership.kind === 'invalid') runtimeFail('RUNTIME_OWNERSHIP_INVALID')
    if (!ownership.currentPolicy) runtimeFail('RUNTIME_COMPONENT_INTEGRITY')
    if (
      !integrity.components.every((component) => component.installed)
      || !integrity.vmImageInstalled
    ) runtimeFail('RUNTIME_COMPONENT_INTEGRITY')
  }

  private requireLifecycleInstalled(
    integrity: RuntimeIntegrity,
    ownership: OwnershipState,
  ): void {
    if (ownership.kind === 'missing') runtimeFail('RUNTIME_NOT_INSTALLED')
    if (ownership.kind === 'invalid') runtimeFail('RUNTIME_OWNERSHIP_INVALID')
    for (const name of ['colima', 'lima']) {
      const component = integrity.components.find((candidate) => candidate.name === name)
      if (component?.installed !== true) runtimeFail('RUNTIME_COMPONENT_INTEGRITY')
    }
  }

  private async acquireVmImage(
    image: RuntimeVmImagePolicy,
    offline: boolean,
    signal?: AbortSignal,
  ): Promise<void> {
    requireActive(signal)
    const finalPath = join(this.root, 'downloads', 'vm-image.artifact')
    const partPath = `${finalPath}.part`
    if (await verifiedFile(finalPath, image.sizeBytes, image.sha256, image.sha512)) return

    let offset = await partialDownloadOffset(partPath, image.sizeBytes)
    if (offset === image.sizeBytes) {
      if (await verifiedFile(partPath, image.sizeBytes, image.sha256, image.sha512)) {
        await rename(partPath, finalPath)
        return
      }
      if (offline) runtimeFail('RUNTIME_ARTIFACT_INTEGRITY')
      requireActive(signal)
      await rm(partPath, { force: true })
      offset = 0
    }
    if (offline) runtimeFail('RUNTIME_OFFLINE_UNAVAILABLE')
    await this.download({
      sourceUrl: image.url,
      targetPath: partPath,
      offsetBytes: offset,
      expectedSizeBytes: image.sizeBytes,
      ...(signal === undefined ? {} : { signal }),
    })
    requireActive(signal)
    if (!await verifiedFile(partPath, image.sizeBytes, image.sha256, image.sha512)) {
      await rm(partPath, { force: true })
      runtimeFail('RUNTIME_ARTIFACT_INTEGRITY')
    }
    await rename(partPath, finalPath)
  }

  private vmImageInstalled(signal?: AbortSignal): Promise<boolean> {
    requireActive(signal)
    const image = this.policy.vmImage
    return verifiedFile(
      join(this.root, 'downloads', 'vm-image.artifact'),
      image.sizeBytes,
      image.sha256,
      image.sha512,
    )
  }

  private environment(): NodeJS.ProcessEnv {
    return {
      PATH: `${join(this.root, 'tools', 'bin')}:/usr/bin:/bin:/usr/sbin:/sbin`,
      HOME: join(this.root, 'home'),
      COLIMA_HOME: join(this.root, 'colima'),
      LIMA_HOME: join(this.root, 'lima'),
      DOCKER_CONFIG: join(this.root, 'tools', 'docker-config'),
    }
  }

  private process(
    executablePath: string,
    arguments_: readonly string[],
    signal?: AbortSignal,
  ) {
    requireActive(signal)
    return this.runProcess({
      executablePath,
      arguments: arguments_,
      workingDirectory: this.root,
      environment: this.environment(),
      standardInput: undefined,
      timeoutMs: PROCESS_TIMEOUT_MS,
      maxInputBytes: 1,
      maxOutputBytes: PROCESS_OUTPUT_BYTES,
      ...(signal === undefined ? {} : { signal }),
    }).catch(() => {
      requireActive(signal)
      return runtimeFail('RUNTIME_PROCESS_FAILED')
    })
  }

  private runColima(arguments_: readonly string[], signal?: AbortSignal) {
    return this.process(join(this.root, 'tools', 'bin', 'colima'), arguments_, signal)
  }

  private async startRuntime(
    integrity: RuntimeIntegrity,
    ownership: OwnershipState,
    signal?: AbortSignal,
  ): Promise<void> {
    this.requireInstalled(integrity, ownership)
    const inspection = await this.host.inspect(signal)
    if (!supported(this.policy, this.host, inspection)) runtimeFail('RUNTIME_HOST_UNSUPPORTED')
    const selected = allocation(this.policy, inspection)
    await this.runColima([
      'start',
      '--profile', this.policy.ownership.profile,
      '--arch', 'aarch64',
      '--vm-type', 'vz',
      '--mount-type', 'virtiofs',
      '--cpus', String(selected.cpus),
      '--memory', String(selected.memory_gib),
      '--disk', String(selected.disk_gib),
      '--disk-image', join(this.root, 'downloads', 'vm-image.artifact'),
      '--runtime', 'docker',
      '--mount', 'none',
      '--kubernetes=false',
      '--network-address=false',
      '--network-host-addresses=false',
      '--activate=false',
      '--ssh-config=false',
      '--binfmt=false',
      '--vz-rosetta=false',
    ], signal)
  }

  private expectedEngineEndpoint(): string {
    return `unix://${join(
      this.root,
      'colima',
      this.policy.ownership.profile,
      'docker.sock',
    )}`
  }

  private async readEngineIdentity(signal?: AbortSignal): Promise<EngineIdentityState> {
    requireActive(signal)
    const identityPath = join(this.root, ENGINE_IDENTITY_FILE)
    const identityStatus = await lstat(identityPath).catch((error: unknown) => {
      if (missing(error)) return undefined
      throw error
    })
    if (identityStatus === undefined) return { kind: 'missing' }
    if (!identityStatus.isFile() || identityStatus.isSymbolicLink()) return { kind: 'invalid' }
    try {
      requireActive(signal)
      const identity = parseEngineIdentity(
        JSON.parse(await readFile(identityPath, 'utf8')),
        this.policy,
        this.expectedEngineEndpoint(),
      )
      return identity === undefined ? { kind: 'invalid' } : { kind: 'valid', identity }
    } catch {
      requireActive(signal)
      return { kind: 'invalid' }
    }
  }

  private async verifyHealth(
    integrity: RuntimeIntegrity,
    ownership: OwnershipState,
    bindEngineIdentity: boolean,
    signal?: AbortSignal,
  ): Promise<void> {
    this.requireInstalled(integrity, ownership)
    try {
      const colima = await this.runColima([
        'status',
        '--profile', this.policy.ownership.profile,
        '--json',
      ], signal)
      const colimaStatus = JSON.parse(colima.stdout) as Record<string, unknown>
      if (
        colimaStatus.status !== 'Running'
        || colimaStatus.arch !== 'aarch64'
        || colimaStatus.runtime !== 'docker'
      ) runtimeFail('RUNTIME_HEALTH_FAILED')

      const dockerPath = join(this.root, 'tools', 'bin', 'docker')
      const dockerConfig = join(this.root, 'tools', 'docker-config')
      const dockerContext = await this.process(dockerPath, [
        '--config', dockerConfig,
        'context',
        'inspect',
        this.policy.ownership.context,
        '--format', '{{json .}}',
      ], signal)
      const context = JSON.parse(dockerContext.stdout) as Record<string, unknown>
      const endpoints = context.Endpoints
      const dockerEndpoint = typeof endpoints === 'object' && endpoints !== null
        ? (endpoints as Record<string, unknown>).docker
        : undefined
      const endpoint = typeof dockerEndpoint === 'object' && dockerEndpoint !== null
        ? (dockerEndpoint as Record<string, unknown>).Host
        : undefined
      const expectedEndpoint = this.expectedEngineEndpoint()
      if (
        context.Name !== this.policy.ownership.context
        || endpoint !== expectedEndpoint
      ) runtimeFail('RUNTIME_HEALTH_FAILED')

      const docker = await this.process(dockerPath, [
        '--config', dockerConfig,
        '--context', this.policy.ownership.context,
        'info',
        '--format', '{{json .}}',
      ], signal)
      const engine = JSON.parse(docker.stdout) as Record<string, unknown>
      if (
        !validEngineId(engine.ID)
        || engine.OSType !== 'linux'
        || (engine.Architecture !== 'aarch64' && engine.Architecture !== 'arm64')
      ) runtimeFail('RUNTIME_HEALTH_FAILED')
      const engineIdentity = await this.readEngineIdentity(signal)
      if (engineIdentity.kind === 'invalid') runtimeFail('RUNTIME_HEALTH_FAILED')
      if (engineIdentity.kind === 'missing') {
        if (!bindEngineIdentity) runtimeFail('RUNTIME_HEALTH_FAILED')
        await atomicJson(join(this.root, ENGINE_IDENTITY_FILE), {
          schema_version: 1,
          policy_sha256: runtimePolicyDigest(this.policy),
          profile: this.policy.ownership.profile,
          context: this.policy.ownership.context,
          endpoint: expectedEndpoint,
          engine_id: engine.ID,
        } satisfies EngineIdentity)
      } else if (
        engineIdentity.identity.engine_id !== engine.ID
        || engineIdentity.identity.endpoint !== endpoint
      ) {
        runtimeFail('RUNTIME_HEALTH_FAILED')
      }
    } catch (error) {
      requireActive(signal)
      if (error instanceof MacosRuntimeError && error.code === 'RUNTIME_COMPONENT_INTEGRITY') throw error
      runtimeFail('RUNTIME_HEALTH_FAILED')
    }
  }

  private async uninstall(
    deleteData: boolean,
    integrity: RuntimeIntegrity,
    ownership: OwnershipState,
    signal?: AbortSignal,
  ): Promise<void> {
    this.requireLifecycleInstalled(integrity, ownership)
    await this.runColima([
      'delete',
      '--profile', this.policy.ownership.profile,
      '--force',
    ], signal)
    for (const child of [
      'tools',
      'home',
      'colima',
      'lima',
      'receipts',
      RECEIPT_FILE,
      ENGINE_IDENTITY_FILE,
    ]) {
      requireActive(signal)
      await rm(join(this.root, child), { recursive: true, force: true })
    }
    if (deleteData) {
      requireActive(signal)
      await rm(join(this.root, 'data'), { recursive: true, force: true })
      requireActive(signal)
      await rm(join(this.root, 'downloads'), { recursive: true, force: true })
    }
    requireActive(signal)
    await rm(join(this.root, MARKER_FILE), { force: true })
  }

  private async writeReceipt(
    operation_: MacosRuntimeOperation,
    signal?: AbortSignal,
  ): Promise<void> {
    requireActive(signal)
    await atomicJson(join(this.root, RECEIPT_FILE), {
      schema_version: 1,
      status: 'success',
      code: 'RUNTIME_VERIFIED',
      operation: operation_,
      policy_sha256: runtimePolicyDigest(this.policy),
      target: { platform: 'darwin', architecture: 'arm64' },
      ownership: this.policy.ownership,
      components: this.policy.components.map((component) => ({
        name: component.name,
        version: component.version,
        repository: component.repository,
        asset_name: component.artifact.assetName,
        sha256: component.artifact.sha256,
        license: component.license.spdxId,
        license_sha256: component.license.sha256,
      })),
      vm_image: {
        version: this.policy.vmImage.version,
        repository: this.policy.vmImage.repository,
        asset_name: this.policy.vmImage.assetName,
        sha256: this.policy.vmImage.sha256,
        sha512: this.policy.vmImage.sha512,
      },
      verified_at: this.now().toISOString(),
    })
  }

  private async directoryExists(path: string): Promise<boolean> {
    try {
      const status = await lstat(path)
      if (status.isSymbolicLink()) runtimeFail('RUNTIME_STORAGE_UNSAFE')
      return status.isDirectory()
    } catch (error) {
      if (missing(error)) return false
      throw error
    }
  }
}
