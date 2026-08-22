/** Verifies private, exact, operation-scoped bind mounts for mediated host files. */

import { chmod, lstat, mkdtemp, mkdir, realpath, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  cleanupStaleMediatedOperationMounts,
  directoryModeIsPrivate,
  initializeMediatedOperationStaging,
  MediatedMountPolicyError,
  prepareMediatedOperationMounts,
  validateRealizedMediatedMounts,
} from './container-operation-mount-policy'

const roots: string[] = []
const operationId = `op_${'a'.repeat(64)}`

async function runtimeRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-operation-mounts-'))
  roots.push(root)
  return realpath(root)
}

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })))
})

describe('mediated operation mount policy', () => {
  it('enforces POSIX private modes without rejecting Windows directory metadata', () => {
    expect(directoryModeIsPrivate(0o700, 'linux')).toBe(true)
    expect(directoryModeIsPrivate(0o755, 'linux')).toBe(false)
    expect(directoryModeIsPrivate(0o755, 'darwin')).toBe(false)
    expect(directoryModeIsPrivate(0o755, 'win32')).toBe(true)
  })

  it('initializes a private profile and removes stale operations before desktop startup', async () => {
    const applicationDataRoot = await runtimeRoot()
    await chmod(applicationDataRoot, 0o755)

    const profileRoot = await initializeMediatedOperationStaging(applicationDataRoot)

    expect(profileRoot).toBe(join(applicationDataRoot, 'mediated-runtime'))
    expect((await lstat(profileRoot)).mode & 0o077).toBe(0)
    const stale = await prepareMediatedOperationMounts(profileRoot, operationId)
    await expect(initializeMediatedOperationStaging(applicationDataRoot)).resolves.toBe(profileRoot)
    await expect(lstat(stale.operationRoot)).rejects.toMatchObject({ code: 'ENOENT' })
  })

  it('creates only private per-operation input and output bind mounts', async () => {
    const root = await runtimeRoot()

    const plan = await prepareMediatedOperationMounts(root, operationId)

    expect(plan).toEqual({
      operationId,
      operationRoot: join(root, 'operation-staging', operationId),
      inputRoot: join(root, 'operation-staging', operationId, 'inputs'),
      outputRoot: join(root, 'operation-staging', operationId, 'outputs'),
      mounts: [
        {
          kind: 'bind',
          source: join(root, 'operation-staging', operationId, 'inputs'),
          target: `/run/ancestryllm/operations/${operationId}/inputs`,
          readOnly: true,
        },
        {
          kind: 'bind',
          source: join(root, 'operation-staging', operationId, 'outputs'),
          target: `/run/ancestryllm/operations/${operationId}/outputs`,
          readOnly: false,
        },
      ],
    })
    for (const path of [join(root, 'operation-staging'), plan.operationRoot, plan.inputRoot, plan.outputRoot]) {
      const stat = await lstat(path)
      expect(stat.isDirectory()).toBe(true)
      expect(stat.isSymbolicLink()).toBe(false)
      expect(stat.mode & 0o077).toBe(0)
      await expect(realpath(path)).resolves.toBe(path)
    }
  })

  it('rejects invalid operation IDs, reused operation directories, and symlinked staging roots', async () => {
    const root = await runtimeRoot()
    await expect(prepareMediatedOperationMounts(root, 'op_short'))
      .rejects.toBeInstanceOf(MediatedMountPolicyError)

    await prepareMediatedOperationMounts(root, operationId)
    await expect(prepareMediatedOperationMounts(root, operationId))
      .rejects.toMatchObject({ code: 'STAGING_UNSAFE' })

    const otherRoot = await runtimeRoot()
    const outside = join(otherRoot, 'outside')
    await mkdir(outside, { mode: 0o700 })
    await symlink(outside, join(otherRoot, 'operation-staging'))
    await expect(prepareMediatedOperationMounts(otherRoot, `op_${'b'.repeat(64)}`))
      .rejects.toMatchObject({ code: 'STAGING_UNSAFE' })
  })

  it('accepts only an exact engine realization of both operation mounts', async () => {
    const root = await runtimeRoot()
    const plan = await prepareMediatedOperationMounts(root, operationId)

    expect(() => validateRealizedMediatedMounts(plan, plan.mounts)).not.toThrow()

    const unsafeRealizations = [
      plan.mounts.slice(0, 1),
      [...plan.mounts, {
        kind: 'bind', source: root, target: '/host', readOnly: true,
      }],
      plan.mounts.map((mount, index) => index === 0 ? { ...mount, readOnly: false } : mount),
      plan.mounts.map((mount, index) => index === 1 ? { ...mount, source: root } : mount),
      plan.mounts.map((mount, index) => index === 0
        ? { ...mount, target: '/run/ancestryllm/operations' }
        : mount),
      plan.mounts.map((mount, index) => index === 0 ? { ...mount, kind: 'volume' } : mount),
    ]
    for (const realized of unsafeRealizations) {
      expect(() => validateRealizedMediatedMounts(plan, realized))
        .toThrow(expect.objectContaining({ code: 'MOUNT_MISMATCH' }))
    }
  })

  it('removes only exact stale operation directories during startup recovery', async () => {
    const root = await runtimeRoot()
    const secondOperationId = `op_${'b'.repeat(64)}`
    const first = await prepareMediatedOperationMounts(root, operationId)
    const second = await prepareMediatedOperationMounts(root, secondOperationId)

    await expect(cleanupStaleMediatedOperationMounts(root)).resolves.toBe(2)
    await expect(lstat(first.operationRoot)).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(lstat(second.operationRoot)).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(lstat(join(root, 'operation-staging'))).resolves.toMatchObject({})

    await writeFile(join(root, 'operation-staging', 'unexpected'), 'do not delete')
    await expect(cleanupStaleMediatedOperationMounts(root))
      .rejects.toMatchObject({ code: 'STAGING_UNSAFE' })
    await expect(lstat(join(root, 'operation-staging', 'unexpected'))).resolves.toMatchObject({})
  })
})
