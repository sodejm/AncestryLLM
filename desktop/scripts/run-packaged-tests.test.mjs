/** Verifies the packaged Playwright launcher preserves arguments without a shell. */

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
      cliPath: 'C:\\repo\\desktop\\node_modules\\@playwright\\test\\cli.js',
      desktopRoot: 'C:\\repo\\desktop',
      executable: 'C:\\Program Files\\nodejs\\node.exe',
    }),
    {
      executable: 'C:\\Program Files\\nodejs\\node.exe',
      args: [
        'C:\\repo\\desktop\\node_modules\\@playwright\\test\\cli.js',
        'test',
        'e2e/packaged-shell.spec.ts',
        '--grep',
        scenario,
      ],
      cwd: 'C:\\repo\\desktop',
      shell: false,
    },
  )
})

test('packaged test runner executes the exact invocation without a shell', () => {
  const calls = []
  const status = runPackagedTests(['--grep', 'multi word filter'], {
    cliPath: '/repo/desktop/node_modules/@playwright/test/cli.js',
    desktopRoot: '/repo/desktop',
    executable: '/usr/bin/node',
    spawnSyncImpl(executable, args, options) {
      calls.push({ executable, args, options })
      return { error: undefined, signal: null, status: 0 }
    },
  })

  assert.equal(status, 0)
  assert.deepEqual(calls, [{
    executable: '/usr/bin/node',
    args: [
      '/repo/desktop/node_modules/@playwright/test/cli.js',
      'test',
      'e2e/packaged-shell.spec.ts',
      '--grep',
      'multi word filter',
    ],
    options: {
      cwd: '/repo/desktop',
      env: process.env,
      shell: false,
      stdio: 'inherit',
    },
  }])
})
