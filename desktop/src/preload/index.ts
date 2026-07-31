import { contextBridge, ipcRenderer } from 'electron'
import { desktopChannels, type AncestryBridge, type DesktopTheme } from '../shared-contract/desktop'
import { parseBridgeResult, parseTheme } from '../shared-contract/runtime'

const ancestry: AncestryBridge = Object.freeze({
  startup: async () => parseBridgeResult(await ipcRenderer.invoke(desktopChannels.startup)),
  setTheme: async (theme: DesktopTheme) => parseBridgeResult(await ipcRenderer.invoke(desktopChannels.setTheme, parseTheme(theme))),
})
contextBridge.exposeInMainWorld('ancestry', ancestry)
