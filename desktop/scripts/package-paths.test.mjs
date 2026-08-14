/** Verifies packaged application discovery across Linux, macOS, and Windows layouts. */
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { discoverPackage } from './package-paths.mjs'

async function fixture(platform) {
  const root = await mkdtemp(join(tmpdir(), `ancestryllm-${platform}-`))
  const resources = platform === 'darwin'
    ? join(root, 'mac', 'AncestryLLM.app', 'Contents', 'Resources')
    : join(root, platform === 'win32' ? 'win-unpacked' : 'linux-unpacked', 'resources')
  const executable = platform === 'darwin'
    ? join(root, 'mac', 'AncestryLLM.app', 'Contents', 'MacOS', 'ancestryllm')
    : join(resources, '..', platform === 'win32' ? 'ancestryllm.exe' : 'ancestryllm')
  await mkdir(resources, { recursive: true })
  await mkdir(join(executable, '..'), { recursive: true })
  await writeFile(join(resources, 'app.asar'), 'fixture')
  await writeFile(executable, 'fixture')
  return { root, resources, executable }
}

for (const platform of ['darwin', 'win32', 'linux']) {
  test(`discovers the electron-builder executable for ${platform}`, async () => {
    const expected = await fixture(platform)
    assert.deepEqual(await discoverPackage(expected.root, platform), {
      resources: expected.resources,
      executable: expected.executable,
    })
  })
}

test('fails closed when a resources directory has no exact executable', async () => {
  const expected = await fixture('linux')
  await writeFile(join(expected.resources, '..', 'ancestryllm-desktop'), 'wrong-name')
  const missingRoot = await mkdtemp(join(tmpdir(), 'ancestryllm-missing-'))
  const resources = join(missingRoot, 'linux-unpacked', 'resources')
  await mkdir(resources, { recursive: true })
  await writeFile(join(resources, 'app.asar'), 'fixture')
  await assert.rejects(discoverPackage(missingRoot, 'linux'), /ancestryllm/)
})
