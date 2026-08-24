import { describe, expect, it } from 'vitest'
import { matchesPackagedMainProcess } from './process-records'

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
})
