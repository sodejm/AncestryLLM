import { contextBridge, ipcRenderer } from 'electron'
import {
  desktopChannels,
  type AncestryBridge,
  type FileGrantId,
  type OpenFileGrantRequest,
  type PreferenceUpdate,
  type SaveFileGrantRequest,
} from '../shared-contract/desktop'
import {
  parseAppInfoResult,
  parseCapabilitiesResult,
  parseFileGrantId,
  parseFileGrantResult,
  parseFileGrantRevocationResult,
  parseOpenFileGrantRequest,
  parsePreferenceUpdate,
  parsePreferencesResult,
  parseSaveFileGrantRequest,
  parseStartupDiagnosticsResult,
} from '../shared-contract/runtime'

const ancestry: AncestryBridge = Object.freeze({
  getAppInfo: async () => parseAppInfoResult(await ipcRenderer.invoke(desktopChannels.getAppInfo)),
  getStartupDiagnostics: async () => parseStartupDiagnosticsResult(await ipcRenderer.invoke(desktopChannels.getStartupDiagnostics)),
  getCapabilities: async () => parseCapabilitiesResult(await ipcRenderer.invoke(desktopChannels.getCapabilities)),
  retrySidecar: async () => parseStartupDiagnosticsResult(await ipcRenderer.invoke(desktopChannels.retrySidecar)),
  getPreferences: async () => parsePreferencesResult(await ipcRenderer.invoke(desktopChannels.getPreferences)),
  updatePreferences: async (update: PreferenceUpdate) => parsePreferencesResult(
    await ipcRenderer.invoke(desktopChannels.updatePreferences, parsePreferenceUpdate(update)),
  ),
  requestOpenFileGrant: async (request: OpenFileGrantRequest) => parseFileGrantResult(
    await ipcRenderer.invoke(desktopChannels.requestOpenFileGrant, parseOpenFileGrantRequest(request)),
  ),
  requestSaveFileGrant: async (request: SaveFileGrantRequest) => parseFileGrantResult(
    await ipcRenderer.invoke(desktopChannels.requestSaveFileGrant, parseSaveFileGrantRequest(request)),
  ),
  revokeFileGrant: async (grantId: FileGrantId) => parseFileGrantRevocationResult(
    await ipcRenderer.invoke(desktopChannels.revokeFileGrant, parseFileGrantId(grantId)),
  ),
})

contextBridge.exposeInMainWorld('ancestry', ancestry)
