/** Ensures accessibility and visual-review commands select checked-in scenarios. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const packageJson = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))
const shellSpec = await readFile(new URL('../e2e/shell.spec.ts', import.meta.url), 'utf8')

for (const scriptName of ['test:accessibility', 'test:visual']) {
  test(`${scriptName} selects at least one checked-in Electron scenario`, () => {
    const script = packageJson.scripts[scriptName]
    const grep = script.match(/--grep '([^']+)'/u)?.[1]

    assert.ok(grep, `${scriptName} must select a bounded Playwright scenario with --grep`)
    assert.match(shellSpec, new RegExp(grep.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&'), 'u'))
  })
}
