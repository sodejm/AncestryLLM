/** Verifies packaged Electron security, resilience, grants, and release evidence with WebdriverIO. */
import assert from 'node:assert/strict'
import { execFile, spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, isAbsolute, join } from 'node:path'
import { promisify } from 'node:util'
import { $, $$, browser } from '@wdio/globals'
import '@wdio/native-types'
import { PRODUCTION_CSP } from '../src/main/security-policy'
import { LINUX_KEYRING_VERIFICATION_SWITCH } from '../src/main/sidecar-supervisor'
import type { AncestryBridge, StartupDiagnostics } from '../src/shared-contract/desktop'
import { outputContainsWindowReadyRecord } from '../src/main/window-readiness'
import { bridgeMethods } from './bridge-contract'
import { normalizeVerificationSelection } from './native-file-dialogs.packaged-verification'

const packagedExecutable = process.env.ANCESTRYLLM_PACKAGED_APP
const automatedPackagedExecutable = process.env.ANCESTRYLLM_PACKAGED_EXECUTABLE
const metricsPath = process.env.ANCESTRYLLM_PACKAGED_METRICS
const withholdEvidencePath = process.env.ANCESTRYLLM_WITHHOLD_EVIDENCE
const restartEvidencePath = process.env.ANCESTRYLLM_RESTART_EVIDENCE
const integrityEvidencePath = process.env.ANCESTRYLLM_INTEGRITY_EVIDENCE
const integrityDiagnosticsPath = process.env.ANCESTRYLLM_INTEGRITY_DIAGNOSTICS
const fileGrantVerificationMarker = process.env.ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION
const fileGrantOpenPath = process.env.ANCESTRYLLM_FILE_GRANT_OPEN_PATH
const fileGrantSavePath = process.env.ANCESTRYLLM_FILE_GRANT_SAVE_PATH
const fileGrantEvidencePath = process.env.ANCESTRYLLM_FILE_GRANT_EVIDENCE
const userDataDirectory = process.env.ANCESTRYLLM_WDIO_USER_DATA
const copiedSidecarPath = process.env.ANCESTRYLLM_WDIO_SIDECAR_PATH
const withheldSidecarPath = process.env.ANCESTRYLLM_WDIO_WITHHELD_SIDECAR
const execFileAsync = promisify(execFile)

type ProcessRecord = Readonly<{
  pid: number
  ppid: number
  rssBytes: number
  commandLine: string
}>

type StartupExpectation = Readonly<{
  state: StartupDiagnostics['state']
  failure: StartupDiagnostics['failure']
  automaticRestartsRemaining: number
  manualRetriesRemaining: number
  report?: Readonly<{
    schema_version: 1
    status: NonNullable<StartupDiagnostics['report']>['status']
  }>
}>

const READY_DIAGNOSTICS: StartupExpectation = Object.freeze({
  state: 'ready',
  failure: null,
  automaticRestartsRemaining: 2,
  manualRetriesRemaining: 1,
  report: Object.freeze({ schema_version: 1, status: 'ready' }),
})
const CAPABILITY_SUMMARY_READY = /^(?:No control capabilities are currently available\.|\d+ local control (?:module is|modules are) available\.)$/u

const visible = async (selector: string) => {
  const element = await $(selector)
  await element.waitForDisplayed()
  return element
}

const click = async (selector: string) => {
  const element = await visible(selector)
  await element.click()
}

const text = async (selector: string) => (await visible(selector)).getText()

const labeledInput = async (label: string) => visible(
  `//label[normalize-space(.)="${label}"]//input`,
)

async function delay(milliseconds: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function eventually<T>(
  description: string,
  read: () => Promise<T>,
  accepts: (value: T) => boolean,
  timeoutMs = 30_000,
): Promise<T> {
  const deadline = Date.now() + timeoutMs
  let last: T
  do {
    last = await read()
    if (accepts(last)) return last
    await delay(200)
  } while (Date.now() < deadline)
  throw new Error(`${description}: ${JSON.stringify(last)}`)
}

async function expectFocusedHeading(name: string): Promise<void> {
  await browser.waitUntil(async () => browser.execute((expected) => {
    const active = document.activeElement
    return active?.matches('h1, h2, h3') === true && active.textContent?.trim() === expected
  }, name), { timeoutMsg: `Expected ${name} heading to receive focus` })
}

async function startupDiagnostics(): Promise<StartupDiagnostics> {
  return browser.execute(async () => {
    const result = await (window as unknown as { ancestry: AncestryBridge })
      .ancestry.getStartupDiagnostics()
    if (!result.ok) throw new Error(`Could not read startup diagnostics: ${result.error.code}`)
    return result.data
  })
}

function matchesStartup(actual: StartupDiagnostics, expected: StartupExpectation): boolean {
  return actual.state === expected.state
    && actual.failure === expected.failure
    && actual.automaticRestartsRemaining === expected.automaticRestartsRemaining
    && actual.manualRetriesRemaining === expected.manualRetriesRemaining
    && (expected.report === undefined || (
      actual.report?.schema_version === expected.report.schema_version
      && actual.report.status === expected.report.status
    ))
}

async function expectStartupDiagnostics(expected: StartupExpectation): Promise<void> {
  let lastActual: StartupDiagnostics | null = null
  try {
    await browser.waitUntil(async () => {
      const actual = await startupDiagnostics().catch(() => null)
      if (actual === null) return false
      lastActual = actual
      return matchesStartup(actual, expected)
    }, { timeout: 30_000, interval: 250 })
  } catch (error) {
    throw new Error(
      `Packaged startup diagnostics did not match: ${JSON.stringify(lastActual)}`,
      { cause: error },
    )
  }
}

async function expectSafeDiagnosticsAlert(expectedMessage: RegExp): Promise<void> {
  await click('a=Diagnostics')
  await expectFocusedHeading('Diagnostics')
  const alert = await visible('[role="alert"]')
  const alertText = await alert.getText()
  assert.match(alertText, expectedMessage)
  assert.doesNotMatch(alertText, /token|secret|stderr|\.json|[/\\](?:Users|home|AppData)[/\\]/iu)
}

async function mainPid(): Promise<number> {
  if (!automatedPackagedExecutable || !userDataDirectory) {
    throw new Error('Automated packaged executable and isolated user data are required')
  }
  const profileArgument = `--user-data-dir=${userDataDirectory}`
  return eventually(
    'Packaged Electron main-process PID was not observed',
    async () => (await processSnapshot()).find((record) => (
      !record.commandLine.includes('--type=')
      && record.commandLine.includes(automatedPackagedExecutable)
      && record.commandLine.includes(profileArgument)
    ))?.pid ?? -1,
    (pid) => pid > 0,
  )
}

async function processSnapshot(): Promise<ProcessRecord[]> {
  if (process.platform === 'win32') {
    const script = [
      '$ErrorActionPreference = "Stop"',
      'Get-CimInstance Win32_Process | ForEach-Object {',
      '  [PSCustomObject]@{',
      '    pid = [int]$_.ProcessId;',
      '    ppid = [int]$_.ParentProcessId;',
      '    rssBytes = [long]$_.WorkingSetSize;',
      '    commandLine = [string]$_.CommandLine',
      '  }',
      '} | ConvertTo-Json -Compress',
    ].join('\n')
    const { stdout } = await execFileAsync('powershell.exe', [
      '-NoLogo',
      '-NoProfile',
      '-NonInteractive',
      '-Command',
      script,
    ], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024, windowsHide: true })
    const decoded = JSON.parse(stdout.trim()) as ProcessRecord | ProcessRecord[]
    return (Array.isArray(decoded) ? decoded : [decoded]).map((record) => ({
      pid: Number(record.pid),
      ppid: Number(record.ppid),
      rssBytes: Number(record.rssBytes),
      commandLine: String(record.commandLine ?? ''),
    }))
  }

  const { stdout } = await execFileAsync('ps', ['-ww', '-axo', 'pid=,ppid=,rss=,command='], {
    encoding: 'utf8',
    maxBuffer: 8 * 1024 * 1024,
  })
  return stdout.split('\n').flatMap((line): ProcessRecord[] => {
    const match = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/u)
    if (!match?.[1] || !match[2] || !match[3] || match[4] === undefined) return []
    return [{
      pid: Number.parseInt(match[1], 10),
      ppid: Number.parseInt(match[2], 10),
      rssBytes: Number.parseInt(match[3], 10) * 1024,
      commandLine: match[4],
    }]
  })
}

function descendantProcessTree(records: readonly ProcessRecord[], rootPid: number): ProcessRecord[] {
  const included = new Set([rootPid])
  let changed = true
  while (changed) {
    changed = false
    for (const record of records) {
      if (included.has(record.pid) || !included.has(record.ppid)) continue
      included.add(record.pid)
      changed = true
    }
  }
  return records.filter((record) => included.has(record.pid))
}

async function sidecarPid(
  applicationPid: number,
  sidecarPath: string,
  excluded: ReadonlySet<number> = new Set(),
): Promise<number> {
  return eventually(
    'Packaged sidecar PID was not observed',
    async () => descendantProcessTree(await processSnapshot(), applicationPid)
      .find((record) => !excluded.has(record.pid) && record.commandLine.includes(sidecarPath))
      ?.pid ?? -1,
    (pid) => pid > 0,
  )
}

async function killProcess(pid: number): Promise<void> {
  if (process.platform === 'win32') {
    await execFileAsync('taskkill.exe', ['/PID', String(pid), '/T', '/F'], {
      encoding: 'utf8',
      windowsHide: true,
    })
    return
  }
  process.kill(pid, 'SIGKILL')
}

async function expectProcessAbsent(pid: number, timeoutMs = 45_000): Promise<void> {
  await eventually(
    `Process ${pid} did not exit`,
    async () => (await processSnapshot()).some((record) => record.pid === pid),
    (present) => !present,
    timeoutMs,
  )
}

async function closeApplicationWindow(): Promise<number> {
  const pid = await mainPid()
  await browser.closeWindow()
  await expectProcessAbsent(pid)
  return pid
}

async function terminateVerificationApplication(): Promise<number> {
  const pid = await mainPid()
  await killProcess(pid)
  await expectProcessAbsent(pid)
  return pid
}

async function writeFaultEvidence(
  path: string,
  scenario: 'sidecar-withhold-retry' | 'sidecar-restart-exhaustion-quit' | 'sidecar-integrity-substitution',
  observations: Record<string, boolean | number | string>,
): Promise<void> {
  await writeFile(path, `${JSON.stringify({
    schemaVersion: 1,
    kind: 'ancestryllm-packaged-fault-evidence',
    scenario,
    status: 'passed',
    packageCopy: true,
    productionFaultHookUsed: false,
    observations,
  }, null, 2)}\n`, { flag: 'wx', mode: 0o600 })
}

async function writeFileGrantEvidence(path: string): Promise<void> {
  await writeFile(path, `${JSON.stringify({
    schemaVersion: 1,
    kind: 'ancestryllm-packaged-file-grant-evidence',
    status: 'passed',
    verificationOnlyDialogAdapter: true,
    observations: {
      openGrantOpaque: true,
      openMetadataValidated: true,
      saveGrantOpaque: true,
      replacementConfirmed: true,
      revocationPassed: true,
      selectedPathsAbsent: true,
    },
  }, null, 2)}\n`, { flag: 'wx', mode: 0o600 })
}

async function writeIntegrityDiagnostics(
  path: string,
  details: Readonly<{
    phase: string
    elapsedMs: number
    status: 'passed' | 'failed'
    processPid: number | null
    processExited: boolean
    errorName: string | null
  }>,
): Promise<void> {
  await writeFile(path, `${JSON.stringify({
    schemaVersion: 1,
    kind: 'ancestryllm-packaged-fault-diagnostics',
    scenario: 'sidecar-integrity-substitution',
    ...details,
  }, null, 2)}\n`, { mode: 0o600 })
}

function stringsIn(value: unknown): string[] {
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(stringsIn)
  if (value !== null && typeof value === 'object') return Object.values(value).flatMap(stringsIn)
  return []
}

async function expectProductionBoundary(rootPid: number): Promise<number> {
  const appUrl = new URL(await browser.getUrl())
  assert.deepEqual({
    protocol: appUrl.protocol,
    hostname: appUrl.hostname,
    pathname: appUrl.pathname,
    search: appUrl.search,
  }, {
    protocol: 'app:',
    hostname: 'bundle',
    pathname: '/index.html',
    search: '',
  })
  assert.equal(await browser.execute(() => typeof (globalThis as { process?: unknown }).process), 'undefined')
  assert.deepEqual(await browser.execute(() => {
    const ancestry = (window as unknown as { ancestry: object }).ancestry
    return { frozen: Object.isFrozen(ancestry), methods: Object.keys(ancestry).sort() }
  }), { frozen: true, methods: bridgeMethods })

  await browser.waitUntil(async () => browser.execute(({ source, flags }) => {
    const pattern = new RegExp(source, flags)
    return Array.from(document.querySelectorAll<HTMLElement>('main *'))
      .some((element) => element.children.length === 0 && pattern.test(element.textContent?.trim() ?? ''))
  }, { source: CAPABILITY_SUMMARY_READY.source, flags: CAPABILITY_SUMMARY_READY.flags }), {
    timeoutMsg: 'Expected the packaged capability summary to become ready',
  })
  const capabilityBurst = await browser.execute(async () => {
    const ancestry = (window as unknown as { ancestry: AncestryBridge }).ancestry
    const responses = await Promise.all(Array.from({ length: 32 }, () => ancestry.getCapabilities()))
    return {
      successful: responses.filter((result) => result.ok).length,
      overloaded: responses.filter(
        (result) => !result.ok && result.error.code === 'BRIDGE_OVERLOADED',
      ).length,
      count: responses.length,
      unexpectedErrorCodes: [...new Set(responses.flatMap(
        (result) => !result.ok && result.error.code !== 'BRIDGE_OVERLOADED'
          ? [result.error.code]
          : [],
      ))].sort(),
    }
  })
  assert.deepEqual(capabilityBurst, {
    successful: 32,
    overloaded: 0,
    count: 32,
    unexpectedErrorCodes: [],
  })

  const denied = await browser.execute(async () => {
    let fetchBlocked = false
    try { await fetch('https://example.invalid/') } catch { fetchBlocked = true }
    const webSocketBlocked = await new Promise<boolean>((resolve) => {
      const socket = new WebSocket('wss://example.invalid/')
      const timer = setTimeout(() => resolve(false), 1_500)
      socket.addEventListener('error', () => { clearTimeout(timer); resolve(true) }, { once: true })
      socket.addEventListener('open', () => { clearTimeout(timer); socket.close(); resolve(false) }, { once: true })
    })
    let serviceWorkerBlocked = !('serviceWorker' in navigator)
    if (!serviceWorkerBlocked) {
      try {
        const registration = await navigator.serviceWorker.register('/service-worker.js')
        await registration.unregister()
      } catch { serviceWorkerBlocked = true }
    }
    return {
      fetchBlocked,
      webSocketBlocked,
      serviceWorkerBlocked,
      childWindowBlocked: window.open('https://github.com/') === null,
      externalResources: performance.getEntriesByType('resource')
        .map((entry) => entry.name)
        .filter((url) => /^(?:https?|wss?):/iu.test(url)),
      csp: (await fetch(location.href)).headers.get('content-security-policy'),
    }
  })
  assert.deepEqual(denied, {
    fetchBlocked: true,
    webSocketBlocked: true,
    serviceWorkerBlocked: true,
    childWindowBlocked: true,
    externalResources: [],
    csp: PRODUCTION_CSP,
  })

  const tree = await eventually(
    'Packaged renderer process was not observed',
    async () => descendantProcessTree(await processSnapshot(), rootPid),
    (records) => records.some((record) => (
      record.commandLine.includes('--type=renderer')
      && !record.commandLine.includes('--no-sandbox')
    )),
  )
  assert.ok(tree.some((record) => record.pid === rootPid))
  const inspectPattern = new RegExp('(?:^|\\s)--inspect(?:-brk)?(?:=|\\s|$)', 'u')
  assert.doesNotMatch(tree.map((record) => record.commandLine).join('\n'), inspectPattern)
  const rssBytes = tree.reduce((total, record) => total + record.rssBytes, 0)
  assert.ok(rssBytes > 0)
  return rssBytes
}

async function expectAccessibleShell(): Promise<void> {
  assert.equal((await $$('header')).length, 1)
  assert.equal((await $$('nav[aria-label="Primary"]')).length, 1)
  assert.equal((await $$('main')).length, 1)
  assert.deepEqual(await browser.execute(() => Array.from(
    document.querySelectorAll<HTMLElement>('nav[aria-label="Primary"] a'),
    (link) => link.textContent?.trim(),
  )), ['Home', 'Chat', 'Tasks', 'Diagnostics', 'Settings'])

  await click('a=Settings')
  await expectFocusedHeading('Settings')
  assert.equal((await $$('[aria-labelledby="general-settings-title"] input[type="radio"]')).length, 3)
  await (await labeledInput('dark')).click()
  await (await labeledInput('Reduce motion')).click()
  await browser.waitUntil(async () => browser.execute(() => (
    document.documentElement.dataset.theme === 'dark'
    && document.documentElement.dataset.reducedMotion === 'true'
  )))

  const visualChecks = await browser.execute(() => {
    const rgb = (value: string): [number, number, number] => {
      const channels = value.match(/[\d.]+/gu)?.slice(0, 3).map(Number)
      if (!channels || channels.length !== 3) throw new Error(`Unsupported color: ${value}`)
      return channels as [number, number, number]
    }
    const luminance = (value: string): number => rgb(value)
      .map((channel) => channel / 255)
      .map((channel) => channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
      .reduce((total, channel, index) => total + channel * ([0.2126, 0.7152, 0.0722][index] ?? 0), 0)
    const contrast = (selector: string): number => {
      const style = getComputedStyle(document.querySelector(selector) as Element)
      const foreground = luminance(style.color)
      const background = luminance(style.backgroundColor)
      return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)
    }
    const target = document.querySelector('.option-label') as Element
    return {
      bodyContrast: contrast('.app-shell'),
      headerContrast: contrast('header'),
      reducedAnimationMs: Number.parseFloat(getComputedStyle(target).animationDuration) * 1000,
      reducedTransitionMs: Number.parseFloat(getComputedStyle(target).transitionDuration) * 1000,
    }
  })
  assert.ok(visualChecks.bodyContrast >= 4.5)
  assert.ok(visualChecks.headerContrast >= 4.5)
  assert.ok(visualChecks.reducedAnimationMs <= 0.001)
  assert.ok(visualChecks.reducedTransitionMs <= 0.001)

  const originalWindow = await browser.getWindowSize()
  await browser.setWindowSize(720, 560)
  const zoomModifier = process.platform === 'darwin' ? 'Meta' : 'Control'
  for (let level = 0; level < 5; level += 1) await browser.keys([zoomModifier, '+'])
  try {
    const layout = await eventually(
      'Packaged 200 percent layout did not settle',
      () => browser.execute(() => {
        const controls = Array.from(document.querySelectorAll<HTMLElement>(
          'a[href], button, input, select, textarea, [tabindex="0"]',
        ))
        const clippedControls = controls.flatMap((control) => {
          const style = getComputedStyle(control)
          const bounds = control.getBoundingClientRect()
          if (style.display === 'none' || style.visibility === 'hidden' || bounds.width === 0 || bounds.height === 0) return []
          if (bounds.left >= -0.5 && bounds.right <= window.innerWidth + 0.5) return []
          return [control.getAttribute('aria-label') ?? control.textContent?.trim() ?? control.tagName]
        })
        return {
          viewportWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          clippedControls,
        }
      }),
      (state) => state.viewportWidth >= 340 && state.viewportWidth <= 365,
    )
    assert.ok(layout.documentWidth <= layout.viewportWidth)
    assert.deepEqual(layout.clippedControls, [])
    assert.equal(await (await visible('nav[aria-label="Primary"]')).isDisplayed(), true)
  } finally {
    await browser.keys([zoomModifier, '0'])
    await browser.setWindowSize(originalWindow.width, originalWindow.height)
  }
}

function inheritedEnvironment(names: readonly string[]): Record<string, string> {
  return Object.fromEntries(names.flatMap((name): [string, string][] => {
    const value = process.env[name]
    return value === undefined ? [] : [[name, value]]
  }))
}

async function isolatedEnvironment(root: string): Promise<Record<string, string>> {
  const isolatedHome = join(root, 'os-home')
  const isolatedTemp = join(isolatedHome, 'tmp')
  await Promise.all([
    join(isolatedHome, 'AppData', 'Local'),
    join(isolatedHome, 'AppData', 'Roaming'),
    join(isolatedHome, 'Library', 'Application Support'),
    join(isolatedHome, 'Library', 'Caches'),
    join(isolatedHome, 'Library', 'Logs'),
    join(isolatedHome, 'Library', 'Preferences'),
    join(isolatedHome, '.cache'),
    join(isolatedHome, '.config'),
    join(isolatedHome, '.local', 'share'),
    isolatedTemp,
    join(root, 'chromium-cache'),
    join(root, 'crash-dumps'),
  ].map(async (path) => mkdir(path, { recursive: true })))
  const baseNames = ['LANG', 'LC_ALL', 'LC_CTYPE', 'PATH', 'TZ'] as const
  const platformNames = process.platform === 'win32'
    ? ['COMSPEC', 'NUMBER_OF_PROCESSORS', 'PATHEXT', 'PROCESSOR_ARCHITECTURE', 'SYSTEMROOT', 'USERNAME', 'USERDOMAIN', 'WINDIR']
    : process.platform === 'darwin'
      ? ['HOME', 'LOGNAME', 'SECURITYSESSIONID', 'SHELL', 'USER', '__CF_USER_TEXT_ENCODING']
      : ['DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'LOGNAME', 'SHELL', 'USER', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR']
  const environment: Record<string, string> = {
    ...inheritedEnvironment([...baseNames, ...platformNames]),
    USERPROFILE: isolatedHome,
    APPDATA: join(isolatedHome, 'AppData', 'Roaming'),
    LOCALAPPDATA: join(isolatedHome, 'AppData', 'Local'),
    XDG_CACHE_HOME: join(isolatedHome, '.cache'),
    XDG_CONFIG_HOME: join(isolatedHome, '.config'),
    XDG_DATA_HOME: join(isolatedHome, '.local', 'share'),
    TEMP: isolatedTemp,
    TMP: isolatedTemp,
    TMPDIR: isolatedTemp,
    NO_PROXY: '127.0.0.1,localhost',
    no_proxy: '127.0.0.1,localhost',
  }
  if (process.platform !== 'darwin') environment.HOME = isolatedHome
  const packagedRuntimePath = process.env.ANCESTRYLLM_PACKAGED_RUNTIME_PATH
  if (packagedRuntimePath !== undefined) environment.PATH = packagedRuntimePath
  return environment
}

function linuxKeyringVerificationArguments(): string[] {
  const root = process.env.ANCESTRYLLM_NATIVE_KEYRING_ROOT
  if (process.platform !== 'linux') {
    if (root !== undefined) throw new Error('Linux keyring verification root is Linux only')
    return []
  }
  if (!root || !isAbsolute(root)) {
    throw new Error('Packaged Linux verification requires an absolute native keyring root')
  }
  return [`--${LINUX_KEYRING_VERIFICATION_SWITCH}=${root}`]
}

function waitForChildExit(
  child: ChildProcessWithoutNullStreams,
  timeoutMs: number,
): Promise<Readonly<{ code: number | null; signal: NodeJS.Signals | null }>> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode })
  }
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      cleanup()
      reject(new Error(`Process ${String(child.pid)} did not exit within ${timeoutMs} ms`))
    }, timeoutMs)
    const onError = (error: Error): void => { cleanup(); reject(error) }
    const onExit = (code: number | null, signal: NodeJS.Signals | null): void => {
      cleanup()
      resolve({ code, signal })
    }
    const cleanup = (): void => {
      clearTimeout(timeout)
      child.off('error', onError)
      child.off('exit', onExit)
    }
    child.once('error', onError)
    child.once('exit', onExit)
  })
}

async function forceCloseProcess(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return
  if (process.platform === 'win32' && child.pid) {
    await execFileAsync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
      encoding: 'utf8',
      windowsHide: true,
    }).catch(() => undefined)
    await waitForChildExit(child, 10_000).catch(() => undefined)
    return
  }
  child.kill('SIGTERM')
  try {
    await waitForChildExit(child, 5_000)
  } catch {
    child.kill('SIGKILL')
    await waitForChildExit(child, 5_000).catch(() => undefined)
  }
}

async function requestNormalApplicationQuit(
  child: ChildProcessWithoutNullStreams,
): Promise<void> {
  if (!child.pid) throw new Error('Normal packaged launch PID is unavailable')
  if (process.platform === 'win32') {
    const script = [
      '$ErrorActionPreference = "Stop"',
      `$target = Get-Process -Id ${String(child.pid)} -ErrorAction Stop`,
      "if (-not $target.CloseMainWindow()) { throw 'native-window-close-request-failed' }",
    ].join('\n')
    await execFileAsync('powershell.exe', [
      '-NoLogo',
      '-NoProfile',
      '-NonInteractive',
      '-Command',
      script,
    ], { encoding: 'utf8', windowsHide: true })
  } else if (!child.kill('SIGTERM')) {
    throw new Error(`Could not request normal exit for process ${String(child.pid)}`)
  }
  const exitStatus = await waitForChildExit(child, 45_000)
  assert.deepEqual(exitStatus, { code: 0, signal: null })
}

async function expectNormalLaunchWithoutDebugSurface(root: string): Promise<void> {
  if (!packagedExecutable) throw new Error('ANCESTRYLLM_PACKAGED_APP is required')
  const automationArguments = process.platform === 'darwin' ? ['--use-mock-keychain'] : []
  const launchArguments = [
    ...automationArguments,
    ...linuxKeyringVerificationArguments(),
    `--user-data-dir=${root}`,
    `--disk-cache-dir=${join(root, 'chromium-cache')}`,
    `--crash-dumps-dir=${join(root, 'crash-dumps')}`,
  ]
  const child = spawn(packagedExecutable, launchArguments, {
    env: await isolatedEnvironment(root),
    stdio: 'pipe',
    windowsHide: true,
  })
  let output = ''
  const consume = (chunk: Buffer): void => {
    output = `${output}${chunk.toString('utf8')}`.slice(-32_768)
  }
  child.stdout.on('data', consume)
  child.stderr.on('data', consume)
  let exitedCleanly = false
  try {
    const rootPid = child.pid
    if (!rootPid) throw new Error('Normal packaged launch PID is unavailable')
    const deadline = Date.now() + 30_000
    let tree: ProcessRecord[] = []
    let windowReady = false
    const observedCommandLines = new Set<string>()
    while (Date.now() < deadline) {
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error(`Normal packaged launch exited before readiness.\n${output}`)
      }
      tree = descendantProcessTree(await processSnapshot(), rootPid)
      for (const record of tree) observedCommandLines.add(record.commandLine)
      windowReady = outputContainsWindowReadyRecord(output)
      if (
        windowReady
        && tree.some((record) => record.pid === rootPid)
        && tree.reduce((total, record) => total + record.rssBytes, 0) > 0
      ) break
      await delay(250)
    }
    assert.ok(tree.some((record) => record.pid === rootPid))
    assert.ok(tree.reduce((total, record) => total + record.rssBytes, 0) > 0)
    assert.equal(windowReady, true)
    const remoteStem = ['remote', 'debugging'].join('-')
    const disallowedControlArgument = new RegExp(
      `(?:^|\\s)--(?:${remoteStem}(?:-address|-port|-pipe)?|inspect(?:-brk)?)(?:=|\\s|$)`,
      'u',
    )
    assert.doesNotMatch(launchArguments.join('\n'), disallowedControlArgument)
    assert.doesNotMatch([...observedCommandLines].join('\n'), disallowedControlArgument)
    assert.doesNotMatch(output, /DevTools listening on /u)
    await requestNormalApplicationQuit(child)
    exitedCleanly = true
  } finally {
    if (!exitedCleanly) await forceCloseProcess(child)
  }
}

describe('unpublished unpacked native package', () => {
  it('exercises first run, persistence, corrupt preferences, security, and resource evidence', async function () {
    if (!metricsPath) this.skip()
    if (!metricsPath || !userDataDirectory) {
      throw new Error('Packaged metrics and isolated user data are required')
    }
    const launchedAt = Number(process.env.ANCESTRYLLM_WDIO_LAUNCH_STARTED_AT)
    assert.ok(Number.isFinite(launchedAt) && launchedAt > 0)
    await expectFocusedHeading('Welcome to AncestryLLM')
    const coldLaunchMs = Date.now() - launchedAt
    assert.match(await text('main'), /Your desktop control shell stays local to this device\./u)
    await click('button=Continue to Home')
    await expectFocusedHeading('Home')
    assert.match(await text('main'), /Packaged build/u)
    assert.match(await text('main'), /Ready/u)
    await click('a=Diagnostics')
    await expectFocusedHeading('Diagnostics')
    assert.match(await text('main'), /Desktop service[\s\S]*Ready/u)
    assert.equal((await $$('[role="alert"]')).length, 0)
    const readyMs = Date.now() - launchedAt
    const rssBytes = await expectProductionBoundary(await mainPid())
    await expectAccessibleShell()

    const preferencesPath = join(userDataDirectory, 'preferences.json')
    const persisted = JSON.parse(await eventually(
      'Packaged preferences were not persisted',
      () => readFile(preferencesPath, 'utf8').catch(() => ''),
      (contents) => contents.startsWith('{'),
    )) as Record<string, unknown>
    assert.deepEqual({
      colorScheme: persisted.colorScheme,
      reducedMotion: persisted.reducedMotion,
      onboardingCompleted: persisted.onboardingCompleted,
      schemaVersion: persisted.schemaVersion,
    }, {
      colorScheme: 'dark',
      reducedMotion: true,
      onboardingCompleted: true,
      schemaVersion: 1,
    })
    assert.deepEqual(Object.keys(persisted).sort(), [
      'colorScheme',
      'onboardingCompleted',
      'reducedMotion',
      'revision',
      'schemaVersion',
    ])

    const warmStartedAt = Date.now()
    await browser.reloadSession()
    await expectFocusedHeading('Home')
    const warmLaunchMs = Date.now() - warmStartedAt
    assert.equal((await $$('h1=Welcome to AncestryLLM')).length, 0)
    assert.deepEqual(await browser.execute(() => ({
      theme: document.documentElement.dataset.theme,
      reducedMotion: document.documentElement.dataset.reducedMotion,
    })), { theme: 'dark', reducedMotion: 'true' })
    assert.match(await text('main'), /Ready/u)

    const corruptPayload = '{fictional-corrupt-preferences'
    await writeFile(preferencesPath, corruptPayload, 'utf8')
    await browser.reloadSession()
    await expectFocusedHeading('Welcome to AncestryLLM')
    await click('button=Continue to Home')
    const alertText = await text('[role="alert"]')
    assert.match(alertText, /PREFERENCES_UNAVAILABLE/u)
    assert.doesNotMatch(alertText, /token|secret|stderr|\.json|[/\\](?:Users|home|AppData)[/\\]/iu)
    assert.equal((await $$('h1=Home')).length, 0)
    assert.equal(await readFile(preferencesPath, 'utf8'), corruptPayload)

    await writeFile(metricsPath, `${JSON.stringify({
      coldLaunchMs,
      warmLaunchMs,
      readyMs,
      rssBytes,
      rendererOutboundRequests: 0,
    }, null, 2)}\n`, { flag: 'wx', mode: 0o600 })
  })

  it('withholds and restores the packaged sidecar through Diagnostics retry', async function () {
    if (!withholdEvidencePath) this.skip()
    if (!withholdEvidencePath || !withheldSidecarPath || !copiedSidecarPath) {
      throw new Error('Withhold evidence and prepared sidecar paths are required')
    }
    await expectStartupDiagnostics({
      state: 'degraded',
      failure: 'startup_failed',
      automaticRestartsRemaining: 2,
      manualRetriesRemaining: 1,
    })
    await expectSafeDiagnosticsAlert(/The desktop service did not start\./u)
    await rename(withheldSidecarPath, copiedSidecarPath)
    await click('button=Retry desktop service')
    await expectStartupDiagnostics({
      state: 'ready',
      failure: null,
      automaticRestartsRemaining: 2,
      manualRetriesRemaining: 0,
    })
    assert.equal((await $$('[role="alert"]')).length, 0)
    await closeApplicationWindow()
    await writeFaultEvidence(withholdEvidencePath, 'sidecar-withhold-retry', {
      failure: 'startup_failed',
      automaticRestartsRemaining: 2,
      manualRetriesRemainingBefore: 1,
      recoveredState: 'ready',
      processExitedAfterWindowClose: true,
    })
  })

  it('exhausts packaged sidecar restarts and exits cleanly', async function () {
    if (!restartEvidencePath) this.skip()
    if (!restartEvidencePath || !copiedSidecarPath) {
      throw new Error('Restart evidence and prepared sidecar path are required')
    }
    await expectStartupDiagnostics(READY_DIAGNOSTICS)
    const applicationPid = await mainPid()
    const killed = new Set<number>()
    const first = await sidecarPid(applicationPid, copiedSidecarPath, killed)
    killed.add(first)
    await killProcess(first)
    await expectStartupDiagnostics({
      state: 'ready',
      failure: null,
      automaticRestartsRemaining: 1,
      manualRetriesRemaining: 1,
    })
    const second = await sidecarPid(applicationPid, copiedSidecarPath, killed)
    killed.add(second)
    await killProcess(second)
    await expectStartupDiagnostics({
      state: 'ready',
      failure: null,
      automaticRestartsRemaining: 0,
      manualRetriesRemaining: 1,
    })
    const third = await sidecarPid(applicationPid, copiedSidecarPath, killed)
    killed.add(third)
    await killProcess(third)
    await expectStartupDiagnostics({
      state: 'degraded',
      failure: 'crash_loop',
      automaticRestartsRemaining: 0,
      manualRetriesRemaining: 1,
    })
    await expectSafeDiagnosticsAlert(/The desktop service stopped repeatedly\./u)
    await click('button=Retry desktop service')
    await expectStartupDiagnostics({
      state: 'ready',
      failure: null,
      automaticRestartsRemaining: 0,
      manualRetriesRemaining: 0,
    })
    const retried = await sidecarPid(applicationPid, copiedSidecarPath, killed)
    await closeApplicationWindow()
    await expectProcessAbsent(retried)
    await writeFaultEvidence(restartEvidencePath, 'sidecar-restart-exhaustion-quit', {
      automaticRestartCount: 2,
      exhaustedFailure: 'crash_loop',
      manualRetriesRemainingBefore: 1,
      manualRetryState: 'ready',
      activeSidecarExitedOnQuit: true,
      processExitedAfterWindowClose: true,
    })
  })

  it('rejects a substituted packaged sidecar before launch', async function () {
    if (!integrityEvidencePath || !integrityDiagnosticsPath) this.skip()
    if (!integrityEvidencePath || !integrityDiagnosticsPath) {
      throw new Error('Integrity evidence and diagnostics outputs are required')
    }
    const startedAt = Date.now()
    let phase = 'launch and readiness'
    let failure: unknown
    let processPid: number | null = null
    let processExited = false
    try {
      processPid = await mainPid()
      await expectStartupDiagnostics({
        state: 'degraded',
        failure: 'startup_failed',
        automaticRestartsRemaining: 2,
        manualRetriesRemaining: 1,
      })
      phase = 'rejection surface'
      await expectSafeDiagnosticsAlert(/The desktop service did not start\./u)
      phase = 'manual rejection'
      await click('button=Retry desktop service')
      await expectStartupDiagnostics({
        state: 'degraded',
        failure: 'startup_failed',
        automaticRestartsRemaining: 2,
        manualRetriesRemaining: 0,
      })
      phase = 'termination'
      await terminateVerificationApplication()
      processExited = true
      phase = 'write fault evidence'
      await writeFaultEvidence(integrityEvidencePath, 'sidecar-integrity-substitution', {
        failure: 'startup_failed',
        automaticRestartsRemaining: 2,
        manualRetriesRemainingBefore: 1,
        manualRetryFailure: 'startup_failed',
        manualRetriesRemainingAfter: 0,
        verificationProcessTerminated: true,
      })
    } catch (error) {
      failure = error
      if (processPid !== null && (await processSnapshot()).some((record) => record.pid === processPid)) {
        await killProcess(processPid).catch(() => undefined)
        await expectProcessAbsent(processPid).catch(() => undefined)
        processExited = !(await processSnapshot()).some((record) => record.pid === processPid)
      }
    }
    await writeIntegrityDiagnostics(integrityDiagnosticsPath, {
      phase,
      elapsedMs: Date.now() - startedAt,
      status: failure ? 'failed' : 'passed',
      processPid,
      processExited,
      errorName: failure instanceof Error ? failure.name : failure ? 'unknown' : null,
    })
    if (failure) throw failure
  })

  it('mediates opaque packaged open and save file grants', async function () {
    if (
      fileGrantVerificationMarker !== '1'
      || !fileGrantOpenPath
      || !fileGrantSavePath
      || !fileGrantEvidencePath
    ) this.skip()
    if (!fileGrantOpenPath || !fileGrantSavePath || !fileGrantEvidencePath) {
      throw new Error('Packaged file-grant verification inputs and evidence output are required')
    }
    const normalizedOpenPath = normalizeVerificationSelection(fileGrantOpenPath)
    const normalizedSavePath = normalizeVerificationSelection(fileGrantSavePath)
    if (normalizedOpenPath === null || normalizedSavePath === null) {
      throw new Error('Packaged file-grant verification paths are invalid')
    }
    const results = await browser.execute(async () => {
      const ancestry = (window as unknown as { ancestry: AncestryBridge }).ancestry
      const open = await ancestry.requestOpenFileGrant({ purpose: 'gedcom-read' })
      const save = await ancestry.requestSaveFileGrant({
        purpose: 'gedcom-write',
        suggestedName: 'safe-output.ged',
      })
      const openRevocation = open.ok && open.data !== null
        ? await ancestry.revokeFileGrant(open.data.grantId)
        : null
      const saveRevocation = save.ok && save.data !== null
        ? await ancestry.revokeFileGrant(save.data.grantId)
        : null
      return { open, save, openRevocation, saveRevocation }
    })
    assert.equal(results.open.ok ? 'ok' : results.open.error.code, 'ok')
    assert.equal(results.save.ok ? 'ok' : results.save.error.code, 'ok')
    if (!results.open.ok || results.open.data === null || !results.save.ok || results.save.data === null) {
      throw new Error('Packaged file-grant mediation did not return both grants')
    }
    const openGrant = results.open.data
    const saveGrant = results.save.data
    assert.match(openGrant.grantId, /^grt_[a-f0-9]{64}$/u)
    assert.match(saveGrant.grantId, /^grt_[a-f0-9]{64}$/u)
    assert.notEqual(saveGrant.grantId, openGrant.grantId)
    assert.deepEqual(Object.keys(openGrant).sort(), ['access', 'grantId', 'metadata', 'purpose', 'scope'])
    assert.deepEqual(Object.keys(saveGrant).sort(), ['access', 'grantId', 'metadata', 'purpose', 'scope'])
    assert.deepEqual({
      purpose: openGrant.purpose,
      access: openGrant.access,
      scope: openGrant.scope,
      displayName: openGrant.metadata.displayName,
      format: openGrant.metadata.format,
      validation: openGrant.metadata.validation,
    }, {
      purpose: 'gedcom-read',
      access: 'read',
      scope: {
        originatingWindow: 'requesting-window',
        lifetime: 'app-session',
        redemption: 'single-use',
      },
      displayName: basename(fileGrantOpenPath),
      format: 'gedcom',
      validation: 'validated-input',
    })
    assert.ok(openGrant.metadata.sizeBytes > 0)
    assert.deepEqual({
      purpose: saveGrant.purpose,
      access: saveGrant.access,
      scope: saveGrant.scope,
      displayName: saveGrant.metadata.displayName,
      format: saveGrant.metadata.format,
      validation: saveGrant.metadata.validation,
    }, {
      purpose: 'gedcom-write',
      access: 'write',
      scope: {
        originatingWindow: 'requesting-window',
        lifetime: 'app-session',
        redemption: 'single-use',
      },
      displayName: basename(fileGrantSavePath),
      format: 'gedcom',
      validation: 'replacement-confirmed',
    })
    assert.deepEqual(results.openRevocation, { ok: true, data: { revoked: true } })
    assert.deepEqual(results.saveRevocation, { ok: true, data: { revoked: true } })
    const exposedStrings = stringsIn(results)
    for (const selectedPath of new Set([
      fileGrantOpenPath,
      normalizedOpenPath,
      fileGrantSavePath,
      normalizedSavePath,
    ])) assert.equal(exposedStrings.includes(selectedPath), false)
    await closeApplicationWindow()
    await writeFileGrantEvidence(fileGrantEvidencePath)
  })

  it('launches production normally without a debugging transport', async () => {
    if (!packagedExecutable) throw new Error('ANCESTRYLLM_PACKAGED_APP is required')
    await closeApplicationWindow()
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-normal-launch-'))
    try {
      await expectNormalLaunchWithoutDebugSurface(root)
    } finally {
      await rm(root, { force: true, recursive: true, maxRetries: 10, retryDelay: 100 })
    }
  })
})
