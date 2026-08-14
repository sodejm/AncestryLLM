/** Supplies deterministic native file selections only for packaged grant verification. */
import { posix, win32 } from 'node:path'
import type { NativeFileDialogPort } from '../src/main/file-grant-broker'

const verificationMarker = 'ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION'
const openPathVariable = 'ANCESTRYLLM_FILE_GRANT_OPEN_PATH'
const savePathVariable = 'ANCESTRYLLM_FILE_GRANT_SAVE_PATH'

/** Normalizes an absolute platform path, returning `null` for unsafe or unsupported input. */
export function normalizeVerificationSelection(
  value: string,
  platform: NodeJS.Platform = process.platform,
): string | null {
  const pathApi = platform === 'win32'
    ? win32
    : platform === 'darwin' || platform === 'linux'
      ? posix
      : null
  if (pathApi === null || value.length === 0 || value.includes('\0')) return null

  const candidate = platform === 'win32' ? value.replaceAll('/', '\\') : value
  if (!pathApi.isAbsolute(candidate) || pathApi.normalize(candidate) !== candidate) return null
  return candidate
}

function selectedPath(variable: string): string {
  if (process.env[verificationMarker] !== '1') {
    throw new Error('Packaged file-grant verification adapter is disabled.')
  }
  const value = process.env[variable]
  const selection = typeof value === 'string' ? normalizeVerificationSelection(value) : null
  if (selection === null) {
    throw new Error(`Packaged file-grant verification path is invalid: ${variable}`)
  }
  return selection
}

/**
 * Creates the opt-in packaged-test dialog adapter backed by explicit environment selections.
 * Access fails while the verification marker is disabled or a selected path is invalid.
 */
export function createNativeFileDialogPort(): NativeFileDialogPort {
  return Object.freeze({
    async selectOpenFile() {
      return selectedPath(openPathVariable)
    },
    async selectSaveFile() {
      return selectedPath(savePathVariable)
    },
    async confirmReplacement() {
      return true
    },
  })
}
