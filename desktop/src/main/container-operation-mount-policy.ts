/** Builds and verifies the only host bind mounts permitted for one mediated operation. */

import { lstat, mkdir, readdir, realpath, rm } from 'node:fs/promises'
import { isAbsolute, join, normalize, relative, resolve } from 'node:path'
import type { HostRealizedContainerMount } from './container-supervisor'

/** Enumerates the stable path-free failures exposed by the mount-policy boundary. */
export type MediatedMountPolicyErrorCode =
  | 'INVALID_OPERATION'
  | 'STAGING_UNSAFE'
  | 'MOUNT_MISMATCH'

const ERROR_MESSAGES: Readonly<Record<MediatedMountPolicyErrorCode, string>> = Object.freeze({
  INVALID_OPERATION: 'The mediated operation identifier is invalid.',
  STAGING_UNSAFE: 'The private operation staging area is unsafe.',
  MOUNT_MISMATCH: 'The realized operation mounts do not match the authorized plan.',
})

/** Carries a stable, path-free mount-policy failure. */
export class MediatedMountPolicyError extends Error {
  constructor(readonly code: MediatedMountPolicyErrorCode) {
    super(ERROR_MESSAGES[code])
    this.name = 'MediatedMountPolicyError'
  }
}

/** Describes one exact per-operation bind mount. */
export interface MediatedOperationMount {
  readonly kind: 'bind'
  readonly source: string
  readonly target: string
  readonly readOnly: boolean
}

/** Keeps trusted staging paths in Electron Main and out of transport DTOs. */
export interface MediatedOperationMountPlan {
  readonly operationId: string
  readonly operationRoot: string
  readonly inputRoot: string
  readonly outputRoot: string
  readonly mounts: readonly [MediatedOperationMount, MediatedOperationMount]
}

const OPERATION_ID = /^op_[a-f0-9]{64}$/

function fail(code: MediatedMountPolicyErrorCode): never {
  throw new MediatedMountPolicyError(code)
}

function isAlreadyPresent(error: unknown): boolean {
  return typeof error === 'object' && error !== null
    && (error as { code?: unknown }).code === 'EEXIST'
}

async function inspectCanonicalDirectory(path: string, requirePrivateAccess: boolean): Promise<string> {
  if (!isAbsolute(path) || path.includes('\0') || normalize(path) !== path) fail('STAGING_UNSAFE')
  try {
    const stat = await lstat(path)
    if (!stat.isDirectory() || stat.isSymbolicLink()
      || (requirePrivateAccess && (stat.mode & 0o077) !== 0)) {
      fail('STAGING_UNSAFE')
    }
    const canonical = await realpath(path)
    if (canonical !== resolve(path)) fail('STAGING_UNSAFE')
    return canonical
  } catch (error) {
    if (error instanceof MediatedMountPolicyError) throw error
    return fail('STAGING_UNSAFE')
  }
}

async function inspectPrivateDirectory(path: string): Promise<string> {
  return inspectCanonicalDirectory(path, true)
}

async function ensurePrivateDirectory(path: string): Promise<string> {
  try {
    await mkdir(path, { mode: 0o700 })
  } catch (error) {
    if (!isAlreadyPresent(error)) fail('STAGING_UNSAFE')
  }
  return inspectPrivateDirectory(path)
}

function requireDescendant(parent: string, child: string): void {
  const pathFromParent = relative(parent, child)
  if (pathFromParent.length === 0 || pathFromParent === '..'
    || pathFromParent.startsWith('../') || isAbsolute(pathFromParent)) fail('STAGING_UNSAFE')
}

/**
 * Creates the fixed private runtime profile beneath Electron's trusted app-data
 * directory and removes exact operation remnants before any desktop work starts.
 */
export async function initializeMediatedOperationStaging(
  applicationDataRoot: string,
): Promise<string> {
  const appDataRoot = await inspectCanonicalDirectory(applicationDataRoot, false)
  const runtimeProfileRoot = await ensurePrivateDirectory(join(appDataRoot, 'mediated-runtime'))
  requireDescendant(appDataRoot, runtimeProfileRoot)
  await cleanupStaleMediatedOperationMounts(runtimeProfileRoot)
  return runtimeProfileRoot
}

/**
 * Creates a fresh private directory tree for one operation and returns its exact
 * input-read-only and output-read-write bind plan.
 */
export async function prepareMediatedOperationMounts(
  runtimeProfileRoot: string,
  operationId: string,
): Promise<Readonly<MediatedOperationMountPlan>> {
  if (!OPERATION_ID.test(operationId)) fail('INVALID_OPERATION')
  const profileRoot = await inspectPrivateDirectory(runtimeProfileRoot)
  const stagingRoot = await ensurePrivateDirectory(join(profileRoot, 'operation-staging'))
  requireDescendant(profileRoot, stagingRoot)
  const operationRoot = join(stagingRoot, operationId)
  let operationCreated = false
  try {
    await mkdir(operationRoot, { mode: 0o700 })
    operationCreated = true
    const inputRoot = await ensurePrivateDirectory(join(operationRoot, 'inputs'))
    const outputRoot = await ensurePrivateDirectory(join(operationRoot, 'outputs'))
    const canonicalOperationRoot = await inspectPrivateDirectory(operationRoot)
    requireDescendant(stagingRoot, canonicalOperationRoot)
    requireDescendant(canonicalOperationRoot, inputRoot)
    requireDescendant(canonicalOperationRoot, outputRoot)
    const containerRoot = `/run/ancestryllm/operations/${operationId}`
    const mounts = Object.freeze([
      Object.freeze({
        kind: 'bind' as const,
        source: inputRoot,
        target: `${containerRoot}/inputs`,
        readOnly: true,
      }),
      Object.freeze({
        kind: 'bind' as const,
        source: outputRoot,
        target: `${containerRoot}/outputs`,
        readOnly: false,
      }),
    ] as const)
    return Object.freeze({
      operationId,
      operationRoot: canonicalOperationRoot,
      inputRoot,
      outputRoot,
      mounts,
    })
  } catch (error) {
    if (operationCreated) await rm(operationRoot, { recursive: true, force: true }).catch(() => undefined)
    if (error instanceof MediatedMountPolicyError) throw error
    return fail('STAGING_UNSAFE')
  }
}

/**
 * Removes exact, app-owned operation directories left by an interrupted prior
 * session. Unexpected entries are preserved and stop recovery fail closed.
 */
export async function cleanupStaleMediatedOperationMounts(
  runtimeProfileRoot: string,
): Promise<number> {
  const profileRoot = await inspectPrivateDirectory(runtimeProfileRoot)
  const stagingRoot = await ensurePrivateDirectory(join(profileRoot, 'operation-staging'))
  requireDescendant(profileRoot, stagingRoot)
  let entries
  try {
    entries = await readdir(stagingRoot, { withFileTypes: true })
  } catch {
    return fail('STAGING_UNSAFE')
  }
  const staleRoots: string[] = []
  for (const entry of entries) {
    if (!OPERATION_ID.test(entry.name) || !entry.isDirectory() || entry.isSymbolicLink()) {
      fail('STAGING_UNSAFE')
    }
    const staleRoot = await inspectPrivateDirectory(join(stagingRoot, entry.name))
    requireDescendant(stagingRoot, staleRoot)
    staleRoots.push(staleRoot)
  }
  try {
    for (const staleRoot of staleRoots) {
      await rm(staleRoot, { recursive: true, force: false })
    }
  } catch {
    return fail('STAGING_UNSAFE')
  }
  return staleRoots.length
}

/** Verifies Docker's complete realized mount set before trusting operation output. */
export function validateRealizedMediatedMounts(
  plan: Readonly<MediatedOperationMountPlan>,
  realized: readonly HostRealizedContainerMount[],
): void {
  if (realized.length !== plan.mounts.length) fail('MOUNT_MISMATCH')
  const expectedByTarget = new Map(plan.mounts.map((mount) => [mount.target, mount]))
  const seenTargets = new Set<string>()
  for (const mount of realized) {
    const expected = expectedByTarget.get(mount.target)
    if (!expected || seenTargets.has(mount.target)
      || mount.kind !== 'bind'
      || mount.source !== expected.source
      || mount.readOnly !== expected.readOnly) fail('MOUNT_MISMATCH')
    seenTargets.add(mount.target)
  }
  if (seenTargets.size !== expectedByTarget.size) fail('MOUNT_MISMATCH')
}
