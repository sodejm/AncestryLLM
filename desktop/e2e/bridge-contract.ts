/** Defines the explicit preload bridge allowlist shared by source and packaged tests. */
import type { AncestryBridge } from '../src/shared-contract/desktop'

const bridgeMethodSet = {
  deleteSecret: true,
  getAppInfo: true,
  getCapabilities: true,
  getPreferences: true,
  getSecretStatus: true,
  getSettings: true,
  getStartupDiagnostics: true,
  requestOpenFileGrant: true,
  requestSaveFileGrant: true,
  retrySidecar: true,
  revokeFileGrant: true,
  setSecret: true,
  updatePreferences: true,
  updateSettings: true,
} as const satisfies Readonly<Record<keyof AncestryBridge, true>>

export const bridgeMethods = Object.freeze(Object.keys(bridgeMethodSet).sort())
