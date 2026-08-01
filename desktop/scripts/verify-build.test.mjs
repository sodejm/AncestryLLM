import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { inspectBuild } from './verify-build.mjs'

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
