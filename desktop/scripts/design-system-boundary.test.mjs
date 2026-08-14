/** Enforces the renderer design system's presentation-only trust boundary. */

import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const repositoryRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const designSystemRoot = join(repositoryRoot, 'src', 'renderer', 'src', 'design-system')
const forbidden = [
  [/from\s+['"](?:node:|electron)/u, 'Node or Electron import'],
  [/\bancestryBridge\b|\bwindow\.ancestry\b/u, 'desktop bridge access'],
  [/\bfetch\s*\(|\bWebSocket\b|\bXMLHttpRequest\b|\bEventSource\b/u, 'network primitive'],
  [/dangerouslySetInnerHTML/u, 'raw HTML rendering'],
]

test('design-system production modules remain presentation-only', async () => {
  const names = (await readdir(designSystemRoot))
    .filter((name) => /\.(?:ts|tsx)$/u.test(name) && !name.includes('.test.'))
    .sort()

  assert.ok(names.length > 0, 'expected design-system production modules')
  for (const name of names) {
    const source = await readFile(join(designSystemRoot, name), 'utf8')
    for (const [pattern, description] of forbidden) {
      assert.doesNotMatch(source, pattern, `${name} must not contain ${description}`)
    }
    assert.doesNotMatch(source, /(?:^|\n)\s*import\s+.*mock-bridge/u, `${name} must not import fixtures`)
  }
})
