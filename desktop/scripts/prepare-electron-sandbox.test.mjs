/** Verifies fail-closed Linux Chromium sandbox preparation. */

import assert from 'node:assert/strict'
import test from 'node:test'
import {
  installedElectronDistribution,
  prepareLinuxElectronSandbox,
} from './prepare-electron-sandbox.mjs'

const fileStatus = (overrides = {}) => ({
  gid: 501,
  isFile: () => true,
  isSymbolicLink: () => false,
  mode: 0o100755,
  uid: 501,
  ...overrides,
})

test('Linux preparation applies and verifies the Chromium SUID sandbox contract', () => {
  const calls = []
  const states = [fileStatus(), fileStatus({ gid: 0, mode: 0o104755, uid: 0 })]

  prepareLinuxElectronSandbox('/repo/desktop/node_modules/electron/dist', {
    platform: 'linux',
    execFileSyncImpl(command, args, options) { calls.push({ args, command, options }) },
    lstatSyncImpl(path) {
      assert.equal(path, '/repo/desktop/node_modules/electron/dist/chrome-sandbox')
      return states.shift()
    },
  })

  assert.deepEqual(calls, [
    {
      command: 'sudo',
      args: [
        '--non-interactive', 'chown', 'root:root', '--',
        '/repo/desktop/node_modules/electron/dist/chrome-sandbox',
      ],
      options: { stdio: 'inherit' },
    },
    {
      command: 'sudo',
      args: [
        '--non-interactive', 'chmod', '4755', '--',
        '/repo/desktop/node_modules/electron/dist/chrome-sandbox',
      ],
      options: { stdio: 'inherit' },
    },
  ])
  assert.equal(states.length, 0)
})

test('non-Linux preparation performs no filesystem or privilege operation', () => {
  prepareLinuxElectronSandbox('/repo/desktop/node_modules/electron/dist', {
    platform: 'darwin',
    execFileSyncImpl() { assert.fail('must not invoke sudo') },
    lstatSyncImpl() { assert.fail('must not inspect the sandbox') },
  })
})

test('Linux preparation rejects unsafe sandbox file types before invoking sudo', () => {
  for (const [status, expected] of [
    [fileStatus({ isSymbolicLink: () => true }), /must not be a symlink/u],
    [fileStatus({ isFile: () => false }), /must be a regular file/u],
  ]) {
    assert.throws(() => prepareLinuxElectronSandbox('/repo/electron/dist', {
      platform: 'linux',
      execFileSyncImpl() { assert.fail('must not invoke sudo') },
      lstatSyncImpl: () => status,
    }), expected)
  }
})

test('Linux preparation rejects incomplete owner and mode changes', () => {
  for (const [prepared, expected] of [
    [fileStatus({ gid: 0, mode: 0o104755, uid: 501 }), /must be owned by root/u],
    [fileStatus({ gid: 501, mode: 0o104755, uid: 0 }), /must use the root group/u],
    [fileStatus({ gid: 0, mode: 0o100755, uid: 0 }), /must be mode 4755/u],
  ]) {
    const states = [fileStatus(), prepared]
    assert.throws(() => prepareLinuxElectronSandbox('/repo/electron/dist', {
      platform: 'linux',
      execFileSyncImpl() {},
      lstatSyncImpl: () => states.shift(),
    }), expected)
  }
})

test('installed Electron distribution is resolved from the package entry point', () => {
  assert.equal(installedElectronDistribution(
    (specifier) => {
      assert.equal(specifier, 'electron')
      return '/repo/desktop/node_modules/electron/index.js'
    },
  ), '/repo/desktop/node_modules/electron/dist')
})
