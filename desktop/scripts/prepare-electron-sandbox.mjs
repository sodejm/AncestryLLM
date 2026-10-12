/** Prepares and verifies the Linux Chromium SUID sandbox without weakening Electron. */

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { lstatSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(import.meta.url)

/**
 * Applies and verifies the Chromium SUID sandbox contract for Linux Electron.
 *
 * @param {string} electronDistributionRoot Electron distribution containing chrome-sandbox.
 * @param {object} [options] Injectable platform and native filesystem operations.
 * @returns {void}
 */
export function prepareLinuxElectronSandbox(electronDistributionRoot, {
  platform = process.platform,
  execFileSyncImpl = execFileSync,
  lstatSyncImpl = lstatSync,
} = {}) {
  if (platform !== 'linux') return
  const sandboxPath = join(electronDistributionRoot, 'chrome-sandbox')
  const sandbox = lstatSyncImpl(sandboxPath)
  assert.equal(sandbox.isSymbolicLink(), false, 'Chromium sandbox must not be a symlink')
  assert.equal(sandbox.isFile(), true, 'Chromium sandbox must be a regular file')
  execFileSyncImpl('sudo', [
    '--non-interactive', 'chown', 'root:root', '--', sandboxPath,
  ], { stdio: 'inherit' })
  execFileSyncImpl('sudo', [
    '--non-interactive', 'chmod', '4755', '--', sandboxPath,
  ], { stdio: 'inherit' })
  const prepared = lstatSyncImpl(sandboxPath)
  assert.equal(prepared.uid, 0, 'Chromium sandbox must be owned by root')
  assert.equal(prepared.gid, 0, 'Chromium sandbox must use the root group')
  assert.equal(prepared.mode & 0o7777, 0o4755, 'Chromium sandbox must be mode 4755')
}

/**
 * Resolves the installed Electron distribution from its public package entry point.
 *
 * @param {Function} [requireResolveImpl] Injectable module resolver.
 * @returns {string} Absolute Electron distribution path.
 */
export function installedElectronDistribution(requireResolveImpl = require.resolve) {
  return join(dirname(requireResolveImpl('electron')), 'dist')
}

/**
 * Prepares the installed Electron distribution's Linux Chromium SUID sandbox.
 *
 * @param {object} [options] Injectable resolver, platform, and native operations.
 * @returns {void}
 */
export function prepareInstalledElectronSandbox(options = {}) {
  const { requireResolveImpl = require.resolve, ...preparationOptions } = options
  prepareLinuxElectronSandbox(
    installedElectronDistribution(requireResolveImpl),
    preparationOptions,
  )
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  prepareInstalledElectronSandbox()
}
