/** Launches one bounded WebdriverIO Electron suite with exact argv and no shell. */

import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import {
  chmodSync,
  copyFileSync,
  cpSync,
  lstatSync,
  mkdtempSync,
  renameSync,
  rmSync,
} from 'node:fs'
import { createRequire } from 'node:module'
import {
  basename,
  dirname,
  join,
  relative,
  resolve,
} from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'

const require = createRequire(import.meta.url)
const defaultDesktopRoot = fileURLToPath(new URL('../', import.meta.url))
const sourceScenarios = Object.freeze([
  'built shell exposes the bounded production Home, Chat, Tasks, Diagnostics, and Settings surfaces',
  'task center streams one safe cancellation lifecycle and reloads the terminal backend snapshot',
  'built degraded shell offers one bounded recovery and renders the ready result',
  'built shell has deterministic skip-link and command-palette focus',
  'built shell passes automated WCAG checks across routes and explicit themes',
  'minimum desktop window at 200 percent zoom keeps every action horizontally reachable',
])
const packagedScenarios = Object.freeze([
  'exercises first run, persistence, corrupt preferences, security, and resource evidence',
  'withholds and restores the packaged sidecar through Diagnostics retry',
  'exhausts packaged sidecar restarts and exits cleanly',
  'rejects a substituted packaged sidecar before launch',
  'mediates opaque packaged open and save file grants',
  'launches production normally without a debugging transport',
])

function selectedScenario(argv, scenarios, mode) {
  const grepIndex = argv.findIndex((argument) => (
    argument === '--grep' || argument === '--mochaOpts.grep'
  ))
  if (grepIndex === -1) return ''
  const pattern = argv[grepIndex + 1]
  assert.equal(typeof pattern, 'string', 'WebdriverIO grep requires a pattern')
  const matcher = new RegExp(pattern)
  const matches = scenarios.filter((scenario) => matcher.test(scenario))
  assert.equal(
    matches.length,
    1,
    `WebdriverIO grep must match exactly one declared ${mode} scenario; matched ${matches.length}`,
  )
  return matches[0]
}

function packageRootForExecutable(applicationExecutable, platform = process.platform) {
  let current = resolve(applicationExecutable)
  if (platform !== 'darwin') return dirname(current)
  while (dirname(current) !== current) {
    if (basename(current).endsWith('.app')) return current
    current = dirname(current)
  }
  throw new Error(`Packaged macOS executable is not inside an app bundle: ${applicationExecutable}`)
}

function packagedSidecarPath(packageRoot, platform = process.platform, architecture = process.arch) {
  const resources = platform === 'darwin'
    ? join(packageRoot, 'Contents', 'Resources')
    : join(packageRoot, 'resources')
  const suffix = platform === 'win32' ? '.exe' : ''
  return join(
    resources,
    'sidecar',
    `${platform}-${architecture}`,
    'ancestryllm-sidecar',
    `ancestryllm-sidecar${suffix}`,
  )
}

function prepareCopiedLinuxSandbox(packageRoot, execFileSyncImpl) {
  if (process.platform !== 'linux') return
  const sandboxPath = join(packageRoot, 'chrome-sandbox')
  const sandbox = lstatSync(sandboxPath)
  assert.equal(sandbox.isSymbolicLink(), false, 'Copied Chromium sandbox must not be a symlink')
  assert.equal(sandbox.isFile(), true, 'Copied Chromium sandbox must be a regular file')
  execFileSyncImpl('sudo', [
    '--non-interactive', 'chown', 'root:root', '--', sandboxPath,
  ], { stdio: 'inherit' })
  execFileSyncImpl('sudo', [
    '--non-interactive', 'chmod', '4755', '--', sandboxPath,
  ], { stdio: 'inherit' })
  const prepared = lstatSync(sandboxPath)
  assert.equal(prepared.uid, 0, 'Copied Chromium sandbox must be owned by root')
  assert.equal(prepared.gid, 0, 'Copied Chromium sandbox must use the root group')
  assert.equal(prepared.mode & 0o7777, 0o4755, 'Copied Chromium sandbox must be mode 4755')
}

/**
 * Prepares the immutable original package or a disposable fault-injection copy.
 * @param {string} scenario - Selected packaged test name.
 * @param {NodeJS.ProcessEnv} environment - Explicit child environment.
 * @param {{execFileSyncImpl?: Function, mkdtempSyncImpl?: Function, rmSyncImpl?: Function}} [options] - Injectable native operations.
 * @returns {{environment: NodeJS.ProcessEnv, cleanupPath?: string}} Prepared package contract.
 */
export function preparePackagedScenario(scenario, environment, {
  execFileSyncImpl = execFileSync,
  mkdtempSyncImpl = mkdtempSync,
  rmSyncImpl = rmSync,
} = {}) {
  const originalExecutable = environment.ANCESTRYLLM_PACKAGED_APP
    ?? process.env.ANCESTRYLLM_PACKAGED_APP
  assert.ok(originalExecutable, 'ANCESTRYLLM_PACKAGED_APP is required for packaged tests')
  assert.equal(lstatSync(originalExecutable).isFile(), true, 'Packaged application must be a file')
  const sourcePackageRoot = packageRootForExecutable(originalExecutable)
  const mutatesPackage = scenario === packagedScenarios[1]
    || scenario === packagedScenarios[2]
    || scenario === packagedScenarios[3]
  if (!mutatesPackage) {
    return {
      environment: {
        ...environment,
        ANCESTRYLLM_PACKAGED_EXECUTABLE: originalExecutable,
        ANCESTRYLLM_WDIO_SIDECAR_PATH: packagedSidecarPath(sourcePackageRoot),
      },
    }
  }

  const root = mkdtempSyncImpl(join(tmpdir(), 'ancestryllm-wdio-package-'))
  const copiedPackageRoot = join(root, basename(sourcePackageRoot))
  try {
    if (process.platform === 'darwin') {
      execFileSyncImpl('ditto', ['--noqtn', sourcePackageRoot, copiedPackageRoot], {
        stdio: 'inherit',
      })
    } else {
      cpSync(sourcePackageRoot, copiedPackageRoot, {
        preserveTimestamps: true,
        recursive: true,
      })
    }
    prepareCopiedLinuxSandbox(copiedPackageRoot, execFileSyncImpl)
    const copiedExecutable = join(
      copiedPackageRoot,
      relative(sourcePackageRoot, resolve(originalExecutable)),
    )
    const copiedSidecar = packagedSidecarPath(copiedPackageRoot)
    const preparedEnvironment = {
      ...environment,
      ANCESTRYLLM_PACKAGED_EXECUTABLE: copiedExecutable,
      ANCESTRYLLM_WDIO_SIDECAR_PATH: copiedSidecar,
    }

    if (scenario === packagedScenarios[1]) {
      const withheldSidecar = join(root, basename(copiedSidecar))
      renameSync(copiedSidecar, withheldSidecar)
      preparedEnvironment.ANCESTRYLLM_WDIO_WITHHELD_SIDECAR = withheldSidecar
    } else if (scenario === packagedScenarios[3]) {
      const substituted = environment.ANCESTRYLLM_SUBSTITUTED_SIDECAR
        ?? process.env.ANCESTRYLLM_SUBSTITUTED_SIDECAR
      assert.ok(substituted, 'ANCESTRYLLM_SUBSTITUTED_SIDECAR is required')
      copyFileSync(substituted, copiedSidecar)
      if (process.platform !== 'win32') chmodSync(copiedSidecar, 0o755)
    }

    if (process.platform === 'darwin') {
      execFileSyncImpl('codesign', [
        '--force', '--deep', '--sign', '-', copiedPackageRoot,
      ], { stdio: 'inherit' })
    }
    return { environment: preparedEnvironment, cleanupPath: root }
  } catch (error) {
    try {
      rmSyncImpl(root, {
        force: true,
        maxRetries: 10,
        recursive: true,
        retryDelay: 100,
      })
    } catch {
      // Best-effort cleanup must not replace the actionable preparation failure.
    }
    throw error
  }
}

/**
 * Removes a package-manager separator and converts the retained public grep flag
 * to WebdriverIO's Mocha option.
 * @param {string[]} argv - Additional runner arguments.
 * @returns {string[]} WebdriverIO-compatible arguments.
 */
export function normalizedWdioArguments(argv) {
  assert.equal(Array.isArray(argv), true, 'WebdriverIO arguments must be an array')
  assert.equal(argv.every((item) => typeof item === 'string'), true, 'WebdriverIO arguments must be strings')
  const normalized = []
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    if (index === 0 && argument === '--') continue
    if (argument === '--grep') {
      const pattern = argv[index + 1]
      assert.equal(typeof pattern, 'string', '--grep requires a pattern')
      normalized.push('--mochaOpts.grep', pattern)
      index += 1
      continue
    }
    normalized.push(argument)
  }
  return normalized
}

/**
 * Constructs a no-shell WebdriverIO invocation for one declared Electron suite.
 * @param {'source' | 'packaged'} mode - Bounded suite identifier.
 * @param {string[]} argv - Additional WebdriverIO arguments.
 * @param {{cliPath?: string, desktopRoot?: string, environment?: NodeJS.ProcessEnv, executable?: string}} [options] - Injectable paths and environment for tests.
 * @returns {Readonly<{executable: string, args: readonly string[], cwd: string, env: NodeJS.ProcessEnv, shell: false}>} Invocation contract.
 */
export function wdioInvocation(mode, argv, {
  cliPath = resolve(dirname(require.resolve('@wdio/cli')), '..', 'bin', 'wdio.js'),
  desktopRoot = defaultDesktopRoot,
  environment = {},
  executable = process.execPath,
} = {}) {
  assert.match(mode, /^(?:source|packaged)$/u, 'unknown WebdriverIO suite')
  return Object.freeze({
    executable,
    args: Object.freeze([
      cliPath,
      'run',
      'wdio.conf.ts',
      '--suite',
      mode,
      ...normalizedWdioArguments(argv),
    ]),
    cwd: desktopRoot,
    env: Object.freeze({ ...process.env, ...environment, ANCESTRYLLM_WDIO_MODE: mode }),
    shell: false,
  })
}

/**
 * Runs one WebdriverIO suite and preserves its actual exit status.
 * @param {'source' | 'packaged'} mode - Bounded suite identifier.
 * @param {string[]} argv - Additional WebdriverIO arguments.
 * @param {{spawnSyncImpl?: Function, cliPath?: string, desktopRoot?: string, environment?: NodeJS.ProcessEnv, executable?: string, mkdtempSyncImpl?: Function, preparePackagedScenarioImpl?: Function, rmSyncImpl?: Function, userDataDirectory?: string}} [options] - Injectable runner, paths, and isolation functions.
 * @returns {number} Integer child-process exit code; spawn and signal failures throw.
 */
export function runWdio(mode, argv, {
  environment = {},
  mkdtempSyncImpl = mkdtempSync,
  preparePackagedScenarioImpl = preparePackagedScenario,
  rmSyncImpl = rmSync,
  spawnSyncImpl = spawnSync,
  userDataDirectory,
  ...invocationOptions
} = {}) {
  const scenarios = mode === 'source' ? sourceScenarios : packagedScenarios
  const scenario = selectedScenario(argv, scenarios, mode)
  const createdUserDataDirectory = userDataDirectory === undefined
  const isolatedUserDataDirectory = userDataDirectory
    ?? mkdtempSyncImpl(join(tmpdir(), 'ancestryllm-wdio-'))
  const fixture = scenario === sourceScenarios[2] ? 'degraded' : 'success'
  let preparedPackage = { environment }
  try {
    preparedPackage = mode === 'packaged'
      ? preparePackagedScenarioImpl(scenario, environment)
      : { environment }
    const invocation = wdioInvocation(mode, argv, {
      ...invocationOptions,
      environment: {
        ANCESTRYLLM_DESKTOP_FIXTURE: fixture,
        ANCESTRYLLM_WDIO_USER_DATA: isolatedUserDataDirectory,
        ...preparedPackage.environment,
      },
    })
    const result = spawnSyncImpl(invocation.executable, invocation.args, {
      cwd: invocation.cwd,
      env: invocation.env,
      shell: false,
      stdio: 'inherit',
    })
    if (result.error) throw result.error
    assert.equal(result.signal, null, `WebdriverIO ${mode} suite terminated by signal ${result.signal}`)
    assert.equal(Number.isInteger(result.status), true, `WebdriverIO ${mode} suite did not report an exit status`)
    return result.status
  } finally {
    try {
      if (preparedPackage.cleanupPath) {
        rmSyncImpl(preparedPackage.cleanupPath, {
          force: true,
          recursive: true,
          maxRetries: 10,
          retryDelay: 100,
        })
      }
    } finally {
      if (createdUserDataDirectory) {
        rmSyncImpl(isolatedUserDataDirectory, {
          force: true,
          recursive: true,
          maxRetries: 10,
          retryDelay: 100,
        })
      }
    }
  }
}

/**
 * Runs each source-built scenario in a fresh Electron profile, or one explicitly filtered scenario.
 * @param {'source' | 'packaged'} mode - Bounded suite identifier.
 * @param {string[]} argv - Additional WebdriverIO arguments.
 * @param {Parameters<typeof runWdio>[2]} [options] - Injectable runner controls.
 * @returns {number} First nonzero exit code, or zero when every invocation passes.
 */
export function runWdioPlan(mode, argv, options = {}) {
  const scenarios = mode === 'source' ? sourceScenarios : packagedScenarios
  const hasScenarioFilter = argv.some((argument) => (
    argument === '--grep' || argument === '--mochaOpts.grep'
  ))
  const invocations = hasScenarioFilter
    ? [argv]
    : scenarios.map((scenario) => ['--grep', scenario, ...argv])
  for (const invocationArguments of invocations) {
    const status = runWdio(mode, invocationArguments, options)
    if (status !== 0) return status
  }
  return 0
}

const entrypoint = process.argv[1]
  ? pathToFileURL(resolve(process.argv[1])).href
  : null
if (import.meta.url === entrypoint) {
  const [mode, ...argv] = process.argv.slice(2)
  process.exitCode = runWdioPlan(mode, argv)
}
