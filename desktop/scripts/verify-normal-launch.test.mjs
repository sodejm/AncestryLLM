/** Verifies the direct selected-packaged-runtime launch probe. */

import assert from 'node:assert/strict'
import test from 'node:test'
import {
  descendantProcessTree,
  nativeKeyringVerificationArguments,
  normalLaunchArguments,
  outputContainsWindowReadyRecord,
} from './verify-normal-launch.mjs'

test('window readiness requires the exact complete structured output line', () => {
  const record = '{"event":"ancestryllm.desktop.window-ready","version":1}'

  assert.equal(outputContainsWindowReadyRecord(`before\n${record}\nafter\n`), true)
  assert.equal(outputContainsWindowReadyRecord(`prefix ${record}\n`), false)
  assert.equal(outputContainsWindowReadyRecord(`${record.slice(0, -1)}\n`), false)
})

test('descendant process tree excludes unrelated and ancestor processes', () => {
  const records = [
    { pid: 1, ppid: 0, rssBytes: 1, commandLine: 'ancestor' },
    { pid: 10, ppid: 1, rssBytes: 2, commandLine: 'application' },
    { pid: 11, ppid: 10, rssBytes: 3, commandLine: 'renderer' },
    { pid: 12, ppid: 11, rssBytes: 4, commandLine: 'sidecar' },
    { pid: 20, ppid: 1, rssBytes: 5, commandLine: 'unrelated' },
  ]

  assert.deepEqual(
    descendantProcessTree(records, 10).map(({ pid }) => pid),
    [10, 11, 12],
  )
})

test('native keyring verifier selector is platform bounded', () => {
  assert.deepEqual(nativeKeyringVerificationArguments({
    environment: {},
    platform: 'darwin',
  }), ['--ancestryllm-macos-ephemeral-verification'])
  assert.deepEqual(nativeKeyringVerificationArguments({
    environment: {},
    platform: 'win32',
  }), [])
  assert.deepEqual(nativeKeyringVerificationArguments({
    environment: { ANCESTRYLLM_NATIVE_KEYRING_ROOT: '/tmp/keyring' },
    platform: 'linux',
  }), ['--ancestryllm-linux-keyring-verification-root=/tmp/keyring'])
  assert.throws(() => nativeKeyringVerificationArguments({
    environment: {},
    platform: 'linux',
  }), /absolute native keyring root/u)
})

test('normal launch arguments isolate data without exposing a debug control switch', () => {
  const args = normalLaunchArguments('/tmp/profile', {
    environment: {},
    platform: 'darwin',
  })

  assert.deepEqual(args, [
    '--use-mock-keychain',
    '--ancestryllm-macos-ephemeral-verification',
    '--user-data-dir=/tmp/profile',
    '--disk-cache-dir=/tmp/profile/chromium-cache',
    '--crash-dumps-dir=/tmp/profile/crash-dumps',
  ])
  assert.doesNotMatch(args.join('\n'), /--(?:remote-debugging|inspect)/u)
})
