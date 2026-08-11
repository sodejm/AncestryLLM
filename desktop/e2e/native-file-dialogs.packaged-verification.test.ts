// Verifies packaged file-dialog selections are canonicalized narrowly and fail closed.
import { describe, expect, it } from 'vitest'
import { normalizeVerificationSelection } from './native-file-dialogs.packaged-verification'

describe('packaged file-grant verification selections', () => {
  it('canonicalizes only mixed Windows separators before broker validation', () => {
    const selected = String.raw`C:\a\AncestryLLM\AncestryLLM/desktop/verification/windows-11-arm/input.ged`

    expect(normalizeVerificationSelection(selected, 'win32')).toBe(
      String.raw`C:\a\AncestryLLM\AncestryLLM\desktop\verification\windows-11-arm\input.ged`,
    )
  })

  it('rejects relative, traversing, redundant, and null-containing selections', () => {
    expect(normalizeVerificationSelection('desktop/input.ged', 'win32')).toBeNull()
    expect(normalizeVerificationSelection(String.raw`C:\a\workspace\..\secret.ged`, 'win32')).toBeNull()
    expect(normalizeVerificationSelection(String.raw`C:\a\\input.ged`, 'win32')).toBeNull()
    expect(normalizeVerificationSelection('C:\\a\\input.ged\0', 'win32')).toBeNull()
  })

  it('preserves normalized absolute POSIX selections and rejects unknown platforms', () => {
    expect(normalizeVerificationSelection('/workspace/desktop/input.ged', 'linux')).toBe(
      '/workspace/desktop/input.ged',
    )
    expect(normalizeVerificationSelection('/workspace/../secret.ged', 'darwin')).toBeNull()
    expect(normalizeVerificationSelection('/workspace/input.ged', 'aix')).toBeNull()
  })
})
