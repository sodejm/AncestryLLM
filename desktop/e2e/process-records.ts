/** Matches packaged Electron processes from platform-native process snapshots. */

/** One bounded native process-table record used by packaged verification. */
export type ProcessRecord = Readonly<{
  pid: number
  ppid: number
  rssBytes: number
  commandLine: string
  executablePath?: string
}>

/** Parses live POSIX processes; a zombie has exited but may await parent reaping. */
export function parsePosixProcessSnapshot(output: string): ProcessRecord[] {
  return output.split('\n').flatMap((line): ProcessRecord[] => {
    const match = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(.*)$/u)
    if (!match?.[1] || !match[2] || !match[3] || !match[4] || match[5] === undefined
      || match[4].startsWith('Z')) return []
    return [{
      pid: Number.parseInt(match[1], 10),
      ppid: Number.parseInt(match[2], 10),
      rssBytes: Number.parseInt(match[3], 10) * 1024,
      commandLine: match[5],
    }]
  })
}

const LAST_WINDOW_CLOSE_SESSION_ERROR =
  'All window handles were removed, causing WebdriverIO to close the session.'

function normalizedCommandValue(value: string, platform: NodeJS.Platform): string {
  return platform === 'win32' ? value.replaceAll('\\', '/').toLowerCase() : value
}

function escapedRegularExpression(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&')
}

function hasExactArgument(commandLine: string, expected: string): boolean {
  return new RegExp(
    `(?:^|\\s|")${escapedRegularExpression(expected)}(?=$|\\s|")`,
    'u',
  ).test(commandLine)
}

function hasExactUserDataDirectory(commandLine: string, expected: string): boolean {
  const directory = escapedRegularExpression(expected)
  return new RegExp(
    `(?:^|\\s|")--user-data-dir=(?:"${directory}"|${directory})(?=$|\\s|")`,
    'u',
  ).test(commandLine)
}

/** Code-only outcome from a packaged Electron main-process lookup. */
export type PackagedMainProcessSearch = Readonly<{
  /** Matching process identifier, or -1 while no eligible match exists. */
  pid: number
  /** Non-sensitive reason that the lookup matched or remained pending. */
  status: 'matched' | 'executable_not_observed' | 'renderer_only' | 'profile_not_observed' | 'excluded'
}>

/** Identifies the isolated packaged Electron main process across supported platforms. */
export function matchesPackagedMainProcess(
  record: ProcessRecord,
  executable: string,
  userDataDirectory: string,
  platform: NodeJS.Platform = process.platform,
): boolean {
  const commandLine = normalizedCommandValue(record.commandLine, platform)
  const expectedExecutable = normalizedCommandValue(executable, platform)
  const expectedProfile = normalizedCommandValue(userDataDirectory, platform)
  const nativeExecutable = normalizedCommandValue(record.executablePath ?? '', platform)
  if (commandLine.includes('--type=')) return false
  const matchesExecutable = hasExactArgument(commandLine, expectedExecutable)
    || nativeExecutable === expectedExecutable
  if (!matchesExecutable) return false
  if (hasExactUserDataDirectory(commandLine, expectedProfile)) return true
  if (platform !== 'win32' || /--user-data-dir=/u.test(commandLine)) return false
  return nativeExecutable === expectedExecutable
}

/** Finds native renderer processes in the packaged application's descendant tree. */
export function observedRenderers(
  records: readonly ProcessRecord[],
  rootPid: number,
  platform: NodeJS.Platform = process.platform,
): readonly ProcessRecord[] {
  const descendants = new Set([rootPid])
  let changed = true
  while (changed) {
    changed = false
    for (const record of records) {
      if (descendants.has(record.pid) || !descendants.has(record.ppid)) continue
      descendants.add(record.pid)
      changed = true
    }
  }
  const children = records.filter((record) => record.pid !== rootPid && descendants.has(record.pid))
  const explicit = children.filter((record) => /(?:^|\s)--type=renderer(?:\s|$)/u.test(record.commandLine))
  if (explicit.length > 0) return explicit
  // Chromium can leave Linux forked renderer argv as --type=zygote. With the
  // renderer DOM already visible through WebDriver, select the largest leaf
  // zygote in this app's process tree, excluding small idle zygote parents.
  if (platform !== 'linux') return []
  const fallback = children
    .filter((record) => /(?:^|\s)--type=zygote(?:\s|$)/u.test(record.commandLine)
      && record.rssBytes >= 64 * 1024 * 1024
      && !children.some((child) => child.ppid === record.pid))
    .sort((left, right) => right.rssBytes - left.rssBytes)[0] ?? null
  return fallback ? [fallback] : []
}

/** Returns one representative renderer for process-observation assertions. */
export function observedRenderer(
  records: readonly ProcessRecord[],
  rootPid: number,
  platform: NodeJS.Platform = process.platform,
): ProcessRecord | null {
  return observedRenderers(records, rootPid, platform)[0] ?? null
}

/** Finds an eligible packaged main process without returning paths or command lines. */
export function findPackagedMainProcess(
  records: readonly ProcessRecord[],
  executable: string,
  userDataDirectory: string,
  excluded: ReadonlySet<number> = new Set(),
  platform: NodeJS.Platform = process.platform,
): PackagedMainProcessSearch {
  const expectedExecutable = normalizedCommandValue(executable, platform)
  const expectedProfile = normalizedCommandValue(userDataDirectory, platform)
  let sawExecutable = false
  let sawMainProcess = false
  let sawProfile = false
  for (const record of records) {
    const commandLine = normalizedCommandValue(record.commandLine, platform)
    if (!hasExactArgument(commandLine, expectedExecutable)) continue
    sawExecutable = true
    if (commandLine.includes('--type=')) continue
    sawMainProcess = true
    if (!hasExactUserDataDirectory(commandLine, expectedProfile)) continue
    sawProfile = true
    if (excluded.has(record.pid)) continue
    return { pid: record.pid, status: 'matched' }
  }
  if (sawProfile) return { pid: -1, status: 'excluded' }
  if (sawMainProcess) return { pid: -1, status: 'profile_not_observed' }
  if (sawExecutable) return { pid: -1, status: 'renderer_only' }
  return { pid: -1, status: 'executable_not_observed' }
}

/** Selects native process records whose OS identifiers were reported by CDP. */
export function recordsForProcessIds(
  records: readonly ProcessRecord[],
  processIds: ReadonlySet<number>,
): ProcessRecord[] {
  return records.filter((record) => processIds.has(record.pid))
}

/** Recognizes WebdriverIO's expected exception after closing the final window. */
export function isExpectedLastWindowCloseError(error: unknown): boolean {
  return error instanceof Error && error.message === LAST_WINDOW_CLOSE_SESSION_ERROR
}
