/** Matches packaged Electron processes from platform-native process snapshots. */

/** One bounded native process-table record used by packaged verification. */
export type ProcessRecord = Readonly<{
  pid: number
  ppid: number
  rssBytes: number
  commandLine: string
  executablePath?: string
}>

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
  const quotedProfile = normalizedCommandValue(
    `--user-data-dir="${userDataDirectory}"`,
    platform,
  )
  const nativeExecutable = normalizedCommandValue(record.executablePath ?? '', platform)
  if (commandLine.includes('--type=')
    || (!commandLine.includes(expectedExecutable) && nativeExecutable !== expectedExecutable)) return false
  if (commandLine.includes(expectedProfile) || commandLine.includes(quotedProfile)) return true
  if (platform !== 'win32' || /--user-data-dir=/u.test(commandLine)) return false
  return nativeExecutable === expectedExecutable
}

/** Finds a native renderer process in the packaged application's descendant tree. */
export function observedRenderer(
  records: readonly ProcessRecord[],
  rootPid: number,
  platform: NodeJS.Platform = process.platform,
): ProcessRecord | null {
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
  const explicit = children.find((record) => /(?:^|\s)--type=renderer(?:\s|$)/u.test(record.commandLine))
  if (explicit) return explicit
  // Chromium can leave Linux forked renderer argv as --type=zygote. With the
  // renderer DOM already visible through WebDriver, select the largest leaf
  // zygote in this app's process tree, excluding small idle zygote parents.
  if (platform !== 'linux') return null
  return children
    .filter((record) => /(?:^|\s)--type=zygote(?:\s|$)/u.test(record.commandLine)
      && record.rssBytes >= 64 * 1024 * 1024
      && !children.some((child) => child.ppid === record.pid))
    .sort((left, right) => right.rssBytes - left.rssBytes)[0] ?? null
}
