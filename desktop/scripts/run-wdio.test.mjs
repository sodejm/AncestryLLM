/** Verifies isolated WebdriverIO scenario orchestration. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { runWdio, runWdioPlan, wdioInvocation } from './run-wdio.mjs'

const runnerOptions = (calls) => ({
  cliPath: '/repo/desktop/node_modules/@wdio/cli/bin/wdio.js',
  desktopRoot: '/repo/desktop',
  executable: '/usr/bin/node',
  mkdtempSyncImpl: () => '/tmp/ancestryllm-isolated-profile',
  preparePackagedScenarioImpl(scenario, environment) {
    calls.push({ preparation: { scenario, environment } })
    return {
      environment: {
        ...environment,
        ANCESTRYLLM_PACKAGED_EXECUTABLE: '/repo/release/ancestryllm',
      },
      cleanupPath: '/tmp/ancestryllm-packaged-copy',
    }
  },
  rmSyncImpl(path, options) { calls.push({ cleanup: { path, options } }) },
  spawnSyncImpl(executable, args, options) {
    calls.push({ executable, args, options })
    return { error: undefined, signal: null, status: 0 }
  },
})

test('default invocation resolves WebdriverIO through its public package export', () => {
  const invocation = wdioInvocation('source', [])

  assert.match(invocation.args[0], /node_modules\/@wdio\/cli\/bin\/wdio\.js$/u)
})

test('pnpm separator is not forwarded to WebdriverIO', () => {
  const invocation = wdioInvocation('source', [
    '--',
    '--grep',
    'task center streams one safe cancellation lifecycle',
  ])

  assert.deepEqual(invocation.args.slice(-2), [
    '--mochaOpts.grep',
    'task center streams one safe cancellation lifecycle',
  ])
  assert.equal(invocation.args.includes('--'), false)
})

test('source run selects the degraded fixture and removes its isolated profile', () => {
  const calls = []
  const status = runWdio('source', ['--grep', 'built degraded shell'], runnerOptions(calls))

  assert.equal(status, 0)
  assert.equal(calls.length, 2)
  assert.equal(calls[0].options.env.ANCESTRYLLM_DESKTOP_FIXTURE, 'degraded')
  assert.equal(calls[0].options.env.ANCESTRYLLM_WDIO_USER_DATA, '/tmp/ancestryllm-isolated-profile')
  assert.equal(calls[0].options.env.ANCESTRYLLM_WDIO_LAUNCH_STARTED_AT, undefined)
  assert.deepEqual(calls[1], {
    cleanup: {
      path: '/tmp/ancestryllm-isolated-profile',
      options: { force: true, recursive: true },
    },
  })
})

test('packaged preparation failure still removes the isolated profile', () => {
  const calls = []
  const options = {
    ...runnerOptions(calls),
    preparePackagedScenarioImpl() {
      throw new Error('package preparation failed')
    },
  }

  assert.throws(
    () => runWdio('packaged', ['--grep', 'packaged scenario'], options),
    /package preparation failed/u,
  )
  assert.deepEqual(calls, [{
    cleanup: {
      path: '/tmp/ancestryllm-isolated-profile',
      options: { force: true, recursive: true },
    },
  }])
})

test('complete source plan runs every scenario through a fresh profile', () => {
  const calls = []
  const status = runWdioPlan('source', [], runnerOptions(calls))
  const invocations = calls.filter((call) => call.args !== undefined)
  const cleanups = calls.filter((call) => call.cleanup !== undefined)

  assert.equal(status, 0)
  assert.equal(invocations.length, 6)
  assert.equal(cleanups.length, 6)
  assert.equal(invocations.every(({ args }) => args.includes('--mochaOpts.grep')), true)
})

test('complete packaged plan prepares and cleans every isolated scenario', () => {
  const calls = []
  const status = runWdioPlan('packaged', [], runnerOptions(calls))
  const invocations = calls.filter((call) => call.args !== undefined)
  const preparations = calls.filter((call) => call.preparation !== undefined)
  const packageCleanups = calls.filter((call) => (
    call.cleanup?.path === '/tmp/ancestryllm-packaged-copy'
  ))

  assert.equal(status, 0)
  assert.equal(invocations.length, 6)
  assert.equal(preparations.length, 6)
  assert.equal(packageCleanups.length, 6)
  assert.equal(invocations.every(({ options }) => (
    options.env.ANCESTRYLLM_PACKAGED_EXECUTABLE === '/repo/release/ancestryllm'
  )), true)
})
