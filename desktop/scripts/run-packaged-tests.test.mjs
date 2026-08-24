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
  const status = runPackagedTests(['--grep', 'multi word filter'], {
    cliPath: '/repo/desktop/node_modules/@wdio/cli/bin/wdio.js',
    desktopRoot: '/repo/desktop',
    executable: '/usr/bin/node',
    nowImpl: () => 1_787_551_078_620,
    userDataDirectory: '/tmp/ancestryllm-test-profile',
    preparePackagedScenarioImpl(scenario, environment) {
      assert.equal(scenario, 'multi word filter')
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
      'multi word filter',
    ],
    options: {
      cwd: '/repo/desktop',
      env: {
        ...process.env,
        ANCESTRYLLM_DESKTOP_FIXTURE: 'success',
        ANCESTRYLLM_PACKAGED_EXECUTABLE: '/repo/release/ancestryllm',
        ANCESTRYLLM_WDIO_LAUNCH_STARTED_AT: '1787551078620',
        ANCESTRYLLM_WDIO_USER_DATA: '/tmp/ancestryllm-test-profile',
        ANCESTRYLLM_WDIO_MODE: 'packaged',
      },
      shell: false,
      stdio: 'inherit',
    },
  }])
})
