/** Tests supported sidecar target mapping and packaged sidecar discovery rules. */
import assert from 'node:assert/strict'
import { chmod, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { test } from 'node:test'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  nativeTarget,
  sidecarExecutable,
  verifyPackagedSidecar,
} from './verify-sidecar.mjs'

test('maps only the four supported native platform targets', () => {
  assert.equal(nativeTarget('darwin', 'arm64'), 'darwin-arm64')
  assert.equal(nativeTarget('darwin', 'x64'), 'darwin-x64')
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
    sidecarExecutable('/bundle', 'win32-x64'),
    join('/bundle', 'win32-x64', 'ancestryllm-sidecar', 'ancestryllm-sidecar.exe'),
  )
})

test('finds exactly one sidecar under packaged Electron resources', async () => {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-verify-sidecar-'))
  const executable = join(
    root,
    'mac-arm64',
    'AncestryLLM.app',
    'Contents',
    'Resources',
    'sidecar',
    'darwin-arm64',
    'ancestryllm-sidecar',
    'ancestryllm-sidecar',
  )
  try {
    await mkdir(join(executable, '..'), { recursive: true })
    await writeFile(executable, 'fixture')
    await chmod(executable, 0o700)
    assert.equal(await verifyPackagedSidecar(root, 'darwin-arm64'), executable)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
