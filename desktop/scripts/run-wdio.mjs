/** Launches one bounded WebdriverIO Electron suite with exact argv and no shell. */

import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import {
  chmodSync,
  closeSync,
  constants,
  copyFileSync,
  cpSync,
  fstatSync,
  lstatSync,
  mkdtempSync,
  openSync,
  readSync,
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
const suiteTitles = Object.freeze({
  packaged: 'unpublished unpacked native package',
  source: 'source-built desktop shell',
})
const sourceScenarios = Object.freeze([
  'built shell exposes the bounded production Home, Chat, Tasks, GEDCOM, RootsMagic, Diagnostics, and Settings surfaces',
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
  'launches the selected packaged runtime normally without a debugging transport',
  'queries and exports an immutable RootsMagic source through the native workbench',
])
const startupDiagnosticCodes = new Set([
  'SIDECAR_VERIFICATION_STARTED',
  'SIDECAR_VERIFICATION_SUCCEEDED',
  'SIDECAR_VERIFICATION_REJECTED',
  'SIDECAR_SPAWN_REQUESTED',
  'SIDECAR_SPAWN_SUCCEEDED',
  'SIDECAR_SPAWN_FAILED',
  'SIDECAR_READINESS_ACCEPTED',
  'SIDECAR_READINESS_REJECTED',
  'SIDECAR_HEALTH_SUCCEEDED',
  'SIDECAR_HEALTH_REJECTED',
  'SIDECAR_STARTUP_TIMEOUT',
  'SIDECAR_MANUAL_RETRY_REQUESTED',
  'SIDECAR_MANUAL_RETRY_SUCCEEDED',
  'SIDECAR_MANUAL_RETRY_FAILED',
  'PYTHON_CORE_BOOTSTRAP_STARTED',
  'PYTHON_CORE_READY',
  'SIDECAR_BOOTSTRAP_STARTED',
  'SIDECAR_SERVER_READY',
])

/** Reads only a bounded regular diagnostic file, without following a file symlink. */
function readStartupDiagnosticFile(path) {
  const maximumBytes = 512 * 1024
  let descriptor
  try {
    const entry = lstatSync(path)
    if (!entry.isFile() || entry.isSymbolicLink() || entry.size > maximumBytes) return ''
    descriptor = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
    const opened = fstatSync(descriptor)
    if (!opened.isFile() || opened.size > maximumBytes) return ''
    const buffer = Buffer.alloc(maximumBytes + 1)
    const bytes = readSync(descriptor, buffer, 0, buffer.length, 0)
    return bytes > maximumBytes ? '' : buffer.subarray(0, bytes).toString('utf8')
  } catch {
    return ''
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

/** Emits only known startup stages and canonical timestamps before failed-run profile cleanup. */
function captureStartupDiagnostics(profile) {
  try {
    const directory = join(profile, 'diagnostics')
    const entry = lstatSync(directory)
    if (!entry.isDirectory() || entry.isSymbolicLink()) return
    const events = []
    for (const component of ['electron-main', 'python-core', 'desktop-sidecar']) {
      for (const suffix of ['.2', '.1', '']) {
        const fileEvents = []
        const content = readStartupDiagnosticFile(join(directory, `${component}.jsonl${suffix}`))
        for (const line of content.split('\n')) {
          if (Buffer.byteLength(line, 'utf8') > 4096) continue
          try {
            const event = JSON.parse(line)
            if (event?.schema_version !== 'ancestryllm.desktop-diagnostic/1'
              || event.component !== component
              || !startupDiagnosticCodes.has(event.code)
              || typeof event.timestamp !== 'string'
              || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u.test(event.timestamp)
              || new Date(event.timestamp).toISOString() !== event.timestamp) continue
            fileEvents.push({ timestamp: event.timestamp, component, code: event.code })
            if (fileEvents.length > 100) fileEvents.shift()
          } catch {
            // Malformed or partial diagnostic lines must not hide the original test failure.
          }
        }
        events.push(...fileEvents)
      }
    }
    events.sort((left, right) => left.timestamp.localeCompare(right.timestamp))
    console.info('[packaged-startup-diagnostics]', JSON.stringify({ events: events.slice(-100) }))
  } catch {
    // Diagnostic collection must preserve the runner's failure and profile cleanup.
  }
}

function selectedScenario(argv, scenarios, mode) {
  const grepIndex = argv.findIndex((argument) => (
    argument === '--grep' || argument === '--mochaOpts.grep'
  ))
  if (grepIndex === -1) return ''
  const pattern = argv[grepIndex + 1]
  assert.equal(typeof pattern, 'string', 'WebdriverIO grep requires a pattern')
  const matcher = new RegExp(pattern)
  const suiteTitle = suiteTitles[mode]
  assert.equal(typeof suiteTitle, 'string', 'unknown WebdriverIO suite')
  const matches = scenarios.filter((scenario) => matcher.test(`${suiteTitle} ${scenario}`))
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

function parsedWdioArguments(argv) {
  assert.equal(Array.isArray(argv), true, 'WebdriverIO arguments must be an array')
  assert.equal(argv.every((item) => typeof item === 'string'), true, 'WebdriverIO arguments must be strings')
  const normalized = []
  let headless = false
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    if (argument === '--') continue
    if (argument === '--headless') {
      headless = true
      continue
    }
    assert.equal(argument.startsWith('--headless='), false, 'Use --headless without a value')
    if (argument === '--grep' || argument === '--mochaOpts.grep') {
      const pattern = argv[index + 1]
      assert.equal(typeof pattern, 'string', '--grep requires a pattern')
      normalized.push('--mochaOpts.grep', pattern)
      index += 1
      continue
    }
    normalized.push(argument)
  }
  return { args: normalized, headless }
}

function validatePresentationMode(mode, headless) {
  assert.match(mode, /^(?:source|packaged)$/u, 'unknown WebdriverIO suite')
  assert.ok(!headless || mode === 'source', 'Hidden mode is only supported for source tests, not packaged/native evidence')
}

/**
 * Consumes package-manager separators and the hidden-mode switch, and normalizes grep.
 * @param {string[]} argv - Additional runner arguments.
 * @returns {string[]} WebdriverIO-compatible arguments.
 */
export function normalizedWdioArguments(argv) {
  return parsedWdioArguments(argv).args
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
  const { args, headless } = parsedWdioArguments(argv)
  validatePresentationMode(mode, headless)
  return Object.freeze({
    executable,
    args: Object.freeze([
      cliPath,
      'run',
      'wdio.conf.ts',
      '--suite',
      mode,
      ...args,
    ]),
    cwd: desktopRoot,
    env: Object.freeze({
      ...process.env,
      ...environment,
      ANCESTRYLLM_WDIO_MODE: mode,
      ANCESTRYLLM_E2E_HEADLESS: headless ? '1' : '0',
    }),
    shell: false,
  })
}

/**
 * Constructs the no-shell direct packaged-runtime launch verifier invocation.
 * @param {{desktopRoot?: string, environment?: NodeJS.ProcessEnv, executable?: string, scriptPath?: string}} [options] - Injectable paths and environment for tests.
 * @returns {Readonly<{executable: string, args: readonly string[], cwd: string, env: NodeJS.ProcessEnv, shell: false}>} Invocation contract.
 */
export function normalLaunchInvocation({
  desktopRoot = defaultDesktopRoot,
  environment = {},
  executable = process.execPath,
  scriptPath = join(desktopRoot, 'scripts', 'verify-normal-launch.mjs'),
} = {}) {
  return Object.freeze({
    executable,
    args: Object.freeze([scriptPath]),
    cwd: desktopRoot,
    env: Object.freeze({
      ...process.env,
      ...environment,
      ANCESTRYLLM_WDIO_MODE: 'packaged',
      ANCESTRYLLM_E2E_HEADLESS: '0',
    }),
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
  const { args, headless } = parsedWdioArguments(argv)
  validatePresentationMode(mode, headless)
  const scenarios = mode === 'source' ? sourceScenarios : packagedScenarios
  const scenario = selectedScenario(args, scenarios, mode)
  if (headless) {
    assert.ok(scenario, 'Hidden mode requires one explicitly selected source scenario')
    assert.notEqual(scenario, sourceScenarios[3], 'Native keyboard focus requires a visible Electron window')
  }
  const createdUserDataDirectory = userDataDirectory === undefined
  const isolatedUserDataDirectory = userDataDirectory
    ?? mkdtempSyncImpl(join(tmpdir(), 'ancestryllm-wdio-'))
  const fixture = scenario === sourceScenarios[2] ? 'degraded' : 'success'
  let preparedPackage = { environment }
  let failed = true
  try {
    preparedPackage = mode === 'packaged'
      ? preparePackagedScenarioImpl(scenario, environment)
      : { environment }
    const invocationEnvironment = {
      ANCESTRYLLM_DESKTOP_FIXTURE: fixture,
      ANCESTRYLLM_WDIO_USER_DATA: isolatedUserDataDirectory,
      ...preparedPackage.environment,
    }
    const directNormalLaunch = mode === 'packaged' && scenario === packagedScenarios[5]
    const invocation = directNormalLaunch
      ? normalLaunchInvocation({ ...invocationOptions, environment: invocationEnvironment })
      : wdioInvocation(mode, argv, {
          ...invocationOptions,
          environment: invocationEnvironment,
        })
    const result = spawnSyncImpl(invocation.executable, invocation.args, {
      cwd: invocation.cwd,
      env: invocation.env,
      shell: false,
      stdio: 'inherit',
    })
    if (result.error) throw result.error
    const runner = directNormalLaunch ? 'Packaged normal-launch verifier' : `WebdriverIO ${mode} suite`
    assert.equal(result.signal, null, `${runner} terminated by signal ${result.signal}`)
    assert.equal(Number.isInteger(result.status), true, `${runner} did not report an exit status`)
    failed = result.status !== 0
    return result.status
  } finally {
    if (mode === 'packaged' && failed) captureStartupDiagnostics(isolatedUserDataDirectory)
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
  const { args, headless } = parsedWdioArguments(argv)
  validatePresentationMode(mode, headless)
  const scenarios = mode === 'source'
    ? sourceScenarios.filter((scenario) => !headless || scenario !== sourceScenarios[3])
    : packagedScenarios
  const hasScenarioFilter = args.includes('--mochaOpts.grep')
  const presentationArguments = headless ? ['--headless'] : []
  if (headless) {
    console.info('Hidden source checks only: native keyboard focus and packaged/native verification are excluded.')
  }
  const invocations = hasScenarioFilter
    ? [[...presentationArguments, ...args]]
    : scenarios.map((scenario) => [...presentationArguments, '--grep', scenario, ...args])
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
