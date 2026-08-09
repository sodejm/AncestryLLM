/** Boots the Electron main process, security policies, runtime bridge, IPC, and window lifecycle. */
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
import type { AncestryBridge } from '../shared-contract/desktop'
import { registerDesktopIpcHandlers } from './ipc-handlers'
import { isTrustedRendererUrl, resolveRendererTarget } from './renderer-location'
import {
  APP_ENTRY_URL,
  APP_SCHEME_PRIVILEGES,
  assertSecureWebPreferences,
  createAppProtocolHandler,
  secureWebPreferences,
} from './security-policy'
import { installSessionPolicy } from './session-policy'
import { startRuntimeBridge } from './runtime-bridge'
import type { SidecarSupervisor } from './sidecar-supervisor'
import { installSingleInstanceGuard } from './single-instance'
import { WINDOW_READY_RECORD } from './window-readiness'
import { installKeyboardZoom, type KeyboardZoomTarget } from './zoom-policy'

app.enableSandbox()
const primaryInstance = installSingleInstanceGuard({
  requestLock: () => app.requestSingleInstanceLock(),
  quit: () => app.quit(),
  onSecondInstance: (listener) => { app.on('second-instance', listener) },
  primaryWindow: () => BrowserWindow.getAllWindows()[0],
})
if (primaryInstance) {
  protocol.registerSchemesAsPrivileged([{ scheme: 'app', privileges: APP_SCHEME_PRIVILEGES }])
}

let bridge: AncestryBridge | undefined
const rendererRoot = join(__dirname, '../renderer')
const rendererPath = join(rendererRoot, 'index.html')
const preloadPath = join(__dirname, '../preload/index.cjs')
let sidecarSupervisor: SidecarSupervisor | undefined
let shutdownAuthorized = false
let shutdownPromise: Promise<void> | undefined

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
  installKeyboardZoom(contents as unknown as KeyboardZoomTarget)
  contents.setWindowOpenHandler(() => ({ action: 'deny' }))
  contents.on('will-navigate', (event) => event.preventDefault())
  contents.on('will-redirect', (event) => event.preventDefault())
  contents.on('will-attach-webview', (event) => event.preventDefault())
  contents.on('devtools-opened', () => {
    if (isProductionRenderer()) contents.closeDevTools()
  })
}

let pendingWindowPreferences: ReturnType<typeof secureWebPreferences> | null = null

if (primaryInstance) {
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
    denyWebContentsCapabilities(contents)
  })
}

function registerIpcHandlers(): void {
  if (!bridge) throw new Error('Runtime bridge is unavailable.')
  registerDesktopIpcHandlers(
    ipcMain,
    bridge,
    (event) => trustedSender(event as IpcMainInvokeEvent),
  )
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
  window.once('ready-to-show', () => {
    window.show()
    console.info(WINDOW_READY_RECORD)
  })
  void window.loadURL(resolveRendererTarget(rendererPolicy()).value)
}

if (primaryInstance) {
  app.whenReady().then(async () => {
    const runtime = await startRuntimeBridge()
    bridge = runtime.bridge
    sidecarSupervisor = runtime.supervisor
    await protocol.handle('app', createAppProtocolHandler(async (file) => readFile(join(rendererRoot, file))))
    installSessionPolicy(session.defaultSession as unknown as Parameters<typeof installSessionPolicy>[0])
    registerIpcHandlers()
    createWindow()
    app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
  })
  app.on('before-quit', (event) => {
    if (shutdownAuthorized || (!sidecarSupervisor && !shutdownPromise)) return
    event.preventDefault()
    if (!shutdownPromise && sidecarSupervisor) {
      const supervisor = sidecarSupervisor
      sidecarSupervisor = undefined
      shutdownPromise = supervisor.stop().finally(() => {
        shutdownAuthorized = true
        app.quit()
      })
    }
  })
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
}
