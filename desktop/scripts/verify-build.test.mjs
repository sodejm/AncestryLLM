import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { inspectBuild, resolveBuildOutputPath } from './verify-build.mjs'

test('build output path converts a Windows file URL without duplicating the drive prefix', () => {
  assert.equal(
    resolveBuildOutputPath(
      'file:///C:/a/AncestryLLM/AncestryLLM/desktop/scripts/verify-build.mjs',
      { windows: true },
    ),
    String.raw`C:\a\AncestryLLM\AncestryLLM\desktop\out`,
  )
})

test('build inspection rejects development copy, source maps, remote assets, credentials, and updater metadata', async () => {
  for (const [name, contents] of [
    ['renderer.js', 'const heading = "Component gallery"'],
    ['app.js.map', '{}'],
    ['app.js', 'fetch("https://remote.invalid/api")'],
    ['index.html', '<script src="https://remote.invalid/app.js"></script>'],
    ['credentials.txt', 'password=fake'],
    ['latest.yml', 'version: 1'],
  ]) {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-build-'))
    await mkdir(join(root, 'out'))
    await writeFile(join(root, 'out', name), contents)
    await assert.rejects(inspectBuild(join(root, 'out')))
  }
})

test('production build inspection rejects fixture bridge and test-hook machinery', async () => {
  for (const contents of [
    'createMockAncestryBridge("success")',
    'process.env.ANCESTRYLLM_DESKTOP_FIXTURE',
    'process.env.ANCESTRYLLM_DESKTOP_SECURITY_E2E',
    'globalThis.__ancestryllmSecurityStateForTests = () => ({})',
  ]) {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-build-'))
    await mkdir(join(root, 'out'))
    await writeFile(join(root, 'out', 'index.js'), contents)
    await assert.rejects(inspectBuild(join(root, 'out')))
  }
})
