/** Verifies that a selected packaged-runtime launch exposes no debugging transport. */

import assert from 'node:assert/strict'
import { execFile, spawn } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import { isAbsolute, join, resolve } from 'node:path'
import { promisify } from 'node:util'
import { pathToFileURL } from 'node:url'

const execFileAsync = promisify(execFile)
const WINDOW_READY_RECORD = '{"event":"ancestryllm.desktop.window-ready","version":1}'
const LINUX_KEYRING_VERIFICATION_SWITCH = 'ancestryllm-linux-keyring-verification-root'
const MACOS_EPHEMERAL_VERIFICATION_SWITCH = 'ancestryllm-macos-ephemeral-verification'

/**
 * @typedef {Readonly<{pid: number, ppid: number, rssBytes: number, commandLine: string}>} ProcessRecord
 */

/** Returns whether complete process-output lines contain the exact readiness record. */
export function outputContainsWindowReadyRecord(output) {
  return output.split(/\r?\n/u).includes(WINDOW_READY_RECORD)
}

/** Returns one root process and every descendant present in a native snapshot. */
export function descendantProcessTree(records, rootPid) {
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

/** Returns the verifier-only native keyring selector for the current platform. */
export function nativeKeyringVerificationArguments({
  environment = process.env,
  platform = process.platform,
} = {}) {
  const root = environment.ANCESTRYLLM_NATIVE_KEYRING_ROOT
  if (platform === 'darwin') {
    if (root !== undefined) throw new Error('Linux keyring verification root is Linux only')
    return [`--${MACOS_EPHEMERAL_VERIFICATION_SWITCH}`]
  }
  if (platform === 'win32') {
    if (root !== undefined) throw new Error('Linux keyring verification root is Linux only')
    return []
  }
  if (!root || !isAbsolute(root)) {
    throw new Error('Packaged Linux verification requires an absolute native keyring root')
  }
  return [`--${LINUX_KEYRING_VERIFICATION_SWITCH}=${root}`]
}

/** Returns the isolated, non-debugging argument contract for a normal package launch. */
export function normalLaunchArguments(root, {
  environment = process.env,
  platform = process.platform,
} = {}) {
  assert.ok(isAbsolute(root), 'Packaged normal-launch profile must be absolute')
  return [
    ...(platform === 'darwin' ? ['--use-mock-keychain'] : []),
    ...nativeKeyringVerificationArguments({ environment, platform }),
    `--user-data-dir=${root}`,
    `--disk-cache-dir=${join(root, 'chromium-cache')}`,
    `--crash-dumps-dir=${join(root, 'crash-dumps')}`,
  ]
}

function inheritedEnvironment(names, source) {
  return Object.fromEntries(names.flatMap((name) => {
    const value = source[name]
    return value === undefined ? [] : [[name, value]]
  }))
}

async function isolatedEnvironment(root, {
  environment = process.env,
  platform = process.platform,
} = {}) {
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
  const baseNames = ['LANG', 'LC_ALL', 'LC_CTYPE', 'PATH', 'TZ']
  const platformNames = platform === 'win32'
    ? ['COMSPEC', 'NUMBER_OF_PROCESSORS', 'PATHEXT', 'PROCESSOR_ARCHITECTURE', 'SYSTEMROOT', 'USERNAME', 'USERDOMAIN', 'WINDIR']
    : platform === 'darwin'
      ? ['HOME', 'LOGNAME', 'SECURITYSESSIONID', 'SHELL', 'USER', '__CF_USER_TEXT_ENCODING']
      : ['DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'LOGNAME', 'SHELL', 'USER', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR']
  const isolated = {
    ...inheritedEnvironment([...baseNames, ...platformNames], environment),
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
  if (platform !== 'darwin') isolated.HOME = isolatedHome
  if (environment.ANCESTRYLLM_PACKAGED_RUNTIME_PATH !== undefined) {
    isolated.PATH = environment.ANCESTRYLLM_PACKAGED_RUNTIME_PATH
  }
  return isolated
}

async function processSnapshot(platform = process.platform) {
  if (platform === 'win32') {
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
    const decoded = JSON.parse(stdout.trim())
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
  return stdout.split('\n').flatMap((line) => {
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

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds))
}

function waitForChildExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode })
  }
  return new Promise((resolveExit, reject) => {
    const timeout = setTimeout(() => {
      cleanup()
      reject(new Error(`Process ${String(child.pid)} did not exit within ${timeoutMs} ms`))
    }, timeoutMs)
    const onError = (error) => { cleanup(); reject(error) }
    const onExit = (code, signal) => { cleanup(); resolveExit({ code, signal }) }
    const cleanup = () => {
      clearTimeout(timeout)
      child.off('error', onError)
      child.off('exit', onExit)
    }
    child.once('error', onError)
    child.once('exit', onExit)
  })
}

async function forceCloseProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null || !child.pid) return
  if (process.platform === 'win32') {
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

async function requestNormalApplicationQuit(child, platform = process.platform) {
  if (!child.pid) throw new Error('Normal packaged launch PID is unavailable')
  if (platform === 'win32') {
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

/** Launches and verifies one selected packaged runtime without any automation transport. */
export async function verifyNormalLaunch({
  environment = process.env,
  executable = environment.ANCESTRYLLM_PACKAGED_EXECUTABLE,
  platform = process.platform,
  root = environment.ANCESTRYLLM_WDIO_USER_DATA,
} = {}) {
  assert.ok(executable, 'ANCESTRYLLM_PACKAGED_EXECUTABLE is required')
  assert.ok(isAbsolute(executable), 'Packaged executable must be absolute')
  assert.ok(root, 'ANCESTRYLLM_WDIO_USER_DATA is required')
  assert.ok(isAbsolute(root), 'Packaged normal-launch profile must be absolute')
  const launchArguments = normalLaunchArguments(root, { environment, platform })
  const child = spawn(executable, launchArguments, {
    env: await isolatedEnvironment(root, { environment, platform }),
    stdio: 'pipe',
    windowsHide: true,
  })
  let output = ''
  let spawnError
  const consume = (chunk) => {
    output = `${output}${chunk.toString('utf8')}`.slice(-32_768)
  }
  child.stdout.on('data', consume)
  child.stderr.on('data', consume)
  child.once('error', (error) => { spawnError = error })
  let exitedCleanly = false
  try {
    const rootPid = child.pid
    if (!rootPid) throw new Error('Normal packaged launch PID is unavailable')
    const deadline = Date.now() + 30_000
    /** @type {ProcessRecord[]} */
    let tree = []
    let windowReady = false
    const observedCommandLines = new Set()
    while (Date.now() < deadline) {
      if (spawnError) throw spawnError
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error(`Normal packaged launch exited before readiness.\n${output}`)
      }
      tree = descendantProcessTree(await processSnapshot(platform), rootPid)
      for (const record of tree) observedCommandLines.add(record.commandLine)
      windowReady = outputContainsWindowReadyRecord(output)
      if (
        windowReady
        && tree.some((record) => record.pid === rootPid)
        && tree.reduce((total, record) => total + record.rssBytes, 0) > 0
      ) break
      await delay(250)
    }
    assert.ok(tree.some((record) => record.pid === rootPid), 'Packaged root process was not observed')
    assert.ok(
      tree.reduce((total, record) => total + record.rssBytes, 0) > 0,
      'Packaged process tree reported no resident memory',
    )
    assert.equal(windowReady, true, `Packaged window did not become ready.\n${output}`)
    const remoteStem = ['remote', 'debugging'].join('-')
    const disallowedControlArgument = new RegExp(
      `(?:^|\\s)--(?:${remoteStem}(?:-address|-port|-pipe)?|inspect(?:-brk)?)(?:=|\\s|$)`,
      'u',
    )
    assert.doesNotMatch(launchArguments.join('\n'), disallowedControlArgument)
    assert.doesNotMatch([...observedCommandLines].join('\n'), disallowedControlArgument)
    assert.doesNotMatch(output, /DevTools listening on /u)
    await requestNormalApplicationQuit(child, platform)
    exitedCleanly = true
  } finally {
    if (!exitedCleanly) await forceCloseProcess(child)
  }
  return Object.freeze({
    event: 'ancestryllm.desktop.normal-launch-verified',
    version: 1,
  })
}

const entrypoint = process.argv[1]
  ? pathToFileURL(resolve(process.argv[1])).href
  : null
if (import.meta.url === entrypoint) {
  verifyNormalLaunch()
    .then((record) => { process.stdout.write(`${JSON.stringify(record)}\n`) })
    .catch((error) => {
      process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
      process.exitCode = 1
    })
}
