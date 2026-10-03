/** Compatibility entrypoint for the authoritative packaged WebdriverIO suite. */

import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { runWdioPlan, wdioInvocation } from './run-wdio.mjs'

/**
 * Constructs the packaged WebdriverIO invocation retained by release workflows.
 * @param {string[]} argv - Additional WebdriverIO arguments.
 * @param {{cliPath?: string, desktopRoot?: string, executable?: string}} [options] - Injectable paths for tests.
 * @returns {ReturnType<typeof wdioInvocation>} Validated child-process contract.
 */
export function packagedTestInvocation(argv, options = {}) {
  return wdioInvocation('packaged', argv, options)
}

/**
 * Runs the packaged WebdriverIO suite without a command shell.
 * @param {string[]} argv - Additional WebdriverIO arguments.
 * @param {{spawnSyncImpl?: Function, cliPath?: string, desktopRoot?: string, executable?: string}} [options] - Injectable runner and paths.
 * @returns {number} Integer child-process exit code.
 */
export function runPackagedTests(argv, options = {}) {
  return runWdioPlan('packaged', argv, options)
}

const entrypoint = process.argv[1]
  ? pathToFileURL(resolve(process.argv[1])).href
  : null
if (import.meta.url === entrypoint) {
  process.exitCode = runPackagedTests(process.argv.slice(2))
}
