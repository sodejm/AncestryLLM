/** Boots the Electron main process, security policies, runtime bridge, IPC, and window lifecycle. */
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  ipcMain,
  protocol,
  session,
  shell,
  type WebContents,
} from 'electron'
import {
  completeAppShutdown,
  requestVerifiedShutdownBeforeWindowClose,
  type AppShutdownProgress,
  type UnsafeShutdownChoice,
} from './app-shutdown'
import { FileGrantBroker } from './file-grant-broker'
import { externalLinkPrompt, openExternalLinkWithConfirmation } from './external-links'
import {
  registerDesktopIpcHandlers,
  type BridgeWebContents,
  type DesktopIpcController,
  type MainDesktopBridge,
} from './ipc-handlers'
import {
  isLocalRuntimeCliRequest,
  runLocalRuntimeCli,
  writeConcurrentLocalRuntimeCliFailure,
} from './local-runtime-cli'
import { createPackagedLocalRuntimeControl } from './local-runtime-control'
import { requestedLinuxKeyringVerificationRoot } from './native-verification'
import { createNativeFileDialogPort } from './native-file-dialogs'
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
import type { JobShutdownAction } from './sidecar-client'
import { acquireSingleInstanceLock, installSingleInstanceGuard } from './single-instance'
import { WINDOW_READY_RECORD } from './window-readiness'
import { installKeyboardZoom, type KeyboardZoomTarget } from './zoom-policy'

app.enableSandbox()
const localRuntimeCliArguments = process.argv.slice(1)
const localRuntimeCliRequested = isLocalRuntimeCliRequest(localRuntimeCliArguments)
const singleInstanceDependencies = {
  requestLock: () => app.requestSingleInstanceLock(),
  onSecondInstance: (listener: () => void) => { app.on('second-instance', listener) },
  primaryWindow: () => BrowserWindow.getAllWindows()[0],
}
const primaryInstance = localRuntimeCliRequested
  ? acquireSingleInstanceLock(singleInstanceDependencies)
  : installSingleInstanceGuard({ ...singleInstanceDependencies, quit: () => app.quit() })
if (primaryInstance && !localRuntimeCliRequested) {
  protocol.registerSchemesAsPrivileged([{ scheme: 'app', privileges: APP_SCHEME_PRIVILEGES }])
}

let bridge: MainDesktopBridge | undefined
let fileGrantBroker: FileGrantBroker | undefined
const rendererRoot = join(__dirname, '../renderer')
const rendererPath = join(rendererRoot, 'index.html')
const preloadPath = join(__dirname, '../preload/index.cjs')
let sidecarSupervisor: SidecarSupervisor | undefined
let prepareJobShutdown: ((action: JobShutdownAction) => Promise<void>) | undefined
let shutdownAuthorized = false
let shutdownPromise: Promise<boolean> | undefined
const shutdownProgress: AppShutdownProgress = { jobsPrepared: false }
let ipcController: DesktopIpcController | undefined
let removeSidecarSessionListener: (() => void) | undefined

const requestVerifiedAppQuit = (): void => { app.quit() }

function armVerifiedSigtermHandler(): void {
  process.off('SIGTERM', requestVerifiedAppQuit)
  process.on('SIGTERM', requestVerifiedAppQuit)
}

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

if (primaryInstance && !localRuntimeCliRequested) {
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
  if (!fileGrantBroker) throw new Error('File-grant broker is unavailable.')
  if (ipcController) throw new Error('Desktop IPC handlers are already registered.')
  ipcController = registerDesktopIpcHandlers(ipcMain, bridge, fileGrantBroker, {
    nativeActions: Object.freeze({
      openExternalLink: (destination: string) => openExternalLinkWithConfirmation(destination, {
        confirm: async (normalized) => {
          const response = await dialog.showMessageBox(externalLinkPrompt(normalized))
          return response.response === 1
        },
        openExternal: async (normalized) => { await shell.openExternal(normalized) },
      }),
      copyText: (text: string) => { clipboard.writeText(text) },
    }),
  })
}

function disposeIpcBoundary(): void {
  removeSidecarSessionListener?.()
  removeSidecarSessionListener = undefined
  const controller = ipcController
  ipcController = undefined
  controller?.dispose()
  if (!controller) fileGrantBroker?.dispose()
  fileGrantBroker = undefined
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
  if (!ipcController) throw new Error('Desktop IPC handlers are unavailable.')
  ipcController.authorizeWebContents(
    window.webContents as unknown as BridgeWebContents,
    (url) => isTrustedRendererUrl({ ...rendererPolicy(), senderUrl: url }),
  )
  window.on('close', (event) => {
    requestVerifiedShutdownBeforeWindowClose(
      event,
      !shutdownAuthorized && (sidecarSupervisor !== undefined || shutdownPromise !== undefined),
      shutdownPromise !== undefined,
      () => app.quit(),
    )
  })
  window.once('ready-to-show', () => {
    window.show()
    console.info(WINDOW_READY_RECORD)
  })
  void window.loadURL(resolveRendererTarget(rendererPolicy()).value)
}

async function chooseUnsafeShutdownAction(): Promise<UnsafeShutdownChoice> {
  const result = await dialog.showMessageBox({
    type: 'warning',
    title: 'Background work is still active',
    message: 'AncestryLLM cannot yet verify a safe exit.',
    detail: 'Choose Wait to allow protected work to finish, Request cancellation to stop at a safe point, or Stay open. Atomic publication is never abandoned mid-operation.',
    buttons: ['Wait', 'Request cancellation', 'Stay open'],
    defaultId: 0,
    cancelId: 2,
    noLink: true,
  })
  if (result.response === 0) return 'wait'
  if (result.response === 1) return 'cancel'
  return 'stay'
}

if (localRuntimeCliRequested && !primaryInstance) {
  app.exit(writeConcurrentLocalRuntimeCliFailure((line) => process.stdout.write(line)))
} else if (localRuntimeCliRequested) {
  void app.whenReady().then(async () => {
    const code = await runLocalRuntimeCli(
      localRuntimeCliArguments,
      createPackagedLocalRuntimeControl(process.resourcesPath, app.getPath('userData')),
      (line) => process.stdout.write(line),
    )
    app.exit(code)
  }).catch(() => {
    process.stdout.write(`${JSON.stringify({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'INTERNAL_ERROR',
        message: 'The local runtime command could not be started.',
        remediation: 'Try again or collect sanitized local runtime diagnostics.',
      },
    })}\n`)
    app.exit(1)
  })
} else if (primaryInstance) {
  // Cover signals before Electron readiness, then re-arm the same named
  // listener once runtime ownership is established. Electron/Chromium startup
  // must never leave SIGTERM on its default process-termination path.
  armVerifiedSigtermHandler()
  app.whenReady().then(async () => {
    const runtime = await startRuntimeBridge((supervisor, prepareJobs) => {
      sidecarSupervisor = supervisor
      prepareJobShutdown = prepareJobs
      armVerifiedSigtermHandler()
    }, {
      linuxKeyringVerificationRoot: requestedLinuxKeyringVerificationRoot(app.commandLine),
    })
    bridge = runtime.bridge
    await protocol.handle('app', createAppProtocolHandler(async (file) => readFile(join(rendererRoot, file))))
    installSessionPolicy(session.defaultSession as unknown as Parameters<typeof installSessionPolicy>[0])
    fileGrantBroker = new FileGrantBroker(createNativeFileDialogPort())
    registerIpcHandlers()
    removeSidecarSessionListener = sidecarSupervisor?.onSessionInvalidated(() => {
      ipcController?.invalidateSidecarSession()
    })
    createWindow()
    app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
  })
  app.on('before-quit', (event) => {
    if (shutdownAuthorized) return
    if (!sidecarSupervisor && !shutdownPromise) {
      disposeIpcBoundary()
      return
    }
    event.preventDefault()
    if (!shutdownPromise && sidecarSupervisor) {
      const supervisor = sidecarSupervisor
      const prepareJobs = prepareJobShutdown
      shutdownPromise = completeAppShutdown(
        async (action) => {
          if (!prepareJobs) throw new Error('Job shutdown preparation is unavailable.')
          await prepareJobs(action)
        },
        chooseUnsafeShutdownAction,
        async () => {
          await supervisor.stop()
        },
        () => console.error('Background jobs or sidecar shutdown could not be verified.'),
        () => {
          disposeIpcBoundary()
          sidecarSupervisor = undefined
          prepareJobShutdown = undefined
          shutdownAuthorized = true
          // The normal quit lifecycle has already been vetoed while the
          // sidecar was owned. Once shutdown is verified, exit directly so
          // Electron cannot re-enter a platform-specific window-close cycle.
          app.exit(0)
        },
        () => supervisor.isExplicitSafeEmpty(),
        shutdownProgress,
      ).finally(() => { shutdownPromise = undefined })
    }
  })
  app.on('window-all-closed', () => { app.quit() })
}
