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

/** Identifies the isolated packaged Electron main process across supported platforms. */
export function matchesPackagedMainProcess(
  record: ProcessRecord,
  executable: string,
  userDataDirectory: string,
  platform: NodeJS.Platform = process.platform,
): boolean {
  const commandLine = normalizedCommandValue(record.commandLine, platform)
  const expectedExecutable = normalizedCommandValue(executable, platform)
  const expectedProfile = normalizedCommandValue(
    `--user-data-dir=${userDataDirectory}`,
    platform,
  )
  return !commandLine.includes('--type=')
    && commandLine.includes(expectedExecutable)
    && commandLine.includes(expectedProfile)
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
