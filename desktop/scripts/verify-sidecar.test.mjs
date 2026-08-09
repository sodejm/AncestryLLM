import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { chmod, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { test } from 'node:test'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  nativeTarget,
  sidecarExecutable,
  verifySidecar,
  verifyPackagedSidecar,
} from './verify-sidecar.mjs'

async function writeFixture(sidecarRoot, target = 'darwin-arm64') {
  const executable = sidecarExecutable(sidecarRoot, target)
  const payload = Buffer.from('fixture')
  await mkdir(join(executable, '..'), { recursive: true })
  await writeFile(executable, payload)
  await chmod(executable, 0o700)
  await writeFile(join(sidecarRoot, target, 'sidecar-manifest.json'), `${JSON.stringify({
    app_build: '0.5.0',
    files: [{
      bytes: payload.length,
      path: `ancestryllm-sidecar/${target.startsWith('win32-') ? 'ancestryllm-sidecar.exe' : 'ancestryllm-sidecar'}`,
      sha256: createHash('sha256').update(payload).digest('hex'),
      type: 'file',
    }],
    schema: 'ancestryllm.sidecar-payload/1',
    target,
  })}\n`)
  return executable
}

test('maps supported native platform targets, including Windows ARM64', () => {
  assert.equal(nativeTarget('darwin', 'arm64'), 'darwin-arm64')
  assert.equal(nativeTarget('darwin', 'x64'), 'darwin-x64')
  assert.equal(nativeTarget('win32', 'arm64'), 'win32-arm64')
  assert.equal(nativeTarget('win32', 'x64'), 'win32-x64')
  assert.equal(nativeTarget('linux', 'x64'), 'linux-x64')
  assert.throws(() => nativeTarget('linux', 'arm64'), /Unsupported desktop target/)
})

test('matches the resource path used by Electron main', () => {
  assert.equal(
    sidecarExecutable('/bundle', 'darwin-arm64'),
    join('/bundle', 'darwin-arm64', 'ancestryllm-sidecar', 'ancestryllm-sidecar'),
  )
  assert.equal(
    sidecarExecutable('/bundle', 'win32-arm64'),
    join('/bundle', 'win32-arm64', 'ancestryllm-sidecar', 'ancestryllm-sidecar.exe'),
  )
  assert.equal(
    sidecarExecutable('/bundle', 'win32-x64'),
    join('/bundle', 'win32-x64', 'ancestryllm-sidecar', 'ancestryllm-sidecar.exe'),
  )
})

test('finds exactly one sidecar under packaged Electron resources', async () => {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-verify-sidecar-'))
  const sidecarRoot = join(
    root,
    'mac-arm64',
    'AncestryLLM.app',
    'Contents',
    'Resources',
    'sidecar',
  )
  try {
    const executable = await writeFixture(sidecarRoot)
    assert.equal(await verifyPackagedSidecar(root, 'darwin-arm64'), executable)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('rejects unmanifested or changed sidecar payload files', async () => {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-verify-sidecar-'))
  try {
    await writeFixture(root)
    await writeFile(join(root, 'darwin-arm64', 'ancestryllm-sidecar', 'extra'), 'extra')
    await assert.rejects(
      verifySidecar(root, 'darwin-arm64'),
      /inventory differs/,
    )
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
