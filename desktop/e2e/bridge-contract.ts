/** Defines the explicit preload bridge allowlist shared by source and packaged tests. */
import type { AncestryBridge } from '../src/shared-contract/desktop'

const bridgeMethodSet = {
  createConsent: true,
  createProviderProfile: true,
  cancelJob: true,
  deleteSecret: true,
  getAppInfo: true,
  getCapabilities: true,
  getLocalRuntimeStatus: true,
  getJob: true,
  getPreferences: true,
  getProviderConfiguration: true,
  getSecretStatus: true,
  getSettings: true,
  getStartupDiagnostics: true,
  listJobs: true,
  onJobEvent: true,
  previewConsent: true,
  previewLocalRuntime: true,
  requestOpenFileGrant: true,
  requestSaveFileGrant: true,
  retrySidecar: true,
  revokeConsent: true,
  revokeFileGrant: true,
  setSecret: true,
  subscribeJobEvents: true,
  unsubscribeJobEvents: true,
  updatePreferences: true,
  updateSettings: true,
  validateProviderEndpoint: true,
  applyLocalRuntime: true,
} as const satisfies Readonly<Record<keyof AncestryBridge, true>>

export const bridgeMethods = Object.freeze(Object.keys(bridgeMethodSet).sort())
