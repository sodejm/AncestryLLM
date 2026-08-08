// Launches the packaged Playwright suite with exact argv and no command shell.

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const require = createRequire(import.meta.url)
const defaultDesktopRoot = fileURLToPath(new URL('../', import.meta.url))

export function packagedTestInvocation(argv, {
  cliPath = require.resolve('@playwright/test/cli'),
  desktopRoot = defaultDesktopRoot,
  executable = process.execPath,
} = {}) {
  assert.equal(Array.isArray(argv), true, 'packaged test arguments must be an array')
  assert.equal(argv.every((item) => typeof item === 'string'), true, 'packaged test arguments must be strings')
  return Object.freeze({
    executable,
    args: Object.freeze([
      cliPath,
      'test',
      'e2e/packaged-shell.spec.ts',
      ...argv,
    ]),
    cwd: desktopRoot,
    shell: false,
  })
}

export function runPackagedTests(argv, {
  spawnSyncImpl = spawnSync,
  ...invocationOptions
} = {}) {
  const invocation = packagedTestInvocation(argv, invocationOptions)
  const result = spawnSyncImpl(invocation.executable, invocation.args, {
    cwd: invocation.cwd,
    env: process.env,
    shell: invocation.shell,
    stdio: 'inherit',
  })
  if (result.error) throw result.error
  assert.equal(result.signal, null, `packaged Playwright test terminated by signal ${result.signal}`)
  assert.equal(Number.isInteger(result.status), true, 'packaged Playwright test did not report an exit status')
  return result.status
}

const entrypoint = process.argv[1]
  ? pathToFileURL(resolve(process.argv[1])).href
  : null
if (import.meta.url === entrypoint) {
  process.exitCode = runPackagedTests(process.argv.slice(2))
}
