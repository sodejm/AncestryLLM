/** Verifies packaged Electron main-process records match across supported platforms. */

import { describe, expect, it } from 'vitest'
import {
  findPackagedMainProcess,
  isExpectedLastWindowCloseError,
  matchesPackagedMainProcess,
  recordsForProcessIds,
} from './process-records'

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

  it('matches a Windows profile switch whose value is quoted after the equals sign', () => {
    expect(matchesPackagedMainProcess({
      pid: 43,
      ppid: 1,
      rssBytes: 1024,
      commandLine: `"${windowsExecutable}" --user-data-dir="${windowsProfile}"`,
    }, windowsExecutable, windowsProfile, 'win32')).toBe(true)
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

  it('requires exact Windows executable and profile argument boundaries', () => {
    expect(matchesPackagedMainProcess({
      pid: 45,
      ppid: 1,
      rssBytes: 1024,
      commandLine: `"${windowsExecutable}.backup" --user-data-dir=${windowsProfile}`,
    }, windowsExecutable, windowsProfile, 'win32')).toBe(false)
    expect(matchesPackagedMainProcess({
      pid: 46,
      ppid: 1,
      rssBytes: 1024,
      commandLine: `"${windowsExecutable}" --user-data-dir=${windowsProfile}-copy`,
    }, windowsExecutable, windowsProfile, 'win32')).toBe(false)
  })

  it('returns bounded lookup diagnostics without exposing command lines', () => {
    const profileMismatch = findPackagedMainProcess([{
      pid: 47,
      ppid: 1,
      rssBytes: 1024,
      commandLine: `"${windowsExecutable}" --user-data-dir=C:\\a\\_temp\\another-profile`,
    }], windowsExecutable, windowsProfile, new Set(), 'win32')
    const excluded = findPackagedMainProcess([{
      pid: 48,
      ppid: 1,
      rssBytes: 1024,
      commandLine: `"${windowsExecutable}" --user-data-dir="${windowsProfile}"`,
    }], windowsExecutable, windowsProfile, new Set([48]), 'win32')

    expect(profileMismatch).toEqual({ pid: -1, status: 'profile_not_observed' })
    expect(excluded).toEqual({ pid: -1, status: 'excluded' })
    expect(JSON.stringify([profileMismatch, excluded])).not.toContain('C:')
  })

  it('preserves case-sensitive matching on POSIX platforms', () => {
    expect(matchesPackagedMainProcess({
      pid: 45,
      ppid: 1,
      rssBytes: 1024,
      commandLine: '/opt/AncestryLLM --user-data-dir=/tmp/ancestryllm-profile',
    }, '/opt/ancestryllm', '/tmp/ancestryllm-profile', 'linux')).toBe(false)
  })

  it('selects CDP renderer PIDs without depending on native command-line labels', () => {
    const records = [
      { pid: 45, ppid: 1, rssBytes: 1024, commandLine: '/opt/app --type=zygote' },
      { pid: 46, ppid: 45, rssBytes: 2048, commandLine: '/opt/app --type=zygote' },
      { pid: 99, ppid: 1, rssBytes: 512, commandLine: '/opt/unrelated --type=renderer' },
    ]

    expect(recordsForProcessIds(records, new Set([46, 99]))).toEqual([records[1], records[2]])
  })

  it('recognizes only the exact final-window session-close exception', () => {
    expect(isExpectedLastWindowCloseError(new Error(
      'All window handles were removed, causing WebdriverIO to close the session.',
    ))).toBe(true)
    expect(isExpectedLastWindowCloseError(new Error('window close failed'))).toBe(false)
    expect(isExpectedLastWindowCloseError(
      'All window handles were removed, causing WebdriverIO to close the session.',
    )).toBe(false)
  })
})
