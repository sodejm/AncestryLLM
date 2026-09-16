/** Keeps ordinary application windows visible without consulting test selectors. */
import type { BrowserWindow } from 'electron'

/** Controls presentation separately from renderer startup and security policy. */
export interface WindowPresentation {
  readonly visible: boolean
  /** Prepares platform presentation after Electron readiness, before window creation. */
  prepareApp(): void
  /** Presents a ready window according to the selected build's policy. */
  showWindow(window: Pick<BrowserWindow, 'show'>): void
}

/** Returns the production policy, which always presents ready application windows. */
export function createWindowPresentation(): WindowPresentation {
  return {
    visible: true,
    prepareApp: () => { /* Normal launches retain Electron's default activation policy. */ },
    showWindow: (window) => { window.show() },
  }
}
