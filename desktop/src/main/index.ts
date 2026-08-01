import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  protocol,
  session,
  type IpcMainInvokeEvent,
  type WebContents,
} from 'electron'
import { createMockAncestryBridge } from '../mock-bridge/desktop'
import type { AncestryBridge } from '../shared-contract/desktop'
import { createDesktopControlBridge } from './desktop-control'
import { registerDesktopIpcHandlers } from './ipc-handlers'
import { FilePreferencesStore } from './preferences-store'
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
import { launchNativeSidecar, probeNativeSidecar } from './sidecar-process'
import { createSidecarCapabilitiesClient } from './sidecar-client'
import { resolveSidecarExecutable, SidecarSupervisor } from './sidecar-supervisor'
import { installSingleInstanceGuard } from './single-instance'

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

const fixture = process.env.ANCESTRYLLM_DESKTOP_FIXTURE
let bridge: AncestryBridge = createMockAncestryBridge(
  fixture === 'degraded' || fixture === 'unavailable' ? fixture : 'success',
)
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
    configuredPreferences.set(contents, preferences)
    denyWebContentsCapabilities(contents)
  })
}

function runtimeSecurityState(window: BrowserWindow) {
  const preferences = configuredPreferences.get(window.webContents)
  if (!preferences) throw new Error('Unregistered BrowserWindow')
  return createRuntimeSecurityState(window.webContents.getURL(), preferences, isProductionRenderer())
}

function registerIpcHandlers(): void {
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
  window.once('ready-to-show', () => window.show())
  void window.loadURL(resolveRendererTarget(rendererPolicy()).value)
}

async function startPackagedSidecar(): Promise<void> {
  if (!app.isPackaged) return
  const preferences = new FilePreferencesStore(app.getPath('userData'))
  sidecarSupervisor = new SidecarSupervisor({
    appBuild: app.getVersion(),
    executablePath: resolveSidecarExecutable(process.resourcesPath, process.platform, process.arch),
    launch: launchNativeSidecar,
    probe: probeNativeSidecar,
    startupTimeoutMs: 10_000,
    maxRestarts: 2,
    maxManualRetries: 1,
    onFatal: () => {
      dialog.showErrorBox(
        'AncestryLLM sidecar unavailable',
        'The private service is unavailable. This window will remain open for diagnostics; restart AncestryLLM or reinstall the application if the problem continues.',
      )
    },
  })
  bridge = createDesktopControlBridge({
    appInfo: {
      applicationName: 'AncestryLLM',
      appVersion: app.getVersion(),
      buildChannel: 'packaged',
    },
    supervisor: sidecarSupervisor,
    capabilitiesClient: createSidecarCapabilitiesClient({ session: () => sidecarSupervisor?.session() }),
    preferences,
  })
  await sidecarSupervisor.start()
}

if (primaryInstance) {
  app.whenReady().then(async () => {
    await startPackagedSidecar().catch(() => undefined)
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
