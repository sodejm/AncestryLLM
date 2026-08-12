import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { test } from 'node:test'

const exposedRoots = [
  new URL('../src/preload/', import.meta.url),
  new URL('../src/renderer/', import.meta.url),
  new URL('../src/shared-contract/', import.meta.url),
]

async function readSources(roots, { includeTests = true } = {}) {
  const sources = []
  for (const root of roots) {
    const files = await readdir(root, { recursive: true })
    for (const file of files) {
      if (!/\.(?:html|ts|tsx)$/.test(file)) continue
      if (!includeTests && /\.test\.[cm]?[jt]sx?$/.test(file)) continue
      sources.push(await readFile(new URL(file, root), 'utf8'))
    }
  }
  return sources.join('\n')
}

test('keeps sidecar capabilities and diagnostics out of renderer and preload code', async () => {
  const exposedSource = await readSources(exposedRoots)
  assert.doesNotMatch(exposedSource, /bearerToken|AuthenticatedSidecarSession/)
  assert.doesNotMatch(exposedSource, /SidecarDiagnostics|sidecar-supervisor/)
  assert.doesNotMatch(exposedSource, /127\.0\.0\.1|Authorization:\s*Bearer/)
})

test('keeps host container authority out of renderer, preload, and shared contracts', async () => {
  const exposedSource = await readSources(exposedRoots)

  assert.doesNotMatch(exposedSource, /container-(?:supervisor|process)/)
  assert.doesNotMatch(exposedSource, /DockerCliHostControl|DOCKER_(?:HOST|CONTEXT)|docker\.sock/)
})

test('keeps credentials out of alternate Electron and browser stores', async () => {
  const productionSource = await readSources([new URL('../src/', import.meta.url)], {
    includeTests: false,
  })

  assert.doesNotMatch(productionSource, /\bsafeStorage\b/)
  assert.doesNotMatch(productionSource, /\b(?:localStorage|sessionStorage)\b/)
  assert.doesNotMatch(productionSource, /\bindexedDB\b/i)
  assert.doesNotMatch(productionSource, /\b(?:electron-store|keytar)\b/)
})
