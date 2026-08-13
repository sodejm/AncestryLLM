import { execFile, spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { chmod, copyFile, cp, lstat, mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { get as httpGet } from 'node:http'
import { tmpdir } from 'node:os'
import { basename, dirname, join, relative, resolve } from 'node:path'
import { promisify } from 'node:util'
import {
  chromium,
  expect,
  test,
  type Browser,
  type Page,
} from '@playwright/test'
import { PRODUCTION_CSP } from '../src/main/security-policy'
import type { AncestryBridge, StartupDiagnostics } from '../src/shared-contract/desktop'
import { outputContainsWindowReadyRecord } from '../src/main/window-readiness'
import { bridgeMethods } from './bridge-contract'
import { normalizeVerificationSelection } from './native-file-dialogs.packaged-verification'
import { withinDeadline } from './packaged-deadline'

const executablePath = process.env.ANCESTRYLLM_PACKAGED_APP
const metricsPath = process.env.ANCESTRYLLM_PACKAGED_METRICS
const packagedAttachTimeoutMs = 45_000
const packagedLaunchTimeoutMs = 120_000
const packagedCleanupTimeoutMs = 10_000
const packagedQuitTimeoutMs = 30_000
const withholdEvidencePath = process.env.ANCESTRYLLM_WITHHOLD_EVIDENCE
const restartEvidencePath = process.env.ANCESTRYLLM_RESTART_EVIDENCE
const integrityEvidencePath = process.env.ANCESTRYLLM_INTEGRITY_EVIDENCE
const integrityDiagnosticsPath = process.env.ANCESTRYLLM_INTEGRITY_DIAGNOSTICS
const substitutedSidecarPath = process.env.ANCESTRYLLM_SUBSTITUTED_SIDECAR
const fileGrantVerificationMarker = process.env.ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION
const fileGrantOpenPath = process.env.ANCESTRYLLM_FILE_GRANT_OPEN_PATH
const fileGrantSavePath = process.env.ANCESTRYLLM_FILE_GRANT_SAVE_PATH
const fileGrantEvidencePath = process.env.ANCESTRYLLM_FILE_GRANT_EVIDENCE
const execFileAsync = promisify(execFile)

type LaunchResult = Readonly<{
  browser: Browser
  page: Page
  process: ChildProcessWithoutNullStreams
  launchMs: number
  readyMs: number
  userData: string
}>

type ExitStatus = Readonly<{
  code: number | null
  signal: NodeJS.Signals | null
}>

type ProcessRecord = Readonly<{
  pid: number
  ppid: number
  rssBytes: number
  commandLine: string
}>

type StartupDiagnosticsExpectation = Readonly<{
  state: StartupDiagnostics['state']
  failure: StartupDiagnostics['failure']
  automaticRestartsRemaining: number
  manualRetriesRemaining: number
  report?: Readonly<{
    schema_version: 1
    status: NonNullable<StartupDiagnostics['report']>['status']
  }>
}>

type PackageCopy = Readonly<{
  executablePath: string
  packageRoot: string
  sidecarPath: string
}>

const READY_DIAGNOSTICS: StartupDiagnosticsExpectation = Object.freeze({
  state: 'ready',
  failure: null,
  automaticRestartsRemaining: 2,
  manualRetriesRemaining: 1,
  report: Object.freeze({
    schema_version: 1,
    status: 'ready',
  }),
})

const DEBUG_ARGUMENT = /(?:^|\s)--(?:remote-debugging(?:-address|-port|-pipe)?|inspect(?:-brk)?)(?:=|\s|$)/
const CAPABILITY_SUMMARY_READY = /^(?:No control capabilities are currently available\.|\d+ local control (?:module is|modules are) available\.)$/

type DevToolsVersion = Readonly<{
  webSocketDebuggerUrl?: string
}>

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
  const verificationNames = [
    'ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION',
    'ANCESTRYLLM_FILE_GRANT_OPEN_PATH',
    'ANCESTRYLLM_FILE_GRANT_SAVE_PATH',
  ] as const
  const environment: Record<string, string> = {
    ...inheritedEnvironment([...baseNames, ...platformNames, ...verificationNames]),
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
  // Linux verification supplies a disposable native Secret Service whose
  // collection and D-Bus runtime live under these launcher-owned directories.
  // Keep the packaged process in that session. Chromium state remains isolated
  // by --user-data-dir and the explicit app-data paths.
  if (
    process.platform === 'linux'
    && process.env.ANCESTRYLLM_NATIVE_KEYRING_SESSION === '1'
  ) {
    Object.assign(environment, inheritedEnvironment(['HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR']))
    environment.ANCESTRYLLM_NATIVE_KEYRING_SESSION = '1'
  } else if (process.platform !== 'darwin') {
    // Electron consults the login keychain before it creates a renderer on
    // macOS. Replacing HOME or CFFIXED_USER_HOME can block that lookup.
    environment.HOME = isolatedHome
  }
  const packagedRuntimePath = process.env.ANCESTRYLLM_PACKAGED_RUNTIME_PATH
  if (packagedRuntimePath !== undefined) environment.PATH = packagedRuntimePath
  return environment
}

function waitForDevToolsEndpoint(child: ChildProcessWithoutNullStreams): Promise<string> {
  return new Promise((resolve, reject) => {
    let output = ''
    let settled = false
    const timeout = setTimeout(() => {
      if (settled) return
      settled = true
      reject(new Error(`Timed out waiting for packaged CDP endpoint.\n${output}`))
    }, 20_000)

    const consume = (chunk: Buffer): void => {
      output = `${output}${chunk.toString('utf8')}`.slice(-32_768)
      const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/)
      if (!match?.[1] || settled) return
      settled = true
      clearTimeout(timeout)
      resolve(match[1])
    }
    child.stdout.on('data', consume)
    child.stderr.on('data', consume)
    child.once('error', (error) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      reject(error)
    })
    child.once('exit', (code, signal) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      reject(new Error(
        `Packaged app exited before exposing CDP (code=${String(code)}, signal=${String(signal)}).\n${output}`,
      ))
    })
  })
}

async function verifiedDevToolsEndpoint(loggedEndpoint: string): Promise<string> {
  const parsed = new URL(loggedEndpoint)
  const versionUrl = `http://${parsed.host}/json/version`
  const body = await new Promise<string>((resolve, reject) => {
    const request = httpGet(versionUrl, { agent: false }, (response) => {
      if (response.statusCode !== 200) {
        response.resume()
        reject(new Error(
          `Packaged CDP probe returned HTTP ${String(response.statusCode)} at ${versionUrl}.`,
        ))
        return
      }
      let value = ''
      response.setEncoding('utf8')
      response.on('data', (chunk: string) => { value += chunk })
      response.once('end', () => resolve(value))
      response.once('error', reject)
    })
    request.setTimeout(10_000, () => {
      request.destroy(new Error(`Packaged CDP probe timed out at ${versionUrl}.`))
    })
    request.once('error', reject)
  })
  const version = JSON.parse(body) as DevToolsVersion
  if (!version.webSocketDebuggerUrl) {
    throw new Error(`Packaged CDP probe at ${versionUrl} omitted webSocketDebuggerUrl.`)
  }
  return version.webSocketDebuggerUrl
}

function waitForProcessExit(
  child: ChildProcessWithoutNullStreams,
  timeoutMs: number,
): Promise<ExitStatus> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode })
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.off('exit', onExit)
      reject(new Error(`Packaged app PID ${String(child.pid)} did not exit within ${String(timeoutMs)}ms.`))
    }, timeoutMs)
    const onExit = (code: number | null, signal: NodeJS.Signals | null): void => {
      clearTimeout(timer)
      resolve({ code, signal })
    }
    child.once('exit', onExit)
  })
}

async function forceClosePackaged(result: LaunchResult): Promise<void> {
  await withinDeadline('closing packaged browser automation', packagedCleanupTimeoutMs, () => result.browser.close())
    .catch(() => undefined)
  await forceCloseProcess(result.process)
}

async function forceCloseProcess(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return
  if (process.platform === 'win32' && child.pid) {
    await execFileAsync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
      encoding: 'utf8',
      timeout: 10_000,
      windowsHide: true,
    }).catch(async (error: unknown) => {
      if (child.exitCode !== null || child.signalCode !== null) return
      throw error
    })
    await waitForProcessExit(child, 10_000).catch(() => undefined)
    return
  }
  child.kill('SIGTERM')
  try {
    await waitForProcessExit(child, 2_000)
  } catch {
    child.kill('SIGKILL')
    await waitForProcessExit(child, 5_000).catch(() => undefined)
  }
}

async function removeTemporaryPackage(root: string): Promise<void> {
  await rm(root, {
    recursive: true,
    force: true,
    maxRetries: 10,
    retryDelay: 100,
  })
}

async function closePackaged(result: LaunchResult): Promise<void> {
  const session = await result.browser.newBrowserCDPSession()
  void session.send('Browser.close').catch(() => undefined)
  const status = await waitForProcessExit(result.process, packagedQuitTimeoutMs).finally(async () => {
    await session.detach().catch(() => undefined)
    await withinDeadline(
      'closing packaged browser automation',
      packagedCleanupTimeoutMs,
      () => result.browser.close(),
    ).catch(() => undefined)
  })
  expect(status).toEqual({ code: 0, signal: null })
}

async function launchPackaged(
  root: string,
  expectedHeading: RegExp,
  phase: string,
  applicationExecutable: string = executablePath ?? '',
  expectedDiagnostics: StartupDiagnosticsExpectation = READY_DIAGNOSTICS,
): Promise<LaunchResult> {
  if (!applicationExecutable) throw new Error('ANCESTRYLLM_PACKAGED_APP is required')
  const startedAt = Date.now()
  const automationArguments = process.platform === 'darwin' ? ['--use-mock-keychain'] : []
  const child = spawn(applicationExecutable, [
    ...automationArguments,
    '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port=0',
    `--user-data-dir=${root}`,
    `--disk-cache-dir=${join(root, 'chromium-cache')}`,
    `--crash-dumps-dir=${join(root, 'crash-dumps')}`,
  ], {
    env: await isolatedEnvironment(root),
    stdio: 'pipe',
    windowsHide: true,
  })
  let browser: Browser | undefined
  try {
    return await withinDeadline(`launching packaged ${phase}`, packagedLaunchTimeoutMs, async () => {
      const loggedEndpoint = await waitForDevToolsEndpoint(child)
      const endpoint = await verifiedDevToolsEndpoint(loggedEndpoint)
      try {
        // A deliberately unavailable sidecar consumes the supervisor's bounded
        // startup attempts before Electron creates the renderer. Keep this
        // verifier-only timeout longer than that production retry window.
        browser = await chromium.connectOverCDP(endpoint, { timeout: packagedAttachTimeoutMs })
      } catch (error) {
        throw new Error(
          `Packaged CDP attach failed after a successful /json/version probe: ${error instanceof Error ? error.message : String(error)}`,
          { cause: error },
        )
      }
      const context = browser.contexts()[0]
      if (!context) throw new Error('Packaged CDP session has no browser context.')
      const page = context.pages()[0] ?? await context.waitForEvent('page', {
        timeout: packagedAttachTimeoutMs,
      })
      await page.waitForLoadState('domcontentloaded', { timeout: packagedAttachTimeoutMs })
      await expect(page.getByRole('heading', { name: expectedHeading })).toBeVisible()
      const launchMs = Date.now() - startedAt
      await expectStartupDiagnostics(page, expectedDiagnostics)
      const readyMs = Date.now() - startedAt
      return {
        browser,
        page,
        process: child,
        launchMs,
        readyMs,
        userData: root,
      }
    })
  } catch (error) {
    if (browser) {
      const failedBrowser = browser
      await withinDeadline('closing failed packaged browser automation', packagedCleanupTimeoutMs, () => failedBrowser.close())
        .catch(() => undefined)
    }
    await forceCloseProcess(child).catch(() => undefined)
    throw new Error(
      `Packaged ${phase} launch failed: ${error instanceof Error ? error.message : String(error)}`,
      { cause: error },
    )
  }
}

async function startupDiagnostics(page: Page): Promise<StartupDiagnostics> {
  return withinDeadline('reading packaged startup diagnostics', 15_000, () => page.evaluate(async () => {
    const result = await (window as unknown as {
      ancestry: {
        getStartupDiagnostics(): Promise<{
          ok: boolean
          data?: StartupDiagnostics
          error?: { code: string }
        }>
      }
    }).ancestry.getStartupDiagnostics()
    if (!result.ok || !result.data) {
      throw new Error(`Could not read packaged startup diagnostics: ${result.error?.code ?? 'unknown'}`)
    }
    return result.data
  }))
}

async function expectStartupDiagnostics(
  page: Page,
  expected: StartupDiagnosticsExpectation,
): Promise<void> {
  let lastActual: StartupDiagnostics | null = null
  try {
    await expect.poll(
      async () => {
        const actual = await startupDiagnostics(page).catch(() => null)
        if (actual !== null) lastActual = actual
        return actual ?? {}
      },
      { timeout: 30_000 },
    ).toMatchObject(expected)
  } catch (error) {
    const actual = await startupDiagnostics(page).catch(() => lastActual)
    throw new Error(
      `Packaged startup diagnostics did not match expected state: ${JSON.stringify(actual)}`,
      { cause: error },
    )
  }
}

function packageRootForExecutable(applicationExecutable: string): string {
  let current = resolve(applicationExecutable)
  if (process.platform !== 'darwin') return dirname(current)
  while (dirname(current) !== current) {
    if (basename(current).endsWith('.app')) return current
    current = dirname(current)
  }
  throw new Error(`Packaged macOS executable is not inside an app bundle: ${applicationExecutable}`)
}

function packagedSidecarPath(packageRoot: string): string {
  const target = `${process.platform}-${process.arch}`
  const resources = process.platform === 'darwin'
    ? join(packageRoot, 'Contents', 'Resources')
    : join(packageRoot, 'resources')
  const suffix = process.platform === 'win32' ? '.exe' : ''
  return join(
    resources,
    'sidecar',
    target,
    'ancestryllm-sidecar',
    `ancestryllm-sidecar${suffix}`,
  )
}

async function prepareCopiedLinuxSandbox(packageRoot: string): Promise<void> {
  if (process.platform !== 'linux') return
  const sandboxPath = join(packageRoot, 'chrome-sandbox')
  const sandbox = await lstat(sandboxPath)
  if (sandbox.isSymbolicLink() || !sandbox.isFile()) {
    throw new Error(`Copied Chromium sandbox is not a regular file: ${sandboxPath}`)
  }

  // Copying the unpacked application clears the root owner required by
  // Chromium's SUID sandbox. GitHub-hosted Ubuntu runners provide
  // non-interactive sudo, so restore the production sandbox contract on each
  // disposable fault-injection package instead of disabling the sandbox.
  await execFileAsync('sudo', ['--non-interactive', 'chown', 'root:root', '--', sandboxPath])
  await execFileAsync('sudo', ['--non-interactive', 'chmod', '4755', '--', sandboxPath])

  const prepared = await lstat(sandboxPath)
  if (prepared.uid !== 0 || prepared.gid !== 0 || (prepared.mode & 0o7777) !== 0o4755) {
    throw new Error(`Copied Chromium sandbox is not configured root:root:4755: ${sandboxPath}`)
  }
}

async function copyPackagedApplication(root: string): Promise<PackageCopy> {
  if (!executablePath) throw new Error('ANCESTRYLLM_PACKAGED_APP is required')
  const sourcePackageRoot = packageRootForExecutable(executablePath)
  const packageRoot = join(root, basename(sourcePackageRoot))
  if (process.platform === 'darwin') {
    // Preserve framework symlinks and bundle metadata so the disposable copy
    // remains eligible for ad-hoc re-signing after its sidecar is mutated.
    await execFileAsync('ditto', ['--noqtn', sourcePackageRoot, packageRoot])
  } else {
    await cp(sourcePackageRoot, packageRoot, { recursive: true, preserveTimestamps: true })
  }
  await prepareCopiedLinuxSandbox(packageRoot)
  return {
    executablePath: join(packageRoot, relative(sourcePackageRoot, resolve(executablePath))),
    packageRoot,
    sidecarPath: packagedSidecarPath(packageRoot),
  }
}

async function signVerificationPackage(packageRoot: string): Promise<void> {
  if (process.platform !== 'darwin') return
  await execFileAsync('codesign', ['--force', '--deep', '--sign', '-', packageRoot], {
    encoding: 'utf8',
    maxBuffer: 8 * 1024 * 1024,
  })
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

function stringsIn(value: unknown): string[] {
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(stringsIn)
  if (value !== null && typeof value === 'object') {
    return Object.values(value).flatMap(stringsIn)
  }
  return []
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

async function expectSafeDiagnosticsAlert(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Diagnostics', exact: true }).click()
  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible()
  await expect(alert).not.toContainText(/token|secret|stderr|\.json|[/\\](?:Users|home|AppData)[/\\]/i)
}

async function sidecarPid(
  applicationPid: number,
  copiedSidecarPath: string,
  excluded: ReadonlySet<number> = new Set(),
): Promise<number> {
  let observed = -1
  await expect.poll(async () => {
    const tree = descendantProcessTree(await processSnapshot(), applicationPid)
    const sidecar = tree.find((record) => (
      !excluded.has(record.pid) && record.commandLine.includes(copiedSidecarPath)
    ))
    observed = sidecar?.pid ?? -1
    return observed > 0
  }, { timeout: 30_000 }).toBe(true)
  return observed
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

async function expectProcessAbsent(pid: number): Promise<void> {
  await expect.poll(
    async () => (await processSnapshot()).some((record) => record.pid === pid),
    { timeout: 15_000 },
  ).toBe(false)
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
    const match = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/)
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

function isSandboxedRendererProcess(
  record: ProcessRecord,
  rendererPids: ReadonlySet<number>,
): boolean {
  return rendererPids.has(record.pid)
    && !record.commandLine.includes('--no-sandbox')
}

async function packagedProcessTreeMetrics(browser: Browser, rootPid: number): Promise<number> {
  const session = await browser.newBrowserCDPSession()
  let tree: ProcessRecord[] = []
  let browserProcesses: Awaited<
    ReturnType<typeof session.send<'SystemInfo.getProcessInfo'>>
  >['processInfo'] = []

  try {
    await expect.poll(async () => {
      const [{ processInfo }, snapshot] = await Promise.all([
        session.send('SystemInfo.getProcessInfo'),
        processSnapshot(),
      ])
      browserProcesses = processInfo
      tree = descendantProcessTree(snapshot, rootPid)
      const rendererPids = new Set(
        processInfo
          .filter((record) => record.type === 'renderer')
          .map((record) => record.id),
      )
      return {
        browserMatchesRoot: processInfo.some(
          (record) => record.type === 'browser' && record.id === rootPid,
        ),
        sandboxedRendererCorrelated: tree.some((record) =>
          isSandboxedRendererProcess(record, rendererPids)),
      }
    }, { timeout: 30_000 }).toEqual({
      browserMatchesRoot: true,
      sandboxedRendererCorrelated: true,
    })

    expect(tree.some((record) => record.pid === rootPid)).toBe(true)
    const rendererPids = new Set(
      browserProcesses
        .filter((record) => record.type === 'renderer')
        .map((record) => record.id),
    )
    const correlatedRenderers = browserProcesses.filter(
      (record) => record.type === 'renderer' && tree.some((process) => process.pid === record.id),
    )
    const browserProcess = browserProcesses.find(
      (record) => record.type === 'browser' && record.id === rootPid,
    )
    const correlatedRendererProcesses = tree.filter((record) =>
      isSandboxedRendererProcess(record, rendererPids))
    expect(browserProcess).toBeDefined()
    expect(correlatedRenderers.length).toBeGreaterThan(0)
    expect(correlatedRendererProcesses.length).toBeGreaterThan(0)
    for (const record of [browserProcess, ...correlatedRenderers]) {
      expect(Number.isInteger(record?.id)).toBe(true)
      expect(record?.id).toBeGreaterThan(0)
      expect(Number.isFinite(record?.cpuTime)).toBe(true)
      expect(record?.cpuTime).toBeGreaterThanOrEqual(0)
    }
    expect(tree.some((record) => isSandboxedRendererProcess(record, rendererPids))).toBe(true)
    expect(tree.map((record) => record.commandLine).join('\n')).not.toMatch(/(?:^|\s)--inspect(?:-brk)?(?:=|\s|$)/)
    const rssBytes = tree.reduce((total, record) => total + record.rssBytes, 0)
    expect(rssBytes).toBeGreaterThan(0)
    return rssBytes
  } finally {
    await session.detach()
  }
}

async function expectNormalLaunchWithoutDebugSurface(root: string): Promise<void> {
  if (!executablePath) throw new Error('ANCESTRYLLM_PACKAGED_APP is required')
  const automationArguments = process.platform === 'darwin' ? ['--use-mock-keychain'] : []
  const launchArguments = [
    ...automationArguments,
    `--user-data-dir=${root}`,
    `--disk-cache-dir=${join(root, 'chromium-cache')}`,
    `--crash-dumps-dir=${join(root, 'crash-dumps')}`,
  ]
  const child = spawn(executablePath, launchArguments, {
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

  try {
    const rootPid = child.pid
    if (!rootPid) throw new Error('Normal packaged launch PID is unavailable.')
    const deadline = Date.now() + 30_000
    let tree: ProcessRecord[] = []
    let windowReady = false
    const observedCommandLines = new Set<string>()
    while (Date.now() < deadline) {
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error(
          `Normal packaged launch exited before its renderer window was ready `
          + `(code=${String(child.exitCode)}, signal=${String(child.signalCode)}).\n${output}`,
        )
      }
      tree = descendantProcessTree(await processSnapshot(), rootPid)
      for (const record of tree) observedCommandLines.add(record.commandLine)
      const rssBytes = tree.reduce((total, record) => total + record.rssBytes, 0)
      windowReady = outputContainsWindowReadyRecord(output)
      if (windowReady && tree.some((record) => record.pid === rootPid) && rssBytes > 0) break
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
    expect(tree.some((record) => record.pid === rootPid)).toBe(true)
    expect(tree.reduce((total, record) => total + record.rssBytes, 0)).toBeGreaterThan(0)
    expect(windowReady).toBe(true)
    expect(launchArguments.join('\n')).not.toMatch(DEBUG_ARGUMENT)
    expect([...observedCommandLines].join('\n')).not.toMatch(DEBUG_ARGUMENT)
    expect(output).not.toContain('DevTools listening on ')
  } finally {
    await forceCloseProcess(child)
  }
}

async function expectProductionBoundary(page: Page, browser: Browser, rootPid: number): Promise<number> {
  const appUrl = new URL(page.url())
  expect({
    protocol: appUrl.protocol,
    hostname: appUrl.hostname,
    pathname: appUrl.pathname,
    search: appUrl.search,
  }).toEqual({
    protocol: 'app:',
    hostname: 'bundle',
    pathname: '/index.html',
    search: '',
  })
  expect(await page.evaluate(() => typeof (globalThis as { process?: unknown }).process)).toBe('undefined')
  expect(await page.evaluate(() => {
    const ancestry = (window as unknown as { ancestry: object }).ancestry
    return { frozen: Object.isFrozen(ancestry), methods: Object.keys(ancestry).sort() }
  })).toEqual({ frozen: true, methods: bridgeMethods })

  await page.evaluate(() => history.replaceState(null, '', location.pathname))
  const response = await page.reload()
  expect(await response?.headerValue('content-security-policy')).toBe(PRODUCTION_CSP)

  // The shell owns an initial capability read after startup succeeds. Wait for
  // its rendered result so the verifier burst measures all 32 reader slots.
  await expect(page.getByText(CAPABILITY_SUMMARY_READY)).toBeVisible()

  const capabilityBurst = await withinDeadline(
    'running bounded packaged capability bridge burst',
    10_000,
    () => page.evaluate(async () => {
      const ancestry = (window as unknown as {
        ancestry: {
          getCapabilities(): Promise<{
            ok: boolean
            error?: { code: string }
          }>
        }
      }).ancestry
      const responses = await Promise.all(
        Array.from({ length: 32 }, () => ancestry.getCapabilities()),
      )
      return {
        successful: responses.filter((result) => result.ok).length,
        overloaded: responses.filter(
          (result) => !result.ok && result.error?.code === 'BRIDGE_OVERLOADED',
        ).length,
        count: responses.length,
        unexpectedErrorCodes: [...new Set(responses.flatMap(
          (result) => !result.ok && result.error?.code !== 'BRIDGE_OVERLOADED'
            ? [result.error?.code ?? 'unknown']
            : [],
        ))].sort(),
      }
    }),
  )
  expect(capabilityBurst.count).toBe(32)
  expect(capabilityBurst.successful).toBe(32)
  expect(capabilityBurst.overloaded).toBe(0)
  expect(capabilityBurst.unexpectedErrorCodes).toEqual([])

  const externalRequests: string[] = []
  page.on('request', (request) => {
    if (/^(?:https?|wss?):/i.test(request.url())) externalRequests.push(request.url())
  })
  const denied = await page.evaluate(async () => {
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
    }
  })
  expect(denied).toEqual({
    fetchBlocked: true,
    webSocketBlocked: true,
    serviceWorkerBlocked: true,
    childWindowBlocked: true,
  })
  expect(externalRequests).toEqual([])
  return packagedProcessTreeMetrics(browser, rootPid)
}

async function expectAccessibleShell(page: Page): Promise<void> {
  await expect(page.locator('header')).toHaveCount(1)
  await expect(page.getByRole('navigation', { name: 'Primary' })).toHaveCount(1)
  await expect(page.getByRole('main')).toHaveCount(1)
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('link')).toHaveText([
    'Home',
    'Tasks',
    'Diagnostics',
    'Settings',
  ])

  await page.getByRole('link', { name: 'Settings' }).press('Enter')
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeFocused()
  const theme = page.getByRole('group', { name: 'Theme' })
  await expect(theme.getByRole('radio')).toHaveCount(3)
  await theme.getByRole('radio', { name: 'dark' }).click()
  await expect(theme.getByRole('radio', { name: 'dark' })).toBeChecked()
  const reducedMotion = page.getByRole('checkbox', { name: 'Reduce motion' })
  await reducedMotion.click()
  await expect(reducedMotion).toBeChecked()
  await expect.poll(() => page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    reducedMotion: document.documentElement.dataset.reducedMotion,
  }))).toEqual({ theme: 'dark', reducedMotion: 'true' })

  const visualChecks = await page.evaluate(() => {
    const rgb = (value: string): [number, number, number] => {
      const channels = value.match(/[\d.]+/g)?.slice(0, 3).map(Number)
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
  expect(visualChecks.bodyContrast).toBeGreaterThanOrEqual(4.5)
  expect(visualChecks.headerContrast).toBeGreaterThanOrEqual(4.5)
  expect(visualChecks.reducedAnimationMs).toBeLessThanOrEqual(0.001)
  expect(visualChecks.reducedTransitionMs).toBeLessThanOrEqual(0.001)

  // CDP keyboard events are injected in the renderer and do not traverse
  // Electron's browser-process before-input-event hook. The shortcut policy is
  // covered by zoom-policy.test.ts; this packaged pass exercises the rendered
  // shell at the same maximum 200% scale.
  await page.evaluate(() => { document.documentElement.style.zoom = '200%' })
  const zoomState = await page.evaluate(() => ({
    zoom: getComputedStyle(document.documentElement).zoom,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }))
  expect(zoomState.zoom).toBe('2')
  expect(zoomState.overflow).toBe(0)
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible()
  await page.evaluate(() => { document.documentElement.style.removeProperty('zoom') })

  const client = await page.context().newCDPSession(page)
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 360,
    height: 620,
    deviceScaleFactor: 1,
    mobile: false,
    screenWidth: 360,
    screenHeight: 620,
  })
  try {
    await expect.poll(() => page.evaluate(() => ({
      viewport: window.innerWidth,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }))).toEqual({ viewport: 360, overflow: 0 })
    await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible()
  } finally {
    await client.send('Emulation.clearDeviceMetricsOverride')
    await client.detach()
  }
}

test.describe('unpublished unpacked native package', () => {
  test.skip(!executablePath, 'Packaged executable is required')
  test.describe.configure({ mode: 'serial', timeout: 300_000 })

  test('exercises first run, persistence, corrupt preferences, security, and resource evidence', async () => {
    test.skip(!metricsPath, 'Packaged metrics output is required')
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-packaged-'))
    let running: LaunchResult | undefined
    try {
      const cold = await launchPackaged(root, /Welcome to AncestryLLM/, 'cold')
      running = cold
      const page = cold.page
      await expect(page.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeFocused()
      await expect(page.getByRole('main')).toContainText('Your desktop control shell stays local to this device.')
      await page.getByRole('button', { name: 'Continue to Home' }).click()
      await expect(page.getByRole('heading', { name: 'Home' })).toBeFocused()
      await expect(page.getByText('Packaged build', { exact: true })).toBeVisible()
      await expect(page.getByText('Ready', { exact: true })).toBeVisible()
      await page.getByRole('link', { name: 'Diagnostics' }).press('Enter')
      await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeFocused()
      await expect(
        page.getByLabel('Desktop service').getByText('Ready', { exact: true }),
      ).toBeVisible()
      await expect(page.getByRole('alert')).toHaveCount(0)
      if (!cold.process.pid) throw new Error('Packaged app PID is unavailable.')
      const rssBytes = await expectProductionBoundary(page, cold.browser, cold.process.pid)
      await expectAccessibleShell(page)
      const userData = cold.userData
      await closePackaged(cold)
      running = undefined

      const preferencesPath = join(userData, 'preferences.json')
      const persisted = JSON.parse(await readFile(preferencesPath, 'utf8')) as Record<string, unknown>
      expect(persisted).toMatchObject({
        colorScheme: 'dark',
        reducedMotion: true,
        onboardingCompleted: true,
        schemaVersion: 1,
      })
      expect(Object.keys(persisted).sort()).toEqual([
        'colorScheme',
        'onboardingCompleted',
        'reducedMotion',
        'revision',
        'schemaVersion',
      ])

      const warm = await launchPackaged(root, /^Home$/, 'warm')
      running = warm
      await expect(warm.page.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toHaveCount(0)
      await expect(warm.page.locator('html')).toHaveAttribute('data-theme', 'dark')
      await expect(warm.page.locator('html')).toHaveAttribute('data-reduced-motion', 'true')
      await expect(warm.page.getByText('Ready', { exact: true })).toBeVisible()
      await closePackaged(warm)
      running = undefined

      const corruptPayload = '{fictional-corrupt-preferences'
      await writeFile(preferencesPath, corruptPayload, 'utf8')
      const corrupt = await launchPackaged(root, /Welcome to AncestryLLM/, 'corrupt-preferences')
      running = corrupt
      const continueButton = corrupt.page.getByRole('button', { name: 'Continue to Home' })
      await expect(continueButton).toBeEnabled()
      await continueButton.click()
      const alert = corrupt.page.getByRole('alert')
      await expect(alert).toContainText('PREFERENCES_UNAVAILABLE')
      await expect(corrupt.page.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeVisible()
      await expect(corrupt.page.getByRole('heading', { name: 'Home' })).toHaveCount(0)
      await expect(alert).not.toContainText(/token|secret|stderr|\.json|[/\\](?:Users|home|AppData)[/\\]/i)
      expect(await readFile(preferencesPath, 'utf8')).toBe(corruptPayload)
      await closePackaged(corrupt)
      running = undefined

      await expectNormalLaunchWithoutDebugSurface(join(root, 'normal-no-debug'))

      if (!metricsPath) throw new Error('ANCESTRYLLM_PACKAGED_METRICS is required')
      await writeFile(metricsPath, `${JSON.stringify({
        coldLaunchMs: cold.launchMs,
        warmLaunchMs: warm.launchMs,
        readyMs: cold.readyMs,
        rssBytes,
        rendererOutboundRequests: 0,
      }, null, 2)}\n`, { flag: 'wx' })
    } finally {
      if (running) await forceClosePackaged(running)
      await removeTemporaryPackage(root)
    }
  })

  test('mediates opaque packaged open and save file grants', async () => {
    test.skip(
      fileGrantVerificationMarker !== '1'
      || !fileGrantOpenPath
      || !fileGrantSavePath
      || !fileGrantEvidencePath,
      'Packaged file-grant verification inputs and evidence output are required',
    )
    if (!fileGrantOpenPath || !fileGrantSavePath || !fileGrantEvidencePath) {
      throw new Error('Packaged file-grant verification inputs and evidence output are required')
    }
    const normalizedFileGrantOpenPath = normalizeVerificationSelection(fileGrantOpenPath)
    const normalizedFileGrantSavePath = normalizeVerificationSelection(fileGrantSavePath)
    if (normalizedFileGrantOpenPath === null || normalizedFileGrantSavePath === null) {
      throw new Error('Packaged file-grant verification paths are invalid')
    }
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-file-grants-'))
    let running: LaunchResult | undefined
    try {
      running = await launchPackaged(root, /Welcome to AncestryLLM/, 'file-grant-mediation')
      const results = await running.page.evaluate(async () => {
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

      expect(results.open.ok ? 'ok' : results.open.error.code).toBe('ok')
      expect(results.save.ok ? 'ok' : results.save.error.code).toBe('ok')
      if (!results.open.ok || results.open.data === null
        || !results.save.ok || results.save.data === null) {
        throw new Error('Packaged file-grant mediation did not return both grants.')
      }
      const openGrant = results.open.data
      const saveGrant = results.save.data
      expect(openGrant.grantId).toMatch(/^grt_[a-f0-9]{64}$/)
      expect(saveGrant.grantId).toMatch(/^grt_[a-f0-9]{64}$/)
      expect(saveGrant.grantId).not.toBe(openGrant.grantId)
      expect(Object.keys(openGrant).sort()).toEqual(['access', 'grantId', 'metadata', 'purpose', 'scope'])
      expect(Object.keys(saveGrant).sort()).toEqual(['access', 'grantId', 'metadata', 'purpose', 'scope'])
      expect(openGrant).toMatchObject({
        purpose: 'gedcom-read',
        access: 'read',
        scope: {
          originatingWindow: 'requesting-window',
          lifetime: 'app-session',
          redemption: 'single-use',
        },
        metadata: {
          displayName: basename(fileGrantOpenPath),
          format: 'gedcom',
          validation: 'validated-input',
        },
      })
      expect(openGrant.metadata.sizeBytes).toBeGreaterThan(0)
      expect(saveGrant).toMatchObject({
        purpose: 'gedcom-write',
        access: 'write',
        scope: {
          originatingWindow: 'requesting-window',
          lifetime: 'app-session',
          redemption: 'single-use',
        },
        metadata: {
          displayName: basename(fileGrantSavePath),
          format: 'gedcom',
          validation: 'replacement-confirmed',
        },
      })
      expect(results.openRevocation).toMatchObject({ ok: true, data: { revoked: true } })
      expect(results.saveRevocation).toMatchObject({ ok: true, data: { revoked: true } })
      const exposedStrings = stringsIn(results)
      const selectedPathRepresentations = new Set([
        fileGrantOpenPath,
        normalizedFileGrantOpenPath,
        fileGrantSavePath,
        normalizedFileGrantSavePath,
      ])
      for (const selectedPath of selectedPathRepresentations) {
        expect(exposedStrings).not.toContain(selectedPath)
      }
      await closePackaged(running)
      running = undefined
      await writeFileGrantEvidence(fileGrantEvidencePath)
    } finally {
      if (running) await forceClosePackaged(running)
      await removeTemporaryPackage(root)
    }
  })

  test('withholds and restores the packaged sidecar through Diagnostics retry', async () => {
    test.skip(!withholdEvidencePath, 'Withhold/retry evidence output is required')
    if (!withholdEvidencePath) throw new Error('ANCESTRYLLM_WITHHOLD_EVIDENCE is required')
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-withheld-sidecar-'))
    let running: LaunchResult | undefined
    try {
      const copied = await copyPackagedApplication(root)
      const withheld = join(root, basename(copied.sidecarPath))
      await rename(copied.sidecarPath, withheld)
      await signVerificationPackage(copied.packageRoot)

      const degraded: StartupDiagnosticsExpectation = {
        state: 'degraded',
        failure: 'startup_failed',
        automaticRestartsRemaining: 2,
        manualRetriesRemaining: 1,
      }
      running = await launchPackaged(
        join(root, 'user-data'),
        /Welcome to AncestryLLM/,
        'withheld-sidecar',
        copied.executablePath,
        degraded,
      )
      await expectSafeDiagnosticsAlert(running.page)
      await expect(running.page.getByRole('alert')).toContainText('The desktop service did not start.')

      await rename(withheld, copied.sidecarPath)
      await running.page.getByRole('button', { name: 'Retry desktop service' }).click()
      await expectStartupDiagnostics(running.page, {
        state: 'ready',
        failure: null,
        automaticRestartsRemaining: 2,
        manualRetriesRemaining: 0,
      })
      await expect(running.page.getByRole('alert')).toHaveCount(0)
      await closePackaged(running)
      running = undefined
      await writeFaultEvidence(withholdEvidencePath, 'sidecar-withhold-retry', {
        failure: 'startup_failed',
        automaticRestartsRemaining: 2,
        manualRetriesRemainingBefore: 1,
        recoveredState: 'ready',
        cleanExit: true,
      })
    } finally {
      if (running) await forceClosePackaged(running)
      await removeTemporaryPackage(root)
    }
  })

  test('restarts a killed packaged sidecar, exhausts the budget, and cleans up on quit', async () => {
    test.skip(!restartEvidencePath, 'Restart/exhaustion evidence output is required')
    if (!restartEvidencePath) throw new Error('ANCESTRYLLM_RESTART_EVIDENCE is required')
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-killed-sidecar-'))
    let running: LaunchResult | undefined
    try {
      const copied = await copyPackagedApplication(root)
      await signVerificationPackage(copied.packageRoot)
      running = await launchPackaged(
        join(root, 'user-data'),
        /Welcome to AncestryLLM/,
        'killed-sidecar',
        copied.executablePath,
      )
      if (!running.process.pid) throw new Error('Packaged app PID is unavailable.')
      const killed = new Set<number>()

      const first = await sidecarPid(running.process.pid, copied.sidecarPath, killed)
      killed.add(first)
      await killProcess(first)
      await expectStartupDiagnostics(running.page, {
        state: 'ready',
        failure: null,
        automaticRestartsRemaining: 1,
        manualRetriesRemaining: 1,
      })

      const second = await sidecarPid(running.process.pid, copied.sidecarPath, killed)
      killed.add(second)
      await killProcess(second)
      await expectStartupDiagnostics(running.page, {
        state: 'ready',
        failure: null,
        automaticRestartsRemaining: 0,
        manualRetriesRemaining: 1,
      })

      const third = await sidecarPid(running.process.pid, copied.sidecarPath, killed)
      killed.add(third)
      await killProcess(third)
      await expectStartupDiagnostics(running.page, {
        state: 'degraded',
        failure: 'crash_loop',
        automaticRestartsRemaining: 0,
        manualRetriesRemaining: 1,
      })
      await expectSafeDiagnosticsAlert(running.page)
      await expect(running.page.getByRole('alert')).toContainText('The desktop service stopped repeatedly.')

      await running.page.getByRole('button', { name: 'Retry desktop service' }).click()
      await expectStartupDiagnostics(running.page, {
        state: 'ready',
        failure: null,
        automaticRestartsRemaining: 0,
        manualRetriesRemaining: 0,
      })
      const retried = await sidecarPid(running.process.pid, copied.sidecarPath, killed)
      await closePackaged(running)
      running = undefined
      await expectProcessAbsent(retried)
      await writeFaultEvidence(restartEvidencePath, 'sidecar-restart-exhaustion-quit', {
        automaticRestartCount: 2,
        exhaustedFailure: 'crash_loop',
        manualRetriesRemainingBefore: 1,
        manualRetryState: 'ready',
        activeSidecarExitedOnQuit: true,
        cleanExit: true,
      })
    } finally {
      if (running) await forceClosePackaged(running)
      await removeTemporaryPackage(root)
    }
  })

  test('rejects a target-native substituted packaged sidecar before spawn', async () => {
    test.skip(
      !integrityEvidencePath || !integrityDiagnosticsPath || !substitutedSidecarPath,
      'Integrity evidence output, diagnostics output, and substituted sidecar are required',
    )
    if (!integrityEvidencePath || !integrityDiagnosticsPath || !substitutedSidecarPath) {
      throw new Error(
        'ANCESTRYLLM_INTEGRITY_EVIDENCE, ANCESTRYLLM_INTEGRITY_DIAGNOSTICS, and ANCESTRYLLM_SUBSTITUTED_SIDECAR are required',
      )
    }
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-substituted-sidecar-'))
    let running: LaunchResult | undefined
    let phase = 'copy package'
    const startedAt = Date.now()
    let failure: unknown
    let primaryFailurePhase: string | null = null
    let processPid: number | null = null
    let processExited = false
    try {
      const copied = await copyPackagedApplication(root)
      phase = 'replace sidecar'
      await copyFile(substitutedSidecarPath, copied.sidecarPath)
      if (process.platform !== 'win32') await chmod(copied.sidecarPath, 0o755)
      await signVerificationPackage(copied.packageRoot)

      phase = 'launch and readiness'
      running = await launchPackaged(
        join(root, 'user-data'),
        /Welcome to AncestryLLM/,
        'substituted-sidecar',
        copied.executablePath,
        {
          state: 'degraded',
          failure: 'startup_failed',
          automaticRestartsRemaining: 2,
          manualRetriesRemaining: 1,
        },
      )
      processPid = running.process.pid ?? null
      phase = 'rejection surface'
      await expectSafeDiagnosticsAlert(running.page)
      await expect(running.page.getByRole('alert')).toContainText(
        'The desktop service did not start.',
      )
      phase = 'manual rejection'
      await running.page.getByRole('button', { name: 'Retry desktop service' }).click()
      await expectStartupDiagnostics(running.page, {
        state: 'degraded',
        failure: 'startup_failed',
        automaticRestartsRemaining: 2,
        manualRetriesRemaining: 0,
      })
      phase = 'termination'
      await forceClosePackaged(running)
      expect(running.process.exitCode !== null || running.process.signalCode !== null).toBe(true)
      processExited = true
      running = undefined
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
      primaryFailurePhase = phase
    }

    let cleanupFailure: unknown
    let cleanupFailurePhase: string | null = null
    try {
      phase = 'termination'
      if (running) {
        processPid = running.process.pid ?? processPid
        await forceClosePackaged(running)
        processExited = running.process.exitCode !== null || running.process.signalCode !== null
      }
      phase = 'temporary package cleanup'
      await removeTemporaryPackage(root)
    } catch (error) {
      cleanupFailure = error
      cleanupFailurePhase = phase
    }
    await writeIntegrityDiagnostics(integrityDiagnosticsPath, {
      phase: primaryFailurePhase ?? cleanupFailurePhase ?? phase,
      elapsedMs: Date.now() - startedAt,
      status: failure || cleanupFailure ? 'failed' : 'passed',
      processPid,
      processExited,
      errorName: failure instanceof Error
        ? failure.name
        : cleanupFailure instanceof Error
          ? cleanupFailure.name
          : failure || cleanupFailure
            ? 'unknown'
            : null,
    }).catch((diagnosticError: unknown) => {
      if (!failure && !cleanupFailure) cleanupFailure = diagnosticError
    })
    if (failure) throw failure
    if (cleanupFailure) throw cleanupFailure
  })
})
