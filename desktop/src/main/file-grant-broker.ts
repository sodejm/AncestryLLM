/** Mediates native file selections through opaque, validated, single-use grants. */
import { constants, type Stats } from 'node:fs'
import {
  chmod,
  lstat,
  open,
  readdir,
  realpath,
  rename,
  unlink,
  type FileHandle,
} from 'node:fs/promises'
import { basename, dirname, extname, isAbsolute, join, normalize, resolve } from 'node:path'
import { createHash, randomBytes } from 'node:crypto'
import type {
  FileFormat,
  FileGrant,
  FileGrantAccess,
  FileGrantId,
  FileGrantPurpose,
  FileGrantRevocation,
  FileReadPurpose,
  FileValidation,
  FileWritePurpose,
  OpenFileGrantRequest,
  SaveFileGrantRequest,
} from '../shared-contract/desktop'

/**
 * Enumerates sanitized failures for selection, validation, redemption, and revocation of file grants.
 */
export type FileGrantFailureCode =
  | 'FILE_SELECTION_INVALID'
  | 'FILE_TOO_LARGE'
  | 'FILE_GRANT_FORBIDDEN'
  | 'FILE_GRANT_REVOKED'
  | 'FILE_GRANT_STALE'
  | 'FILE_GRANT_CONFLICT'
  | 'FILE_OPERATION_CANCELLED'
  | 'FILE_DIALOG_FAILED'

/**
 * Carries a stable filesystem-capability error code without exposing the underlying host path.
 */
export class FileGrantBrokerError extends Error {
  readonly code: FileGrantFailureCode

  constructor(code: FileGrantFailureCode) {
    super(code)
    this.name = 'FileGrantBrokerError'
    this.code = code
  }
}

/**
 * Defines trusted native-dialog operations that return paths only to the main-process grant broker.
 */
export interface NativeFileDialogPort {
  selectOpenFile(owner: object, purpose: FileReadPurpose, signal?: AbortSignal): Promise<string | null>
  selectSaveFile(
    owner: object,
    purpose: FileWritePurpose,
    suggestedName: string,
    signal?: AbortSignal,
  ): Promise<string | null>
  confirmReplacement(owner: object, displayName: string, signal?: AbortSignal): Promise<boolean>
}

/**
 * Resolves an opaque grant to a main-process-only path and its exact access and size bounds.
 */
export interface ResolvedFileGrant {
  readonly grantId: FileGrantId
  readonly purpose: FileGrantPurpose
  readonly access: FileGrantAccess
  readonly path: string
  readonly maxBytes: number
}

/** Reports a private staged input without exposing its main-process-only path. */
export interface StagedReadGrant {
  readonly grantId: FileGrantId
  readonly purpose: FileReadPurpose
  readonly sizeBytes: number
  readonly sha256: string
}

/** Reports an atomically published output without exposing its selected host path. */
export interface PublishedWriteGrant {
  readonly grantId: FileGrantId
  readonly purpose: FileWritePurpose
  readonly sizeBytes: number
  readonly sha256: string
  readonly durability: 'confirmed' | 'unconfirmed'
}

/** Abstracts the two commit operations so durability failures remain distinguishable. */
export interface FilePublicationPort {
  rename(source: string, destination: string): Promise<void>
  syncDirectory(path: string): Promise<void>
}

/** Reports a validated private output before publication. */
export interface ValidatedStagedOutput {
  readonly purpose: FileWritePurpose
  readonly sizeBytes: number
  readonly sha256: string
}

interface PurposePolicy {
  readonly access: FileGrantAccess
  readonly format: FileFormat
  readonly extensions: readonly string[]
  readonly maxBytes: number
}

interface Fingerprint {
  readonly dev: number
  readonly ino: number
  readonly mode: number
  readonly nlink: number
  readonly size: number
  readonly mtimeMs: number
  readonly ctimeMs: number
}

interface Binding {
  readonly owner: object
  readonly id: FileGrantId
  readonly purpose: FileGrantPurpose
  readonly access: FileGrantAccess
  readonly format: FileFormat
  readonly path: string
  readonly canonicalPath: string
  readonly maxBytes: number
  readonly fingerprint: Fingerprint | null
  readonly parentFingerprint: Fingerprint | null
  redeemed: boolean
}

interface InspectedStagedFile {
  readonly fingerprint: Fingerprint
  readonly canonicalPath: string
  readonly sizeBytes: number
  readonly sha256: string
}

const purposePolicies: Readonly<Record<FileGrantPurpose, PurposePolicy>> = Object.freeze({
  'gedcom-read': Object.freeze({ access: 'read', format: 'gedcom', extensions: ['.ged', '.gedcom'], maxBytes: 536_870_912 }),
  'rootsmagic-read': Object.freeze({ access: 'read', format: 'rootsmagic', extensions: ['.rmtree'], maxBytes: 8_589_934_592 }),
  'gedcom-write': Object.freeze({ access: 'write', format: 'gedcom', extensions: ['.ged'], maxBytes: 536_870_912 }),
  'json-write': Object.freeze({ access: 'write', format: 'json', extensions: ['.json'], maxBytes: 67_108_864 }),
  'markdown-write': Object.freeze({ access: 'write', format: 'markdown', extensions: ['.md'], maxBytes: 67_108_864 }),
})

// Control characters are intentionally rejected from user-visible file names.
// eslint-disable-next-line no-control-regex
const safeNamePattern = /^[^/\\\u0000-\u001f\u007f]{1,255}$/
const archiveSignatures = [
  Buffer.from([0x50, 0x4b, 0x03, 0x04]),
  Buffer.from([0x50, 0x4b, 0x05, 0x06]),
  Buffer.from([0x50, 0x4b, 0x07, 0x08]),
  Buffer.from([0x1f, 0x8b]),
  Buffer.from([0x42, 0x5a, 0x68]),
  Buffer.from([0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00]),
  Buffer.from([0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c]),
  Buffer.from([0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x00]),
  Buffer.from([0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x01, 0x00]),
  Buffer.from([0x28, 0xb5, 0x2f, 0xfd]),
] as const
const sqliteSignature = Buffer.from('SQLite format 3\0', 'ascii')
const grantPattern = /^grt_[a-f0-9]{64}$/

function fail(code: FileGrantFailureCode): never {
  throw new FileGrantBrokerError(code)
}

function checkSignal(signal?: AbortSignal): void {
  if (signal?.aborted) fail('FILE_OPERATION_CANCELLED')
}

function safeName(value: string): boolean {
  return safeNamePattern.test(value) && value !== '.' && value !== '..'
}

function collisionKey(value: string): string {
  return value.normalize('NFC').toLocaleLowerCase('en-US')
}

/** Normalizes a user-selected absolute path only after rejecting unsafe names and aliases. */
function selectedPath(value: string): string {
  if (!isAbsolute(value) || value.includes('\0') || normalize(value) !== value) {
    fail('FILE_SELECTION_INVALID')
  }
  const name = basename(value)
  if (!safeName(name)) fail('FILE_SELECTION_INVALID')
  return resolve(value)
}

function hasExpectedExtension(path: string, policy: PurposePolicy): boolean {
  return policy.extensions.includes(extname(path).toLocaleLowerCase('en-US'))
}

function fingerprint(stat: Stats): Fingerprint {
  return {
    dev: stat.dev,
    ino: stat.ino,
    mode: stat.mode,
    nlink: stat.nlink,
    size: stat.size,
    mtimeMs: stat.mtimeMs,
    ctimeMs: stat.ctimeMs,
  }
}

function sameFingerprint(left: Fingerprint, right: Fingerprint): boolean {
  return left.dev === right.dev
    && left.ino === right.ino
    && left.mode === right.mode
    && left.nlink === right.nlink
    && left.size === right.size
    && left.mtimeMs === right.mtimeMs
    && left.ctimeMs === right.ctimeMs
}

function sameIdentity(left: Fingerprint | null, right: Fingerprint | null): boolean {
  return left !== null && right !== null && left.dev === right.dev && left.ino === right.ino
}

/** Captures the identity of a bounded single-link regular file for later race detection. */
function validateRegularFile(stat: Stats, maxBytes: number): Fingerprint {
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) fail('FILE_SELECTION_INVALID')
  if (!Number.isSafeInteger(stat.size) || stat.size < 0) fail('FILE_SELECTION_INVALID')
  if (stat.size > maxBytes) fail('FILE_TOO_LARGE')
  return fingerprint(stat)
}

function isMissing(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { code?: unknown }).code === 'ENOENT'
}

function isGedcom(prefix: Buffer): boolean {
  if (archiveSignatures.some((signature) => prefix.subarray(0, signature.length).equals(signature))) return false
  let text: string
  if (prefix.subarray(0, 2).equals(Buffer.from([0xff, 0xfe]))) {
    text = prefix.subarray(2).toString('utf16le')
  } else if (prefix.subarray(0, 2).equals(Buffer.from([0xfe, 0xff]))) {
    const swapped = Buffer.alloc(prefix.length - 2)
    for (let index = 2; index + 1 < prefix.length; index += 2) {
      swapped[index - 2] = prefix[index + 1] as number
      swapped[index - 1] = prefix[index] as number
    }
    text = swapped.toString('utf16le')
  } else {
    text = prefix.toString('utf8').replace(/^\uFEFF/, '')
  }
  return /^0[ \t]+HEAD(?:[ \t\r\n]|$)/.test(text)
}

function safePrivateFilePath(value: string, policy: PurposePolicy): string {
  if (!isAbsolute(value) || value.includes('\0') || normalize(value) !== value
    || !safeName(basename(value)) || !hasExpectedExtension(value, policy)) {
    fail('FILE_SELECTION_INVALID')
  }
  return resolve(value)
}

function openFlags(access: 'read' | 'exclusive-write'): number {
  const optionalConstants = constants as typeof constants & Readonly<Record<string, number | undefined>>
  const common = (optionalConstants.O_CLOEXEC ?? 0) | (optionalConstants.O_NOFOLLOW ?? 0)
  if (access === 'read') return constants.O_RDONLY | common | (optionalConstants.O_NONBLOCK ?? 0)
  return constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | common
}

async function validatePrivateParent(path: string): Promise<void> {
  let parentStat: Stats
  try {
    parentStat = await lstat(dirname(path))
  } catch {
    fail('FILE_SELECTION_INVALID')
  }
  if (!parentStat.isDirectory() || parentStat.isSymbolicLink()) fail('FILE_SELECTION_INVALID')
}

async function removeIfPresent(path: string): Promise<void> {
  try {
    await unlink(path)
  } catch (error) {
    if (!isMissing(error)) throw error
  }
}

async function writeAll(
  handle: Awaited<ReturnType<typeof open>>,
  buffer: Buffer,
  length: number,
): Promise<void> {
  let offset = 0
  while (offset < length) {
    const written = await handle.write(buffer, offset, length - offset)
    if (written.bytesWritten <= 0) fail('FILE_SELECTION_INVALID')
    offset += written.bytesWritten
  }
}

async function copyValidatedFile(
  sourcePath: string,
  destinationPath: string,
  maxBytes: number,
  expected: Fingerprint,
  signal?: AbortSignal,
): Promise<Readonly<{ sizeBytes: number; sha256: string }>> {
  checkSignal(signal)
  await validatePrivateParent(destinationPath)
  let sourceHandle: Awaited<ReturnType<typeof open>> | undefined
  let destinationHandle: Awaited<ReturnType<typeof open>> | undefined
  let destinationCreated = false
  let completed = false
  try {
    sourceHandle = await open(sourcePath, openFlags('read'))
    const opened = validateRegularFile(await sourceHandle.stat(), maxBytes)
    if (!sameFingerprint(expected, opened)) fail('FILE_GRANT_STALE')
    destinationHandle = await open(destinationPath, openFlags('exclusive-write'), 0o600)
    destinationCreated = true
    const digest = createHash('sha256')
    const buffer = Buffer.allocUnsafe(1024 * 1024)
    let sizeBytes = 0
    for (;;) {
      checkSignal(signal)
      const read = await sourceHandle.read(buffer, 0, buffer.length, null)
      if (read.bytesRead === 0) break
      sizeBytes += read.bytesRead
      if (sizeBytes > maxBytes) fail('FILE_TOO_LARGE')
      const chunk = buffer.subarray(0, read.bytesRead)
      digest.update(chunk)
      await writeAll(destinationHandle, chunk, chunk.length)
    }
    if (sizeBytes !== opened.size) fail('FILE_GRANT_STALE')
    await destinationHandle.sync()
    const afterHandle = validateRegularFile(await sourceHandle.stat(), maxBytes)
    const afterPath = validateRegularFile(await lstat(sourcePath), maxBytes)
    if (!sameFingerprint(expected, afterHandle) || !sameFingerprint(expected, afterPath)) {
      fail('FILE_GRANT_STALE')
    }
    checkSignal(signal)
    const copied = Object.freeze({ sizeBytes, sha256: digest.digest('hex') })
    completed = true
    return copied
  } catch (error) {
    if (error instanceof FileGrantBrokerError) throw error
    return fail('FILE_SELECTION_INVALID')
  } finally {
    await destinationHandle?.close().catch(() => undefined)
    await sourceHandle?.close().catch(() => undefined)
    if (destinationCreated && !completed) await removeIfPresent(destinationPath).catch(() => undefined)
  }
}

async function inspectStagedOutput(
  path: string,
  purpose: FileWritePurpose,
  signal?: AbortSignal,
): Promise<InspectedStagedFile> {
  const policy = purposePolicies[purpose]
  const safePath = safePrivateFilePath(path, policy)
  checkSignal(signal)
  let before: Fingerprint
  let canonicalPath: string
  try {
    before = validateRegularFile(await lstat(safePath), policy.maxBytes)
    canonicalPath = await realpath(safePath)
  } catch (error) {
    if (error instanceof FileGrantBrokerError) throw error
    fail('FILE_SELECTION_INVALID')
  }
  let handle: Awaited<ReturnType<typeof open>>
  try {
    handle = await open(safePath, openFlags('read'))
  } catch {
    fail('FILE_SELECTION_INVALID')
  }
  const digest = createHash('sha256')
  const prefixParts: Buffer[] = []
  let prefixBytes = 0
  const textParts: string[] = []
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let sizeBytes = 0
  try {
    const opened = validateRegularFile(await handle.stat(), policy.maxBytes)
    if (!sameFingerprint(before, opened)) fail('FILE_GRANT_STALE')
    const buffer = Buffer.allocUnsafe(1024 * 1024)
    for (;;) {
      checkSignal(signal)
      const read = await handle.read(buffer, 0, buffer.length, null)
      if (read.bytesRead === 0) break
      sizeBytes += read.bytesRead
      if (sizeBytes > policy.maxBytes) fail('FILE_TOO_LARGE')
      const chunk = Buffer.from(buffer.subarray(0, read.bytesRead))
      digest.update(chunk)
      if (prefixBytes < 4_096) {
        const prefixChunk = chunk.subarray(0, Math.min(chunk.length, 4_096 - prefixBytes))
        prefixParts.push(prefixChunk)
        prefixBytes += prefixChunk.length
      }
      if (policy.format === 'json' || policy.format === 'markdown') {
        const decoded = decoder.decode(chunk, { stream: true })
        if (decoded.includes('\0')) fail('FILE_SELECTION_INVALID')
        if (policy.format === 'json') textParts.push(decoded)
      }
    }
    if (sizeBytes !== opened.size) fail('FILE_GRANT_STALE')
    if (policy.format === 'json' || policy.format === 'markdown') {
      const tail = decoder.decode()
      if (tail.includes('\0')) fail('FILE_SELECTION_INVALID')
      if (policy.format === 'json') textParts.push(tail)
    }
    const prefix = Buffer.concat(prefixParts)
    if (archiveSignatures.some((signature) => prefix.subarray(0, signature.length).equals(signature))) {
      fail('FILE_SELECTION_INVALID')
    }
    if (policy.format === 'gedcom' && !isGedcom(prefix)) fail('FILE_SELECTION_INVALID')
    if (policy.format === 'json') {
      try {
        JSON.parse(textParts.join(''))
      } catch {
        fail('FILE_SELECTION_INVALID')
      }
    }
    const afterHandle = validateRegularFile(await handle.stat(), policy.maxBytes)
    const afterPath = validateRegularFile(await lstat(safePath), policy.maxBytes)
    if (!sameFingerprint(before, afterHandle) || !sameFingerprint(before, afterPath)
      || canonicalPath !== await realpath(safePath)) fail('FILE_GRANT_STALE')
    checkSignal(signal)
    return {
      fingerprint: before,
      canonicalPath,
      sizeBytes,
      sha256: digest.digest('hex'),
    }
  } catch (error) {
    if (error instanceof FileGrantBrokerError) throw error
    fail('FILE_SELECTION_INVALID')
  } finally {
    await handle.close().catch(() => undefined)
  }
}

async function syncDirectory(path: string): Promise<void> {
  let handle: Awaited<ReturnType<typeof open>> | undefined
  try {
    handle = await open(path, constants.O_RDONLY)
    await handle.sync()
  } catch (error) {
    const code = typeof error === 'object' && error !== null ? (error as { code?: unknown }).code : undefined
    if (process.platform !== 'win32' || !['EACCES', 'EINVAL', 'EISDIR', 'EPERM'].includes(String(code))) throw error
  } finally {
    await handle?.close().catch(() => undefined)
  }
}

const nodeFilePublication: Readonly<FilePublicationPort> = Object.freeze({
  rename,
  syncDirectory,
})

async function openTemporaryPublication(
  path: string,
  maxBytes: number,
): Promise<Readonly<{ handle: FileHandle; identity: Fingerprint }>> {
  const optionalConstants = constants as typeof constants & Readonly<Record<string, number | undefined>>
  let handle: FileHandle | undefined
  try {
    handle = await open(
      path,
      constants.O_RDWR | (optionalConstants.O_CLOEXEC ?? 0) | (optionalConstants.O_NOFOLLOW ?? 0),
    )
    const opened = validateRegularFile(await handle.stat(), maxBytes)
    const current = validateRegularFile(await lstat(path), maxBytes)
    if (!sameFingerprint(opened, current)) fail('FILE_GRANT_STALE')
    return Object.freeze({ handle, identity: opened })
  } catch (error) {
    await handle?.close().catch(() => undefined)
    if (error instanceof FileGrantBrokerError) throw error
    return fail('FILE_SELECTION_INVALID')
  }
}

async function directoryMatchesIdentity(path: string, expected: Fingerprint): Promise<boolean> {
  try {
    const stat = await lstat(path)
    return stat.isDirectory() && !stat.isSymbolicLink()
      && sameIdentity(expected, fingerprint(stat))
  } catch {
    return false
  }
}

async function findDirectoryByIdentity(
  originalPath: string,
  expected: Fingerprint,
): Promise<string | undefined> {
  if (await directoryMatchesIdentity(originalPath, expected)) return originalPath
  const container = dirname(originalPath)
  if (container === originalPath) return undefined
  let entries
  try {
    entries = await readdir(container, { withFileTypes: true })
  } catch {
    return undefined
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.isSymbolicLink()) continue
    const candidate = join(container, entry.name)
    if (candidate !== originalPath && await directoryMatchesIdentity(candidate, expected)) {
      return candidate
    }
  }
  return undefined
}

async function unlinkExactFile(path: string, expected: Fingerprint): Promise<boolean> {
  try {
    const current = await lstat(path)
    if (!current.isFile() || current.isSymbolicLink()
      || !sameIdentity(expected, fingerprint(current))) return false
    await unlink(path)
    return true
  } catch {
    return false
  }
}

async function cleanupTemporaryPublication(
  handle: FileHandle,
  temporaryPath: string,
  expectedIdentity: Fingerprint,
  expectedParent: Fingerprint | null,
): Promise<void> {
  await handle.truncate(0).catch(() => undefined)
  await handle.sync().catch(() => undefined)
  const originalParent = dirname(temporaryPath)
  const temporaryName = basename(temporaryPath)
  let removed = false
  if (expectedParent !== null) {
    const parent = await findDirectoryByIdentity(originalParent, expectedParent)
    if (parent !== undefined) removed = await unlinkExactFile(join(parent, temporaryName), expectedIdentity)
  }
  await handle.close().catch(() => undefined)
  if (!removed && expectedParent !== null) {
    const parent = await findDirectoryByIdentity(originalParent, expectedParent)
    if (parent !== undefined) await unlinkExactFile(join(parent, temporaryName), expectedIdentity)
  }
}

/** Opens and fingerprints a permitted input without following links or trusting its extension alone. */
async function inspectInput(path: string, policy: PurposePolicy): Promise<Readonly<{ fingerprint: Fingerprint; canonicalPath: string }>> {
  if (!hasExpectedExtension(path, policy)) fail('FILE_SELECTION_INVALID')
  let before: Stats
  let canonicalPath: string
  try {
    before = await lstat(path)
    canonicalPath = await realpath(path)
  } catch {
    fail('FILE_SELECTION_INVALID')
  }
  const expected = validateRegularFile(before, policy.maxBytes)
  const optionalConstants = constants as typeof constants & Readonly<Record<string, number | undefined>>
  const flags = constants.O_RDONLY
    | (optionalConstants.O_CLOEXEC ?? 0)
    | (optionalConstants.O_NOFOLLOW ?? 0)
    | (optionalConstants.O_NONBLOCK ?? 0)
  let handle
  try {
    handle = await open(path, flags)
  } catch {
    fail('FILE_SELECTION_INVALID')
  }
  try {
    const opened = validateRegularFile(await handle.stat(), policy.maxBytes)
    if (!sameFingerprint(expected, opened)) fail('FILE_GRANT_STALE')
    const prefix = Buffer.alloc(Math.min(4_096, opened.size))
    const read = await handle.read(prefix, 0, prefix.length, 0)
    const content = prefix.subarray(0, read.bytesRead)
    if (policy.format === 'gedcom' && !isGedcom(content)) fail('FILE_SELECTION_INVALID')
    if (policy.format === 'rootsmagic' && !content.subarray(0, sqliteSignature.length).equals(sqliteSignature)) {
      fail('FILE_SELECTION_INVALID')
    }
  } finally {
    await handle.close()
  }
  try {
    const after = validateRegularFile(await lstat(path), policy.maxBytes)
    if (!sameFingerprint(expected, after) || canonicalPath !== await realpath(path)) fail('FILE_GRANT_STALE')
  } catch (error) {
    if (error instanceof FileGrantBrokerError) throw error
    fail('FILE_GRANT_STALE')
  }
  return { fingerprint: expected, canonicalPath }
}

/** Validates an output path and its parent identity before issuing a time-limited file grant. */
async function inspectOutput(path: string, policy: PurposePolicy): Promise<Readonly<{
  fingerprint: Fingerprint | null
  parentFingerprint: Fingerprint
  canonicalPath: string
  displayName: string
}>> {
  if (!hasExpectedExtension(path, policy)) fail('FILE_SELECTION_INVALID')
  const displayName = basename(path)
  const parent = dirname(path)
  let parentStat: Stats
  try {
    parentStat = await lstat(parent)
  } catch {
    fail('FILE_SELECTION_INVALID')
  }
  if (!parentStat.isDirectory() || parentStat.isSymbolicLink()) fail('FILE_SELECTION_INVALID')
  const expectedParent = fingerprint(parentStat)
  let canonicalParent: string
  try {
    canonicalParent = await realpath(parent)
  } catch {
    fail('FILE_SELECTION_INVALID')
  }
  let target: Fingerprint | null
  try {
    target = validateRegularFile(await lstat(path), policy.maxBytes)
  } catch (error) {
    if (error instanceof FileGrantBrokerError) throw error
    if (!isMissing(error)) fail('FILE_SELECTION_INVALID')
    target = null
  }
  try {
    const currentParent = await lstat(parent)
    if (!currentParent.isDirectory() || currentParent.isSymbolicLink()
      || !sameFingerprint(expectedParent, fingerprint(currentParent))
      || canonicalParent !== await realpath(parent)) fail('FILE_GRANT_STALE')
  } catch (error) {
    if (error instanceof FileGrantBrokerError) throw error
    fail('FILE_GRANT_STALE')
  }
  return {
    fingerprint: target,
    parentFingerprint: expectedParent,
    canonicalPath: join(canonicalParent, displayName),
    displayName,
  }
}

function publicGrant(binding: Binding, sizeBytes: number, validation: FileValidation): Readonly<FileGrant> {
  return Object.freeze({
    grantId: binding.id,
    purpose: binding.purpose,
    access: binding.access,
    scope: Object.freeze({
      originatingWindow: 'requesting-window',
      lifetime: 'app-session',
      redemption: 'single-use',
    }),
    metadata: Object.freeze({
      displayName: basename(binding.path),
      format: binding.format,
      sizeBytes,
      validation,
    }),
  })
}

/**
 * Owns file grant broker state transitions while enforcing capability-scoped filesystem access without exposing raw paths.
 */
export class FileGrantBroker {
  private readonly dialogs: NativeFileDialogPort
  private readonly publication: Readonly<FilePublicationPort>
  private readonly bindings = new Map<FileGrantId, Binding>()
  private readonly outputLocks = new Map<string, FileGrantId>()
  private readonly pendingDialogs = new Set<object>()
  private readonly ownerGenerations = new WeakMap<object, number>()
  private disposed = false

  constructor(
    dialogs: NativeFileDialogPort,
    publication: Readonly<FilePublicationPort> = nodeFilePublication,
  ) {
    this.dialogs = dialogs
    this.publication = publication
  }

  async requestOpenGrant(
    owner: object,
    request: OpenFileGrantRequest,
    signal?: AbortSignal,
  ): Promise<Readonly<FileGrant> | null> {
    const policy = purposePolicies[request.purpose]
    if (!policy || policy.access !== 'read') fail('FILE_SELECTION_INVALID')
    return this.withDialog(owner, async (generation) => {
      checkSignal(signal)
      const chosen = await this.dialogs.selectOpenFile(owner, request.purpose, signal)
      checkSignal(signal)
      this.requireGeneration(owner, generation)
      if (chosen === null) return null
      const path = selectedPath(chosen)
      const inspected = await inspectInput(path, policy)
      checkSignal(signal)
      this.requireGeneration(owner, generation)
      const binding = this.createBinding(owner, request.purpose, path, inspected.canonicalPath, inspected.fingerprint)
      this.assertNoAlias(binding)
      this.requireGeneration(owner, generation)
      this.bindings.set(binding.id, binding)
      return publicGrant(binding, inspected.fingerprint.size, 'validated-input')
    })
  }

  async requestSaveGrant(
    owner: object,
    request: SaveFileGrantRequest,
    signal?: AbortSignal,
  ): Promise<Readonly<FileGrant> | null> {
    const policy = purposePolicies[request.purpose]
    if (!policy || policy.access !== 'write' || !safeName(request.suggestedName)
      || !hasExpectedExtension(request.suggestedName, policy)) fail('FILE_SELECTION_INVALID')
    return this.withDialog(owner, async (generation) => {
      checkSignal(signal)
      const chosen = await this.dialogs.selectSaveFile(owner, request.purpose, request.suggestedName, signal)
      checkSignal(signal)
      this.requireGeneration(owner, generation)
      if (chosen === null) return null
      const path = selectedPath(chosen)
      let inspected = await inspectOutput(path, policy)
      let validation: FileValidation = 'new-output'
      if (inspected.fingerprint !== null) {
        const confirmed = await this.dialogs.confirmReplacement(owner, inspected.displayName, signal)
        checkSignal(signal)
        this.requireGeneration(owner, generation)
        if (!confirmed) return null
        const revalidated = await inspectOutput(path, policy)
        if (revalidated.fingerprint === null
          || !sameFingerprint(inspected.fingerprint, revalidated.fingerprint)
          || !sameFingerprint(inspected.parentFingerprint, revalidated.parentFingerprint)) fail('FILE_GRANT_STALE')
        inspected = revalidated
        validation = 'replacement-confirmed'
      }
      this.requireGeneration(owner, generation)
      if (this.outputLocks.has(collisionKey(inspected.canonicalPath))) fail('FILE_GRANT_CONFLICT')
      const binding = this.createBinding(
        owner,
        request.purpose,
        path,
        inspected.canonicalPath,
        inspected.fingerprint,
        inspected.parentFingerprint,
      )
      this.assertNoAlias(binding)
      this.requireGeneration(owner, generation)
      this.bindings.set(binding.id, binding)
      this.outputLocks.set(collisionKey(binding.canonicalPath), binding.id)
      return publicGrant(binding, inspected.fingerprint?.size ?? 0, validation)
    })
  }

  /**
   * Redeems an input grant by copying immutable bytes into trusted private staging.
   * The returned metadata deliberately excludes both selected and staging paths.
   */
  async stageReadGrant(
    owner: object,
    grantId: FileGrantId,
    purpose: FileReadPurpose,
    destination: string,
    signal?: AbortSignal,
  ): Promise<Readonly<StagedReadGrant>> {
    const binding = this.binding(owner, grantId, purpose, 'read')
    binding.redeemed = true
    const policy = purposePolicies[purpose]
    const stagedPath = safePrivateFilePath(destination, policy)
    try {
      checkSignal(signal)
      if (collisionKey(binding.canonicalPath) === collisionKey(stagedPath)) fail('FILE_SELECTION_INVALID')
      const inspected = await inspectInput(binding.path, policy)
      if (this.bindings.get(binding.id) !== binding
        || binding.fingerprint === null
        || !sameFingerprint(binding.fingerprint, inspected.fingerprint)
        || binding.canonicalPath !== inspected.canonicalPath) fail('FILE_GRANT_STALE')
      const copied = await copyValidatedFile(
        binding.path,
        stagedPath,
        binding.maxBytes,
        inspected.fingerprint,
        signal,
      )
      await chmod(stagedPath, 0o400)
      const staged = await inspectInput(stagedPath, policy)
      if (staged.fingerprint.size !== copied.sizeBytes) fail('FILE_GRANT_STALE')
      checkSignal(signal)
      return Object.freeze({
        grantId,
        purpose,
        sizeBytes: copied.sizeBytes,
        sha256: copied.sha256,
      })
    } catch (error) {
      await removeIfPresent(stagedPath).catch(() => undefined)
      throw error
    } finally {
      this.removeBinding(binding)
    }
  }

  /** Validates one private staged output before any selected host path is touched. */
  async validateStagedOutput(
    purpose: FileWritePurpose,
    stagedPath: string,
    signal?: AbortSignal,
  ): Promise<Readonly<ValidatedStagedOutput>> {
    const inspected = await inspectStagedOutput(stagedPath, purpose, signal)
    return Object.freeze({ purpose, sizeBytes: inspected.sizeBytes, sha256: inspected.sha256 })
  }

  /**
   * Redeems an output grant by validating private staging and atomically renaming a
   * same-directory temporary file over the selected destination.
   */
  async publishWriteGrant(
    owner: object,
    grantId: FileGrantId,
    purpose: FileWritePurpose,
    stagedPath: string,
    signal?: AbortSignal,
  ): Promise<Readonly<PublishedWriteGrant>> {
    const binding = this.binding(owner, grantId, purpose, 'write')
    binding.redeemed = true
    let temporaryPath: string | undefined
    let temporaryHandle: FileHandle | undefined
    let temporaryIdentity: Fingerprint | undefined
    let published = false
    try {
      checkSignal(signal)
      const staged = await inspectStagedOutput(stagedPath, purpose, signal)
      if (collisionKey(staged.canonicalPath) === collisionKey(binding.canonicalPath)) {
        fail('FILE_SELECTION_INVALID')
      }
      await this.validateWriteBinding(binding)
      temporaryPath = join(
        dirname(binding.path),
        `.${basename(binding.path)}.ancestryllm-${randomBytes(16).toString('hex')}.tmp`,
      )
      const copied = await copyValidatedFile(
        staged.canonicalPath,
        temporaryPath,
        binding.maxBytes,
        staged.fingerprint,
        signal,
      )
      if (copied.sizeBytes !== staged.sizeBytes || copied.sha256 !== staged.sha256) fail('FILE_GRANT_STALE')
      const temporary = await openTemporaryPublication(temporaryPath, binding.maxBytes)
      temporaryHandle = temporary.handle
      temporaryIdentity = temporary.identity
      await this.validateWriteBinding(binding)
      checkSignal(signal)
      await this.publication.rename(temporaryPath, binding.path)
      published = true
      await temporaryHandle.close().catch(() => undefined)
      temporaryHandle = undefined
      let durability: PublishedWriteGrant['durability'] = 'confirmed'
      try {
        await this.publication.syncDirectory(dirname(binding.path))
      } catch {
        durability = 'unconfirmed'
      }
      return Object.freeze({
        grantId,
        purpose,
        sizeBytes: staged.sizeBytes,
        sha256: staged.sha256,
        durability,
      })
    } catch (error) {
      if (error instanceof FileGrantBrokerError) throw error
      return fail('FILE_SELECTION_INVALID')
    } finally {
      if (!published && temporaryPath !== undefined && temporaryHandle !== undefined
        && temporaryIdentity !== undefined) {
        await cleanupTemporaryPublication(
          temporaryHandle,
          temporaryPath,
          temporaryIdentity,
          binding.parentFingerprint,
        )
        temporaryHandle = undefined
      } else if (!published && temporaryPath !== undefined) {
        await removeIfPresent(temporaryPath).catch(() => undefined)
      }
      this.removeBinding(binding)
    }
  }

  async resolveReadGrant(
    owner: object,
    grantId: FileGrantId,
    purpose: FileReadPurpose,
  ): Promise<Readonly<ResolvedFileGrant>> {
    const binding = this.binding(owner, grantId, purpose, 'read')
    binding.redeemed = true
    try {
      const inspected = await inspectInput(binding.path, purposePolicies[purpose])
      if (this.bindings.get(binding.id) !== binding) fail('FILE_GRANT_REVOKED')
      if (binding.fingerprint === null || !sameFingerprint(binding.fingerprint, inspected.fingerprint)
        || binding.canonicalPath !== inspected.canonicalPath) fail('FILE_GRANT_STALE')
    } catch (error) {
      this.removeBinding(binding)
      throw error
    }
    return Object.freeze({ grantId, purpose, access: 'read', path: binding.path, maxBytes: binding.maxBytes })
  }

  async resolveWriteGrant(
    owner: object,
    grantId: FileGrantId,
    purpose: FileWritePurpose,
  ): Promise<Readonly<ResolvedFileGrant>> {
    const binding = this.binding(owner, grantId, purpose, 'write')
    binding.redeemed = true
    try {
      await this.validateWriteBinding(binding)
    } catch (error) {
      this.removeBinding(binding)
      throw error
    }
    return Object.freeze({ grantId, purpose, access: 'write', path: binding.path, maxBytes: binding.maxBytes })
  }

  revokeGrant(owner: object, grantId: string): Readonly<FileGrantRevocation> {
    if (grantPattern.test(grantId)) {
      const binding = this.bindings.get(grantId as FileGrantId)
      if (binding?.owner === owner) this.removeBinding(binding)
    }
    return Object.freeze({ revoked: true })
  }

  revokeOwner(owner: object): void {
    this.ownerGenerations.set(owner, this.generation(owner) + 1)
    for (const binding of [...this.bindings.values()]) {
      if (binding.owner === owner) this.removeBinding(binding)
    }
  }

  revokeAll(): void {
    const owners = new Set<object>(this.pendingDialogs)
    for (const binding of this.bindings.values()) owners.add(binding.owner)
    for (const owner of owners) this.ownerGenerations.set(owner, this.generation(owner) + 1)
    for (const binding of [...this.bindings.values()]) this.removeBinding(binding)
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.revokeAll()
    this.pendingDialogs.clear()
  }

  private async withDialog<T>(owner: object, operation: (generation: number) => Promise<T>): Promise<T> {
    if (this.disposed) fail('FILE_DIALOG_FAILED')
    if (this.pendingDialogs.has(owner)) fail('FILE_GRANT_CONFLICT')
    const generation = this.generation(owner)
    this.pendingDialogs.add(owner)
    try {
      return await operation(generation)
    } finally {
      this.pendingDialogs.delete(owner)
    }
  }

  private generation(owner: object): number {
    return this.ownerGenerations.get(owner) ?? 0
  }

  private requireGeneration(owner: object, expected: number): void {
    if (this.disposed || this.generation(owner) !== expected) fail('FILE_GRANT_REVOKED')
  }

  private createBinding(
    owner: object,
    purpose: FileGrantPurpose,
    path: string,
    canonicalPath: string,
    selectedFingerprint: Fingerprint | null,
    parentFingerprint: Fingerprint | null = null,
  ): Binding {
    const policy = purposePolicies[purpose]
    return {
      owner,
      id: `grt_${randomBytes(32).toString('hex')}`,
      purpose,
      access: policy.access,
      format: policy.format,
      path,
      canonicalPath,
      maxBytes: policy.maxBytes,
      fingerprint: selectedFingerprint,
      parentFingerprint,
      redeemed: false,
    }
  }

  private assertNoAlias(candidate: Binding): void {
    for (const binding of this.bindings.values()) {
      if (binding.id === candidate.id) continue
      if (collisionKey(binding.canonicalPath) === collisionKey(candidate.canonicalPath)
        || sameIdentity(binding.fingerprint, candidate.fingerprint)) {
        if (binding.access === 'write' || candidate.access === 'write') fail('FILE_GRANT_CONFLICT')
      }
    }
  }

  private async validateWriteBinding(binding: Binding): Promise<void> {
    const inspected = await inspectOutput(binding.path, purposePolicies[binding.purpose])
    if (this.bindings.get(binding.id) !== binding) fail('FILE_GRANT_REVOKED')
    if (binding.canonicalPath !== inspected.canonicalPath
      || binding.parentFingerprint === null
      || !sameIdentity(binding.parentFingerprint, inspected.parentFingerprint)
      || (binding.fingerprint === null) !== (inspected.fingerprint === null)
      || (binding.fingerprint !== null && inspected.fingerprint !== null
        && !sameFingerprint(binding.fingerprint, inspected.fingerprint))) fail('FILE_GRANT_STALE')
    this.assertNoAlias(binding)
  }

  private binding(
    owner: object,
    grantId: FileGrantId,
    purpose: FileGrantPurpose,
    access: FileGrantAccess,
  ): Binding {
    if (!grantPattern.test(grantId)) fail('FILE_GRANT_REVOKED')
    const binding = this.bindings.get(grantId)
    if (!binding || binding.redeemed) fail('FILE_GRANT_REVOKED')
    if (binding.owner !== owner || binding.purpose !== purpose || binding.access !== access) {
      fail('FILE_GRANT_FORBIDDEN')
    }
    return binding
  }

  private removeBinding(binding: Binding): void {
    if (this.bindings.get(binding.id) !== binding) return
    this.bindings.delete(binding.id)
    const lockKey = collisionKey(binding.canonicalPath)
    if (this.outputLocks.get(lockKey) === binding.id) {
      this.outputLocks.delete(lockKey)
    }
  }
}
