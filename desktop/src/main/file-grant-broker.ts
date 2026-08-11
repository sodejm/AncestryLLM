/** Mediates native file selections through opaque, validated, single-use grants. */
import { constants, type Stats } from 'node:fs'
import { lstat, open, realpath } from 'node:fs/promises'
import { basename, dirname, extname, isAbsolute, join, normalize, resolve } from 'node:path'
import { randomBytes } from 'node:crypto'
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

export type FileGrantFailureCode =
  | 'FILE_SELECTION_INVALID'
  | 'FILE_TOO_LARGE'
  | 'FILE_GRANT_FORBIDDEN'
  | 'FILE_GRANT_REVOKED'
  | 'FILE_GRANT_STALE'
  | 'FILE_GRANT_CONFLICT'
  | 'FILE_DIALOG_FAILED'

export class FileGrantBrokerError extends Error {
  readonly code: FileGrantFailureCode

  constructor(code: FileGrantFailureCode) {
    super(code)
    this.name = 'FileGrantBrokerError'
    this.code = code
  }
}

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

export interface ResolvedFileGrant {
  readonly grantId: FileGrantId
  readonly purpose: FileGrantPurpose
  readonly access: FileGrantAccess
  readonly path: string
  readonly maxBytes: number
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

const purposePolicies: Readonly<Record<FileGrantPurpose, PurposePolicy>> = Object.freeze({
  'gedcom-read': Object.freeze({ access: 'read', format: 'gedcom', extensions: ['.ged', '.gedcom'], maxBytes: 536_870_912 }),
  'rootsmagic-read': Object.freeze({ access: 'read', format: 'rootsmagic', extensions: ['.rmtree'], maxBytes: 8_589_934_592 }),
  'gedcom-write': Object.freeze({ access: 'write', format: 'gedcom', extensions: ['.ged'], maxBytes: 536_870_912 }),
  'json-write': Object.freeze({ access: 'write', format: 'json', extensions: ['.json'], maxBytes: 536_870_912 }),
  'markdown-write': Object.freeze({ access: 'write', format: 'markdown', extensions: ['.md'], maxBytes: 536_870_912 }),
})

// Control characters are intentionally rejected from user-visible file names.
// eslint-disable-next-line no-control-regex
const safeNamePattern = /^[^/\\\u0000-\u001f\u007f]{1,255}$/
const archiveSignatures = [
  Buffer.from([0x50, 0x4b, 0x03, 0x04]),
  Buffer.from([0x50, 0x4b, 0x05, 0x06]),
  Buffer.from([0x50, 0x4b, 0x07, 0x08]),
  Buffer.from([0x1f, 0x8b]),
] as const
const sqliteSignature = Buffer.from('SQLite format 3\0', 'ascii')
const grantPattern = /^grt_[a-f0-9]{64}$/

function fail(code: FileGrantFailureCode): never {
  throw new FileGrantBrokerError(code)
}

function checkSignal(signal?: AbortSignal): void {
  if (signal?.aborted) throw signal.reason ?? new Error('Operation aborted')
}

function safeName(value: string): boolean {
  return safeNamePattern.test(value) && value !== '.' && value !== '..'
}

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

export class FileGrantBroker {
  private readonly dialogs: NativeFileDialogPort
  private readonly bindings = new Map<FileGrantId, Binding>()
  private readonly outputLocks = new Map<string, FileGrantId>()
  private readonly pendingDialogs = new Set<object>()
  private readonly ownerGenerations = new WeakMap<object, number>()
  private disposed = false

  constructor(dialogs: NativeFileDialogPort) {
    this.dialogs = dialogs
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
      if (this.outputLocks.has(inspected.canonicalPath)) fail('FILE_GRANT_CONFLICT')
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
      this.outputLocks.set(binding.canonicalPath, binding.id)
      return publicGrant(binding, inspected.fingerprint?.size ?? 0, validation)
    })
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
      const inspected = await inspectOutput(binding.path, purposePolicies[purpose])
      if (this.bindings.get(binding.id) !== binding) fail('FILE_GRANT_REVOKED')
      if (binding.canonicalPath !== inspected.canonicalPath
        || binding.parentFingerprint === null
        || !sameFingerprint(binding.parentFingerprint, inspected.parentFingerprint)
        || (binding.fingerprint === null) !== (inspected.fingerprint === null)
        || (binding.fingerprint !== null && inspected.fingerprint !== null
          && !sameFingerprint(binding.fingerprint, inspected.fingerprint))) fail('FILE_GRANT_STALE')
      this.assertNoAlias(binding)
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
      if (binding.canonicalPath === candidate.canonicalPath || sameIdentity(binding.fingerprint, candidate.fingerprint)) {
        if (binding.access === 'write' || candidate.access === 'write') fail('FILE_GRANT_CONFLICT')
      }
    }
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
    if (this.outputLocks.get(binding.canonicalPath) === binding.id) {
      this.outputLocks.delete(binding.canonicalPath)
    }
  }
}
