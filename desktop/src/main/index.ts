import { join } from 'node:path'
import { app, BrowserWindow, ipcMain, session, shell, type IpcMainInvokeEvent } from 'electron'
import { createMockAncestryBridge } from '../mock-bridge/desktop'
import { desktopChannels } from '../shared-contract/desktop'
import { parseTheme } from '../shared-contract/runtime'
import { isTrustedRendererUrl, resolveRendererTarget } from './renderer-location'

app.enableSandbox()
const bridge = createMockAncestryBridge(process.env.ANCESTRYLLM_DESKTOP_FIXTURE === 'failure' ? 'failure' : 'success')
const rendererPath = join(__dirname, '../renderer/index.html')

function rendererPolicy() {
  return {
    developmentUrl: process.env.ELECTRON_RENDERER_URL,
    isPackaged: app.isPackaged,
    rendererPath,
  }
}

function trustedSender(event: IpcMainInvokeEvent): boolean {
  if (!event.senderFrame || event.senderFrame !== event.sender.mainFrame) return false
  return isTrustedRendererUrl({ ...rendererPolicy(), senderUrl: event.senderFrame.url })
}

function createWindow(): void {
  const window = new BrowserWindow({
    width: 1080, minWidth: 720, height: 720, minHeight: 560, show: false,
    webPreferences: { preload: join(__dirname, '../preload/index.mjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true, webviewTag: false },
  })
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event) => event.preventDefault())
  window.webContents.on('will-attach-webview', (event) => event.preventDefault())
  window.once('ready-to-show', () => window.show())
  const rendererTarget = resolveRendererTarget(rendererPolicy())
  if (rendererTarget.kind === 'url') void window.loadURL(rendererTarget.value)
  else void window.loadFile(rendererTarget.value)
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  session.defaultSession.on('will-download', (event) => event.preventDefault())
  shell.openExternal = async () => { throw new Error('External navigation is disabled') }
  ipcMain.handle(desktopChannels.startup, (event) => trustedSender(event) ? bridge.startup() : Promise.reject(new Error('Untrusted IPC sender')))
  ipcMain.handle(desktopChannels.setTheme, (event, theme: unknown) => trustedSender(event) ? bridge.setTheme(parseTheme(theme)) : Promise.reject(new Error('Untrusted IPC sender')))
  createWindow()
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
})
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
