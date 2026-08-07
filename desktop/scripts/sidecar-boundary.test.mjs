/** Prevents sidecar session details and authenticated transport data from leaking into preload or renderer sources. */
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { test } from 'node:test'

const exposedRoots = [
  new URL('../src/preload/', import.meta.url),
  new URL('../src/renderer/', import.meta.url),
  new URL('../src/shared-contract/', import.meta.url),
]

test('keeps sidecar capabilities and diagnostics out of renderer and preload code', async () => {
  const sources = []
  for (const root of exposedRoots) {
    const files = await readdir(root, { recursive: true })
    for (const file of files) {
      if (!/\.(?:html|ts|tsx)$/.test(file)) continue
      sources.push(await readFile(new URL(file, root), 'utf8'))
    }
  }

  const exposedSource = sources.join('\n')
  assert.doesNotMatch(exposedSource, /bearerToken|AuthenticatedSidecarSession/)
  assert.doesNotMatch(exposedSource, /SidecarDiagnostics|sidecar-supervisor/)
  assert.doesNotMatch(exposedSource, /127\.0\.0\.1|Authorization:\s*Bearer/)
})
