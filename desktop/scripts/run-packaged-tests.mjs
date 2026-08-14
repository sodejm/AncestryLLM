/** Launches the packaged Playwright suite with exact argv and no command shell. */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const require = createRequire(import.meta.url)
const defaultDesktopRoot = fileURLToPath(new URL('../', import.meta.url))

/**
 * Constructs a no-shell Playwright invocation limited to the packaged desktop suite.
 * @param {string[]} argv - Additional Playwright arguments passed without shell interpretation.
 * @param {{cliPath?: string, desktopRoot?: string, executable?: string}} [options] - Injectable executable and paths for tests.
 * @returns {Readonly<{executable: string, args: readonly string[], cwd: string, shell: false}>} Validated child-process contract.
 */
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

/**
 * Runs the packaged Playwright suite without a shell and preserves its actual exit status.
 * @param {string[]} argv - Additional Playwright arguments passed verbatim.
 * @param {{spawnSyncImpl?: Function, cliPath?: string, desktopRoot?: string, executable?: string}} [options] - Injectable runner and invocation paths for tests.
 * @returns {number} Integer child-process exit code; spawn and signal failures throw.
 */
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
