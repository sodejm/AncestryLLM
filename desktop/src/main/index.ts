import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import {
  app,
  BrowserWindow,
  ipcMain,
  protocol,
  session,
  type IpcMainInvokeEvent,
  type WebContents,
} from 'electron'
import { createMockAncestryBridge } from '../mock-bridge/desktop'
import { desktopChannels } from '../shared-contract/desktop'
import { parseTheme } from '../shared-contract/runtime'
import { isTrustedRendererUrl, resolveRendererTarget } from './renderer-location'
import {
  APP_ENTRY_URL,
  APP_SCHEME_PRIVILEGES,
  assertSecureWebPreferences,
  createAppProtocolHandler,
  createRuntimeSecurityState,
  secureWebPreferences,
} from './security-policy'
import { installSessionPolicy } from './session-policy'

app.enableSandbox()
protocol.registerSchemesAsPrivileged([{ scheme: 'app', privileges: APP_SCHEME_PRIVILEGES }])

const bridge = createMockAncestryBridge(process.env.ANCESTRYLLM_DESKTOP_FIXTURE === 'failure' ? 'failure' : 'success')
const rendererRoot = join(__dirname, '../renderer')
const rendererPath = join(rendererRoot, 'index.html')
const preloadPath = join(__dirname, '../preload/index.cjs')

function rendererPolicy() {
  return {
    developmentUrl: process.env.ELECTRON_RENDERER_URL,
    isPackaged: app.isPackaged,
    rendererPath,
  }
}

function isProductionRenderer(): boolean {
  return resolveRendererTarget(rendererPolicy()).value === APP_ENTRY_URL
}

function trustedSender(event: IpcMainInvokeEvent): boolean {
  if (!event.senderFrame || event.senderFrame !== event.sender.mainFrame) return false
  return isTrustedRendererUrl({ ...rendererPolicy(), senderUrl: event.senderFrame.url })
}

function denyWebContentsCapabilities(contents: WebContents): void {
  contents.setWindowOpenHandler(() => ({ action: 'deny' }))
  contents.on('will-navigate', (event) => event.preventDefault())
  contents.on('will-redirect', (event) => event.preventDefault())
  contents.on('will-attach-webview', (event) => event.preventDefault())
  contents.on('devtools-opened', () => {
    if (isProductionRenderer()) contents.closeDevTools()
  })
}

const configuredPreferences = new WeakMap<WebContents, ReturnType<typeof secureWebPreferences>>()
let pendingWindowPreferences: ReturnType<typeof secureWebPreferences> | null = null

app.on('web-contents-created', (_event, contents) => {
  const preferences = pendingWindowPreferences
  if (contents.getType() === 'window' && !preferences) {
    contents.close({ waitForBeforeUnload: false })
    app.exit(1)
    return
  }
  if (!preferences) {
    if (isProductionRenderer()) contents.close({ waitForBeforeUnload: false })
    return
  }
  assertSecureWebPreferences(preferences, isProductionRenderer())
  configuredPreferences.set(contents, preferences)
  denyWebContentsCapabilities(contents)
})

function runtimeSecurityState(window: BrowserWindow) {
  const preferences = configuredPreferences.get(window.webContents)
  if (!preferences) throw new Error('Unregistered BrowserWindow')
  return createRuntimeSecurityState(window.webContents.getURL(), preferences, isProductionRenderer())
}

function registerIpcHandlers(): void {
  const rejectUntrusted = () => Promise.reject(new Error('Untrusted IPC sender'))
  ipcMain.handle(desktopChannels.startup, (event) => trustedSender(event) ? bridge.startup() : rejectUntrusted())
  ipcMain.handle(desktopChannels.setTheme, (event, theme: unknown) =>
    trustedSender(event) ? bridge.setTheme(parseTheme(theme)) : rejectUntrusted())
}

function createWindow(): void {
  const production = isProductionRenderer()
  const preferences = secureWebPreferences(preloadPath, production)
  assertSecureWebPreferences(preferences, production)
  pendingWindowPreferences = preferences
  let window: BrowserWindow
  try {
    window = new BrowserWindow({
      width: 1080,
      minWidth: 720,
      height: 720,
      minHeight: 560,
      show: false,
      webPreferences: preferences,
    })
  } finally {
    pendingWindowPreferences = null
  }
  window.once('ready-to-show', () => window.show())
  void window.loadURL(resolveRendererTarget(rendererPolicy()).value)
}

app.whenReady().then(async () => {
  await protocol.handle('app', createAppProtocolHandler(async (file) => readFile(join(rendererRoot, file))))
  installSessionPolicy(session.defaultSession as unknown as Parameters<typeof installSessionPolicy>[0])
  registerIpcHandlers()
  createWindow()
  if (process.env.ANCESTRYLLM_DESKTOP_SECURITY_E2E === '1') {
    Object.defineProperty(globalThis, '__ancestryllmSecurityStateForTests', {
      configurable: false,
      value: () => {
        const window = BrowserWindow.getAllWindows()[0]
        if (!window) throw new Error('No BrowserWindow')
        return runtimeSecurityState(window)
      },
      writable: false,
    })
  }
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
})

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
