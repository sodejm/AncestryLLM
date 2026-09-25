/** Verifies packaged Electron main-process records match across supported platforms. */

import { describe, expect, it } from 'vitest'
import { matchesPackagedMainProcess, observedRenderer } from './process-records'

describe('packaged process records', () => {
  const windowsExecutable = String.raw`C:\a\AncestryLLM\dist\win-unpacked\AncestryLLM.exe`
  const windowsProfile = String.raw`C:\a\_temp\ancestryllm-profile`

  it('matches Windows executable and profile paths without depending on slash style or case', () => {
    expect(matchesPackagedMainProcess({
      pid: 42,
      ppid: 1,
      rssBytes: 1024,
      commandLine: '"c:/A/ANCESTRYLLM/dist/win-unpacked/ancestryllm.exe" --user-data-dir=c:/A/_TEMP/AncestryLLM-Profile',
    }, windowsExecutable, windowsProfile, 'win32')).toBe(true)
  })

  it('matches a Windows profile argument whose value is quoted by the launcher', () => {
    expect(matchesPackagedMainProcess({
      pid: 46,
      ppid: 1,
      rssBytes: 1024,
      commandLine: `"${windowsExecutable}" --user-data-dir="${windowsProfile}"`,
    }, windowsExecutable, windowsProfile, 'win32')).toBe(true)
  })

  it('identifies a Windows main process from its native executable path when the launcher omits the profile argument', () => {
    const record = {
      pid: 47,
      ppid: 1,
      rssBytes: 1024,
      executablePath: windowsExecutable,
      commandLine: `"${windowsExecutable}" --inspect=0`,
    }
    expect(matchesPackagedMainProcess(record, windowsExecutable, windowsProfile, 'win32')).toBe(true)
    expect(matchesPackagedMainProcess({ ...record, commandLine: '' }, windowsExecutable, windowsProfile, 'win32')).toBe(true)
    expect(matchesPackagedMainProcess({ ...record, executablePath: String.raw`C:\other.exe` }, windowsExecutable, windowsProfile, 'win32')).toBe(false)
    expect(matchesPackagedMainProcess({ ...record, commandLine: `${record.commandLine} --type=renderer` }, windowsExecutable, windowsProfile, 'win32')).toBe(false)
    expect(matchesPackagedMainProcess({ ...record, commandLine: `${record.commandLine} --user-data-dir=C:/other` }, windowsExecutable, windowsProfile, 'win32')).toBe(false)
  })

  it('rejects Windows renderer processes and unrelated profiles', () => {
    const baseCommand = `"${windowsExecutable}" --user-data-dir=${windowsProfile}`

    expect(matchesPackagedMainProcess({
      pid: 43,
      ppid: 42,
      rssBytes: 1024,
      commandLine: `${baseCommand} --type=renderer`,
    }, windowsExecutable, windowsProfile, 'win32')).toBe(false)
    expect(matchesPackagedMainProcess({
      pid: 44,
      ppid: 1,
      rssBytes: 1024,
      commandLine: `"${windowsExecutable}" --user-data-dir=C:\\a\\_temp\\another-profile`,
    }, windowsExecutable, windowsProfile, 'win32')).toBe(false)
  })

  it('preserves case-sensitive matching on POSIX platforms', () => {
    expect(matchesPackagedMainProcess({
      pid: 45,
      ppid: 1,
      rssBytes: 1024,
      commandLine: '/opt/AncestryLLM --user-data-dir=/tmp/ancestryllm-profile',
    }, '/opt/ancestryllm', '/tmp/ancestryllm-profile', 'linux')).toBe(false)
  })

  it('identifies an explicit renderer only inside the observed process tree', () => {
    const records = [
      { pid: 10, ppid: 1, rssBytes: 1, commandLine: '/app' },
      { pid: 11, ppid: 10, rssBytes: 1, commandLine: '/app --type=zygote' },
      { pid: 12, ppid: 11, rssBytes: 1, commandLine: '/app --type=renderer' },
    ]
    expect(observedRenderer(records, 10, 'darwin')).toEqual(records[2])
    expect(observedRenderer([{ ...records[0]!, commandLine: '/app --type=renderer' }], 10, 'darwin')).toBeNull()
    expect(observedRenderer([{ ...records[2]!, ppid: 99 }, records[0]!], 10, 'darwin')).toBeNull()
  })

  it('identifies a Linux renderer from native nested zygote descendants when its process title is inherited', () => {
    const records = [
      { pid: 10, ppid: 1, rssBytes: 40, commandLine: '/app' },
      { pid: 11, ppid: 10, rssBytes: 30, commandLine: '/app --type=zygote' },
      { pid: 12, ppid: 11, rssBytes: 10, commandLine: '/app --type=zygote' },
      { pid: 13, ppid: 12, rssBytes: 140 * 1024 * 1024, commandLine: '/app --type=zygote' },
      { pid: 14, ppid: 10, rssBytes: 200, commandLine: '/app --type=utility' },
      { pid: 15, ppid: 1, rssBytes: 300, commandLine: '/other --type=renderer' },
    ]
    expect(observedRenderer(records, 10, 'linux')).toEqual(records[3])
    expect(observedRenderer(records, 10, 'darwin')).toBeNull()
  })
})
