import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  MANIFEST_SCHEMA,
  SidecarIntegrityError,
  verifySidecarPayload,
} from './sidecar-integrity'

const temporaryRoots: string[] = []

async function fixture(): Promise<{
  targetRoot: string
  expectedManifestSha256: string
}> {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-sidecar-integrity-'))
  temporaryRoots.push(root)
  const targetRoot = join(root, 'sidecar', 'darwin-arm64')
  const executable = join(targetRoot, 'ancestryllm-sidecar', 'ancestryllm-sidecar')
  await mkdir(join(executable, '..'), { recursive: true })
  const payload = Buffer.from('native fixture')
  await writeFile(executable, payload)
  const manifest = `${JSON.stringify({
    app_build: '0.5.0',
    files: [{
      bytes: payload.length,
      path: 'ancestryllm-sidecar/ancestryllm-sidecar',
      sha256: createHash('sha256').update(payload).digest('hex'),
      type: 'file',
    }],
    schema: MANIFEST_SCHEMA,
    target: 'darwin-arm64',
  })}\n`
  await writeFile(join(targetRoot, 'sidecar-manifest.json'), manifest)
  return {
    targetRoot,
    expectedManifestSha256: createHash('sha256').update(manifest).digest('hex'),
  }
}

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map(async (root) => {
    await rm(root, { recursive: true, force: true })
  }))
})

describe('packaged sidecar integrity', () => {
  it('accepts the exact manifest rooted in the immutable application build', async () => {
    const details = await fixture()

    await expect(verifySidecarPayload({
      ...details,
      expectedTarget: 'darwin-arm64',
      appBuild: '0.5.0',
    })).resolves.toBeUndefined()
  })

  it('rejects a rewritten adjacent manifest even when it matches a tampered payload', async () => {
    const details = await fixture()
    const executable = join(
      details.targetRoot,
      'ancestryllm-sidecar',
      'ancestryllm-sidecar',
    )
    const tampered = Buffer.from('attacker replacement')
    await writeFile(executable, tampered)
    await writeFile(join(details.targetRoot, 'sidecar-manifest.json'), `${JSON.stringify({
      app_build: '0.5.0',
      files: [{
        bytes: tampered.length,
        path: 'ancestryllm-sidecar/ancestryllm-sidecar',
        sha256: createHash('sha256').update(tampered).digest('hex'),
        type: 'file',
      }],
      schema: MANIFEST_SCHEMA,
      target: 'darwin-arm64',
    })}\n`)

    await expect(verifySidecarPayload({
      ...details,
      expectedTarget: 'darwin-arm64',
      appBuild: '0.5.0',
    })).rejects.toBeInstanceOf(SidecarIntegrityError)
  })

  it('rejects payload changes and unmanifested files', async () => {
    const details = await fixture()
    await writeFile(join(details.targetRoot, 'ancestryllm-sidecar', 'extra'), 'extra')

    await expect(verifySidecarPayload({
      ...details,
      expectedTarget: 'darwin-arm64',
      appBuild: '0.5.0',
    })).rejects.toBeInstanceOf(SidecarIntegrityError)
  })

  it('sanitizes a payload entry that disappears after the manifest is built', async () => {
    const details = await fixture()
    await rm(join(
      details.targetRoot,
      'ancestryllm-sidecar',
      'ancestryllm-sidecar',
    ))

    const verification = verifySidecarPayload({
      ...details,
      expectedTarget: 'darwin-arm64',
      appBuild: '0.5.0',
    })
    await expect(verification).rejects.toEqual(new SidecarIntegrityError())
    await expect(verification).rejects.not.toHaveProperty('code')
    await expect(verification).rejects.not.toHaveProperty('path')
  })

  it('rejects manifest symlinks', async () => {
    const details = await fixture()
    const manifest = join(details.targetRoot, 'sidecar-manifest.json')
    await rm(manifest)
    await symlink('../../../outside', manifest)

    await expect(verifySidecarPayload({
      ...details,
      expectedTarget: 'darwin-arm64',
      appBuild: '0.5.0',
    })).rejects.toBeInstanceOf(SidecarIntegrityError)
  })

  it('rejects payload symlinks that escape the target', async () => {
    const details = await fixture()
    const executable = join(
      details.targetRoot,
      'ancestryllm-sidecar',
      'ancestryllm-sidecar',
    )
    await rm(executable)
    await symlink('../../../../outside', executable)

    await expect(verifySidecarPayload({
      ...details,
      expectedTarget: 'darwin-arm64',
      appBuild: '0.5.0',
    })).rejects.toBeInstanceOf(SidecarIntegrityError)
  })
})
