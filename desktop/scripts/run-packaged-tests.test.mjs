/** Verifies the packaged WebdriverIO launcher preserves arguments without a shell. */

import assert from 'node:assert/strict'
import test from 'node:test'
import {
  packagedTestInvocation,
  runPackagedTests,
} from './run-packaged-tests.mjs'

test('packaged test invocation keeps a multiword grep filter as one argument', () => {
  const scenario = 'withholds and restores the packaged sidecar through Diagnostics retry'

  assert.deepEqual(
    packagedTestInvocation(['--grep', scenario], {
      cliPath: 'C:\\repo\\desktop\\node_modules\\@wdio\\cli\\bin\\wdio.js',
      desktopRoot: 'C:\\repo\\desktop',
      executable: 'C:\\Program Files\\nodejs\\node.exe',
    }),
    {
      executable: 'C:\\Program Files\\nodejs\\node.exe',
      args: [
        'C:\\repo\\desktop\\node_modules\\@wdio\\cli\\bin\\wdio.js',
        'run',
        'wdio.conf.ts',
        '--suite',
        'packaged',
        '--mochaOpts.grep',
        scenario,
      ],
      cwd: 'C:\\repo\\desktop',
      env: { ...process.env, ANCESTRYLLM_WDIO_MODE: 'packaged' },
      shell: false,
    },
  )
})

test('packaged test runner executes the exact invocation without a shell', () => {
  const calls = []
  const status = runPackagedTests(['--grep', 'withholds and restores'], {
    cliPath: '/repo/desktop/node_modules/@wdio/cli/bin/wdio.js',
    desktopRoot: '/repo/desktop',
    executable: '/usr/bin/node',
    userDataDirectory: '/tmp/ancestryllm-test-profile',
    preparePackagedScenarioImpl(scenario, environment) {
      assert.equal(
        scenario,
        'withholds and restores the packaged sidecar through Diagnostics retry',
      )
      return {
        environment: {
          ...environment,
          ANCESTRYLLM_PACKAGED_EXECUTABLE: '/repo/release/ancestryllm',
        },
      }
    },
    spawnSyncImpl(executable, args, options) {
      calls.push({ executable, args, options })
      return { error: undefined, signal: null, status: 0 }
    },
  })

  assert.equal(status, 0)
  assert.deepEqual(calls, [{
    executable: '/usr/bin/node',
    args: [
      '/repo/desktop/node_modules/@wdio/cli/bin/wdio.js',
      'run',
      'wdio.conf.ts',
      '--suite',
      'packaged',
      '--mochaOpts.grep',
      'withholds and restores',
    ],
    options: {
      cwd: '/repo/desktop',
      env: {
        ...process.env,
        ANCESTRYLLM_DESKTOP_FIXTURE: 'success',
        ANCESTRYLLM_PACKAGED_EXECUTABLE: '/repo/release/ancestryllm',
        ANCESTRYLLM_WDIO_USER_DATA: '/tmp/ancestryllm-test-profile',
        ANCESTRYLLM_WDIO_MODE: 'packaged',
      },
      shell: false,
      stdio: 'inherit',
    },
  }])
})

test('packaged test runner preserves WebDriver options without passing them to the direct verifier', () => {
  const calls = []
  const scenarios = []
  const status = runPackagedTests(['--logLevel', 'debug'], {
    cliPath: '/repo/desktop/node_modules/@wdio/cli/bin/wdio.js',
    desktopRoot: '/repo/desktop',
    executable: '/usr/bin/node',
    userDataDirectory: '/tmp/ancestryllm-test-profile',
    preparePackagedScenarioImpl(scenario, environment) {
      scenarios.push(scenario)
      return { environment }
    },
    spawnSyncImpl(executable, args, options) {
      calls.push({ executable, args, options })
      return { error: undefined, signal: null, status: 0 }
    },
  })

  assert.equal(status, 0)
  assert.equal(calls.length, 6)
  assert.equal(scenarios.length, 6)
  assert.equal(new Set(scenarios).size, 6)
  assert.equal(calls.slice(0, -1).every(({ args }) => (
    args.includes('--mochaOpts.grep')
      && args.includes('--logLevel')
      && args.includes('debug')
  )), true)
  assert.deepEqual(calls.at(-1)?.args, [
    '/repo/desktop/scripts/verify-normal-launch.mjs',
  ])
})
