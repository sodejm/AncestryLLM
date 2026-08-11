/** Supplies deterministic native file selections only for packaged grant verification. */
import { isAbsolute } from 'node:path'
import type { NativeFileDialogPort } from '../src/main/file-grant-broker'

const verificationMarker = 'ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION'
const openPathVariable = 'ANCESTRYLLM_FILE_GRANT_OPEN_PATH'
const savePathVariable = 'ANCESTRYLLM_FILE_GRANT_SAVE_PATH'

function selectedPath(variable: string): string {
  if (process.env[verificationMarker] !== '1') {
    throw new Error('Packaged file-grant verification adapter is disabled.')
  }
  const value = process.env[variable]
  if (typeof value !== 'string' || value.length === 0 || !isAbsolute(value)) {
    throw new Error(`Packaged file-grant verification path is invalid: ${variable}`)
  }
  return value
}

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
