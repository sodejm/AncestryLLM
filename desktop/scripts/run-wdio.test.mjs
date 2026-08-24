/** Verifies isolated WebdriverIO scenario orchestration. */

import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import {
  preparePackagedScenario,
  runWdio,
  runWdioPlan,
  wdioInvocation,
} from './run-wdio.mjs'

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
      options: { force: true, maxRetries: 10, recursive: true, retryDelay: 100 },
    },
  })
})

test('source grep derives the degraded fixture from the canonical scenario', () => {
  const calls = []
  const status = runWdio('source', ['--grep', 'bounded recovery'], runnerOptions(calls))

  assert.equal(status, 0)
  assert.equal(calls[0].options.env.ANCESTRYLLM_DESKTOP_FIXTURE, 'degraded')
})

test('source grep rejects zero declared scenario matches before invocation', () => {
  const calls = []

  assert.throws(
    () => runWdio('source', ['--grep', 'missing scenario'], runnerOptions(calls)),
    /must match exactly one declared source scenario; matched 0/u,
  )
  assert.equal(calls.some((call) => call.args !== undefined), false)
})

test('source grep rejects multiple declared scenario matches before invocation', () => {
  const calls = []

  assert.throws(
    () => runWdio('source', ['--grep', 'built'], runnerOptions(calls)),
    /must match exactly one declared source scenario; matched 4/u,
  )
  assert.equal(calls.some((call) => call.args !== undefined), false)
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
    () => runWdio('packaged', ['--grep', 'exercises first run'], options),
    /package preparation failed/u,
  )
  assert.deepEqual(calls, [{
    cleanup: {
      path: '/tmp/ancestryllm-isolated-profile',
      options: { force: true, maxRetries: 10, recursive: true, retryDelay: 100 },
    },
  }])
})

test('packaged cleanup failure still removes the isolated profile', () => {
  const calls = []
  const options = {
    ...runnerOptions(calls),
    rmSyncImpl(path, cleanupOptions) {
      calls.push({ cleanup: { path, options: cleanupOptions } })
      if (path === '/tmp/ancestryllm-packaged-copy') {
        throw new Error('package cleanup failed')
      }
    },
  }

  assert.throws(
    () => runWdio('packaged', ['--grep', 'exercises first run'], options),
    /package cleanup failed/u,
  )
  assert.deepEqual(calls.filter((call) => call.cleanup !== undefined), [
    {
      cleanup: {
        path: '/tmp/ancestryllm-packaged-copy',
        options: { force: true, maxRetries: 10, recursive: true, retryDelay: 100 },
      },
    },
    {
      cleanup: {
        path: '/tmp/ancestryllm-isolated-profile',
        options: { force: true, maxRetries: 10, recursive: true, retryDelay: 100 },
      },
    },
  ])
})

test('packaged grep resolves one declared scenario before package preparation', () => {
  const calls = []
  const status = runWdio('packaged', ['--grep', 'withholds'], runnerOptions(calls))

  assert.equal(status, 0)
  assert.equal(
    calls.find((call) => call.preparation !== undefined)?.preparation.scenario,
    'withholds and restores the packaged sidecar through Diagnostics retry',
  )
})

test('packaged grep rejects zero declared scenario matches before preparation', () => {
  const calls = []

  assert.throws(
    () => runWdio('packaged', ['--grep', 'missing scenario'], runnerOptions(calls)),
    /must match exactly one declared packaged scenario; matched 0/u,
  )
  assert.equal(calls.some((call) => call.preparation !== undefined), false)
  assert.equal(calls.some((call) => call.args !== undefined), false)
})

test('packaged grep rejects multiple declared scenario matches before preparation', () => {
  const calls = []

  assert.throws(
    () => runWdio('packaged', ['--grep', 'packaged'], runnerOptions(calls)),
    /must match exactly one declared packaged scenario; matched 4/u,
  )
  assert.equal(calls.some((call) => call.preparation !== undefined), false)
  assert.equal(calls.some((call) => call.args !== undefined), false)
})

test('packaged preparation cleanup retries transient Windows file locks', () => {
  const sourceRoot = mkdtempSync(join(tmpdir(), 'ancestryllm-wdio-source-'))
  const cleanupRoot = mkdtempSync(join(tmpdir(), 'ancestryllm-wdio-copy-'))
  const packageRoot = process.platform === 'darwin'
    ? join(sourceRoot, 'AncestryLLM.app')
    : join(sourceRoot, 'package')
  const executable = process.platform === 'darwin'
    ? join(packageRoot, 'Contents', 'MacOS', 'AncestryLLM')
    : join(packageRoot, process.platform === 'win32' ? 'AncestryLLM.exe' : 'ancestryllm')
  const cleanupCalls = []
  mkdirSync(join(packageRoot, process.platform === 'darwin' ? 'Contents/MacOS' : ''), {
    recursive: true,
  })
  writeFileSync(executable, '')
  if (process.platform === 'linux') writeFileSync(join(packageRoot, 'chrome-sandbox'), '')

  try {
    assert.throws(() => preparePackagedScenario(
      'withholds and restores the packaged sidecar through Diagnostics retry',
      { ANCESTRYLLM_PACKAGED_APP: executable },
      {
        execFileSyncImpl() { throw new Error('package preparation failed') },
        mkdtempSyncImpl: () => cleanupRoot,
        rmSyncImpl(path, options) { cleanupCalls.push({ path, options }) },
      },
    ))
    assert.deepEqual(cleanupCalls, [{
      path: cleanupRoot,
      options: { force: true, maxRetries: 10, recursive: true, retryDelay: 100 },
    }])
  } finally {
    rmSync(sourceRoot, { force: true, recursive: true })
    rmSync(cleanupRoot, { force: true, recursive: true })
  }
})

test('packaged preparation cleanup failure preserves the preparation error', () => {
  const sourceRoot = mkdtempSync(join(tmpdir(), 'ancestryllm-wdio-source-'))
  const cleanupRoot = mkdtempSync(join(tmpdir(), 'ancestryllm-wdio-copy-'))
  const packageRoot = process.platform === 'darwin'
    ? join(sourceRoot, 'AncestryLLM.app')
    : join(sourceRoot, 'package')
  const executable = process.platform === 'darwin'
    ? join(packageRoot, 'Contents', 'MacOS', 'AncestryLLM')
    : join(packageRoot, process.platform === 'win32' ? 'AncestryLLM.exe' : 'ancestryllm')
  const preparationError = new Error('package preparation failed')
  const cleanupError = new Error('package cleanup failed')
  mkdirSync(join(packageRoot, process.platform === 'darwin' ? 'Contents/MacOS' : ''), {
    recursive: true,
  })
  writeFileSync(executable, '')
  if (process.platform === 'linux') writeFileSync(join(packageRoot, 'chrome-sandbox'), '')

  try {
    assert.throws(
      () => preparePackagedScenario(
        'withholds and restores the packaged sidecar through Diagnostics retry',
        { ANCESTRYLLM_PACKAGED_APP: executable },
        {
          execFileSyncImpl() { throw preparationError },
          mkdtempSyncImpl: () => cleanupRoot,
          rmSyncImpl() { throw cleanupError },
        },
      ),
      (error) => {
        if (process.platform === 'win32') {
          assert.notEqual(error, cleanupError)
          assert.match(error.message, /ENOENT/u)
        } else {
          assert.equal(error, preparationError)
        }
        return true
      },
    )
  } finally {
    rmSync(sourceRoot, { force: true, recursive: true })
    rmSync(cleanupRoot, { force: true, recursive: true })
  }
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

test('non-filter options are forwarded to every isolated packaged scenario', () => {
  const calls = []
  const status = runWdioPlan('packaged', ['--logLevel', 'debug'], runnerOptions(calls))
  const invocations = calls.filter((call) => call.args !== undefined)
  const preparations = calls
    .filter((call) => call.preparation !== undefined)
    .map(({ preparation }) => preparation.scenario)

  assert.equal(status, 0)
  assert.equal(invocations.length, 6)
  assert.equal(preparations.length, 6)
  assert.equal(new Set(preparations).size, 6)
  assert.equal(preparations.every((scenario) => scenario.length > 0), true)
  assert.equal(invocations.every(({ args }) => (
    args.includes('--mochaOpts.grep')
      && args.includes('--logLevel')
      && args.includes('debug')
  )), true)
})
