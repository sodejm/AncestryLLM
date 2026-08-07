/** Exposes the validated desktop bridge into the renderer through Electron's isolated preload boundary. */
import { contextBridge, ipcRenderer } from 'electron'
import { desktopChannels, type AncestryBridge, type PreferenceUpdate } from '../shared-contract/desktop'
import {
  parseAppInfoResult,
  parseCapabilitiesResult,
  parsePreferenceUpdate,
  parsePreferencesResult,
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
})

contextBridge.exposeInMainWorld('ancestry', ancestry)
