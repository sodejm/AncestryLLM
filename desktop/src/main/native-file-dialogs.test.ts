/** Verifies native file dialogs enforce owner, purpose, and replacement boundaries. */
import { describe, expect, it, vi } from 'vitest'
import { createNativeFileDialogPort } from './native-file-dialogs'

vi.mock('electron', () => ({
  BrowserWindow: { fromWebContents: vi.fn() },
  dialog: {
    showMessageBox: vi.fn(),
    showOpenDialog: vi.fn(),
    showSaveDialog: vi.fn(),
  },
}))

function dependencies() {
  const window = Object.freeze({ id: 1 })
  return {
    window,
    port: {
      windowFromOwner: vi.fn().mockReturnValue(window),
      showOpenDialog: vi.fn().mockResolvedValue({ canceled: false, filePaths: ['/safe/tree.ged'] }),
      showSaveDialog: vi.fn().mockResolvedValue({ canceled: false, filePath: '/safe/report.md' }),
      showMessageBox: vi.fn().mockResolvedValue({ response: 0 }),
      showItemInFolder: vi.fn(),
    },
  }
}

describe('native file dialog adapter', () => {
  it('binds one native open selection to the correct owner and constrained format', async () => {
    const { window, port } = dependencies()
    const owner = Object.freeze({ id: 7 })
    const dialogs = createNativeFileDialogPort(port)

    await expect(dialogs.selectOpenFile(owner, 'gedcom-read')).resolves.toBe('/safe/tree.ged')
    expect(port.windowFromOwner).toHaveBeenCalledWith(owner)
    expect(port.showOpenDialog).toHaveBeenCalledWith(window, {
      title: 'Open a GEDCOM family tree',
      properties: ['openFile', 'dontAddToRecent'],
      filters: [{ name: 'GEDCOM family tree', extensions: ['ged', 'gedcom'] }],
    })
  })

  it('treats cancel as no grant and rejects ambiguous or ownerless selections', async () => {
    const canceled = dependencies()
    canceled.port.showOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] })
    await expect(createNativeFileDialogPort(canceled.port).selectOpenFile({}, 'rootsmagic-read')).resolves.toBeNull()

    const ambiguous = dependencies()
    ambiguous.port.showOpenDialog.mockResolvedValue({ canceled: false, filePaths: ['/a.ged', '/b.ged'] })
    await expect(createNativeFileDialogPort(ambiguous.port).selectOpenFile({}, 'gedcom-read')).rejects.toMatchObject({
      code: 'FILE_DIALOG_FAILED',
    })

    const ownerless = dependencies()
    ownerless.port.windowFromOwner.mockReturnValue(null)
    await expect(createNativeFileDialogPort(ownerless.port).selectOpenFile({}, 'gedcom-read')).rejects.toMatchObject({
      code: 'FILE_DIALOG_FAILED',
    })
  })

  it('uses only the suggested basename in save and replacement UI', async () => {
    const { port } = dependencies()
    const dialogs = createNativeFileDialogPort(port)

    await expect(dialogs.selectSaveFile({}, 'markdown-write', 'report.md')).resolves.toBe('/safe/report.md')
    expect(port.showSaveDialog).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
      defaultPath: 'report.md',
      showsTagField: false,
      filters: [{ name: 'Markdown document', extensions: ['md'] }],
    }))
    await expect(dialogs.confirmReplacement({}, 'report.md')).resolves.toBe(false)
    const options = port.showMessageBox.mock.calls[0]?.[1]
    expect(options).toMatchObject({
      message: 'Replace report.md?',
      buttons: ['Cancel', 'Replace'],
      defaultId: 0,
      cancelId: 0,
    })
    expect(JSON.stringify(options)).not.toContain('/safe/')
  })

  it('selects the exact new RootsMagic output path and preserves cancellation', async () => {
    const selected = dependencies()
    selected.port.showSaveDialog.mockResolvedValue({ canceled: false, filePath: '/safe/Tree Export' })
    const dialogs = createNativeFileDialogPort(selected.port)

    await expect(dialogs.selectNewOutputDirectory({}, 'Tree Export')).resolves.toBe('/safe/Tree Export')
    expect(selected.port.showSaveDialog).toHaveBeenCalledWith(selected.window, {
      title: 'Choose a new RootsMagic export folder',
      defaultPath: 'Tree Export',
      showsTagField: false,
      filters: [],
    })

    const canceled = dependencies()
    canceled.port.showSaveDialog.mockResolvedValue({ canceled: true })
    await expect(createNativeFileDialogPort(canceled.port).selectNewOutputDirectory({}, 'Tree Export'))
      .resolves.toBeNull()
  })

  it('reveals the exact completed folder and maps native failures to a coded error', async () => {
    const working = dependencies()
    await expect(createNativeFileDialogPort(working.port).reveal('/safe/Tree Export')).resolves.toBeUndefined()
    expect(working.port.showItemInFolder).toHaveBeenCalledWith('/safe/Tree Export')

    const failingReveal = dependencies()
    failingReveal.port.showItemInFolder.mockImplementation(() => { throw new Error('native failure') })
    await expect(createNativeFileDialogPort(failingReveal.port).reveal('/safe/Tree Export')).rejects.toMatchObject({
      code: 'FILE_DIALOG_FAILED',
    })

    const failingPicker = dependencies()
    failingPicker.port.showSaveDialog.mockRejectedValue(new Error('native failure'))
    await expect(createNativeFileDialogPort(failingPicker.port).selectNewOutputDirectory({}, 'Tree Export'))
      .rejects.toMatchObject({ code: 'FILE_DIALOG_FAILED' })
  })
})
