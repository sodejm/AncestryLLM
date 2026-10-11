/** Matches packaged Electron processes from platform-native process snapshots. */

/** One bounded native process-table record used by packaged verification. */
export type ProcessRecord = Readonly<{
  pid: number
  ppid: number
  rssBytes: number
  commandLine: string
}>

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
  return !commandLine.includes('--type=')
    && hasExactArgument(commandLine, expectedExecutable)
    && hasExactUserDataDirectory(commandLine, expectedProfile)
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
