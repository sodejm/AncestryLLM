/** Adapts Electron native dialogs to the bounded file-grant selection port. */
import { BrowserWindow, dialog, type WebContents } from 'electron'
import type { FileReadPurpose, FileWritePurpose } from '../shared-contract/desktop'
import {
  FileGrantBrokerError,
  type NativeFileDialogPort,
} from './file-grant-broker'

interface FileFilter {
  readonly name: string
  readonly extensions: readonly string[]
}

interface OpenDialogOptions {
  readonly title: string
  readonly properties: readonly string[]
  readonly filters: readonly FileFilter[]
}

interface SaveDialogOptions {
  readonly title: string
  readonly defaultPath: string
  readonly showsTagField: false
  readonly filters: readonly FileFilter[]
}

interface MessageBoxOptions {
  readonly type: 'warning'
  readonly title: string
  readonly message: string
  readonly detail: string
  readonly buttons: readonly ['Cancel', 'Replace']
  readonly defaultId: 0
  readonly cancelId: 0
  readonly noLink: true
}

interface NativeDialogDependencies {
  readonly windowFromOwner: (owner: object) => object | null
  readonly showOpenDialog: (
    owner: object,
    options: OpenDialogOptions,
  ) => Promise<Readonly<{ canceled: boolean; filePaths: readonly string[] }>>
  readonly showSaveDialog: (
    owner: object,
    options: SaveDialogOptions,
  ) => Promise<Readonly<{ canceled: boolean; filePath?: string }>>
  readonly showMessageBox: (
    owner: object,
    options: MessageBoxOptions,
  ) => Promise<Readonly<{ response: number }>>
}

const purposeFilters: Readonly<Record<FileReadPurpose | FileWritePurpose, readonly FileFilter[]>> = Object.freeze({
  'gedcom-read': Object.freeze([{ name: 'GEDCOM family tree', extensions: Object.freeze(['ged', 'gedcom']) }]),
  'rootsmagic-read': Object.freeze([{ name: 'RootsMagic database', extensions: Object.freeze(['rmtree']) }]),
  'gedcom-write': Object.freeze([{ name: 'GEDCOM family tree', extensions: Object.freeze(['ged']) }]),
  'json-write': Object.freeze([{ name: 'JSON data', extensions: Object.freeze(['json']) }]),
  'markdown-write': Object.freeze([{ name: 'Markdown document', extensions: Object.freeze(['md']) }]),
})

const electronDependencies: NativeDialogDependencies = Object.freeze({
  windowFromOwner: (owner: object) => BrowserWindow.fromWebContents(owner as WebContents),
  showOpenDialog: (owner: object, options: OpenDialogOptions) => dialog.showOpenDialog(
    owner as BrowserWindow,
    options as Electron.OpenDialogOptions,
  ),
  showSaveDialog: (owner: object, options: SaveDialogOptions) => dialog.showSaveDialog(
    owner as BrowserWindow,
    options as Electron.SaveDialogOptions,
  ),
  showMessageBox: (owner: object, options: MessageBoxOptions) => dialog.showMessageBox(
    owner as BrowserWindow,
    options as unknown as Electron.MessageBoxOptions,
  ),
})

function requireActive(signal?: AbortSignal): void {
  if (signal?.aborted) throw signal.reason ?? new Error('File dialog request was cancelled.')
}

/** Preserves cancellation while translating native dialog failures into a stable coded error. */
async function invokeDialog<T>(signal: AbortSignal | undefined, operation: () => Promise<T>): Promise<T> {
  requireActive(signal)
  try {
    const result = await operation()
    requireActive(signal)
    return result
  } catch (cause) {
    requireActive(signal)
    if (cause instanceof FileGrantBrokerError) throw cause
    throw new FileGrantBrokerError('FILE_DIALOG_FAILED')
  }
}

/** Resolves a dialog owner only when it maps to a live trusted application window. */
function ownerWindow(dependencies: NativeDialogDependencies, owner: object): object {
  const window = dependencies.windowFromOwner(owner)
  if (window === null) throw new FileGrantBrokerError('FILE_DIALOG_FAILED')
  return window
}

/**
 * Creates the trusted-window dialog adapter used to issue path-free file capabilities.
 *
 * Dialog cancellation remains distinct from operational failure, and untrusted owners fail closed.
 */
export function createNativeFileDialogPort(
  dependencies: NativeDialogDependencies = electronDependencies,
): NativeFileDialogPort {
  const port: NativeFileDialogPort = {
    async selectOpenFile(owner: object, purpose: FileReadPurpose, signal?: AbortSignal) {
      const result = await invokeDialog(signal, () => dependencies.showOpenDialog(
        ownerWindow(dependencies, owner),
        Object.freeze({
          title: purpose === 'gedcom-read' ? 'Open a GEDCOM family tree' : 'Open a RootsMagic database',
          properties: Object.freeze(['openFile', 'dontAddToRecent']),
          filters: purposeFilters[purpose],
        }),
      ))
      if (result.canceled || result.filePaths.length === 0) return null
      if (result.filePaths.length !== 1 || typeof result.filePaths[0] !== 'string') {
        throw new FileGrantBrokerError('FILE_DIALOG_FAILED')
      }
      return result.filePaths[0]
    },
    async selectSaveFile(
      owner: object,
      purpose: FileWritePurpose,
      suggestedName: string,
      signal?: AbortSignal,
    ) {
      const result = await invokeDialog(signal, () => dependencies.showSaveDialog(
        ownerWindow(dependencies, owner),
        Object.freeze({
          title: 'Choose where to save the generated file',
          defaultPath: suggestedName,
          showsTagField: false,
          filters: purposeFilters[purpose],
        }),
      ))
      if (result.canceled || result.filePath === undefined || result.filePath.length === 0) return null
      return result.filePath
    },
    async confirmReplacement(owner: object, displayName: string, signal?: AbortSignal) {
      const result = await invokeDialog(signal, () => dependencies.showMessageBox(
        ownerWindow(dependencies, owner),
        Object.freeze({
          type: 'warning',
          title: 'Replace existing file?',
          message: `Replace ${displayName}?`,
          detail: 'The existing file will be replaced only after the new output is complete.',
          buttons: Object.freeze(['Cancel', 'Replace'] as const),
          defaultId: 0,
          cancelId: 0,
          noLink: true,
        }),
      ))
      return result.response === 1
    },
  }
  return Object.freeze(port)
}
