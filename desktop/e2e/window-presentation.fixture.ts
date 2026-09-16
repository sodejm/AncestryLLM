/** Provides opt-in hidden windows exclusively for source-built Electron tests. */
import { app } from 'electron'
import type { WindowPresentation } from '../src/main/window-presentation'

/** Hides opted-in source windows without changing production or packaged presentation. */
export function createWindowPresentation(): WindowPresentation {
  const hidden = !app.isPackaged && process.env.ANCESTRYLLM_E2E_HEADLESS === '1'
  return {
    visible: !hidden,
    prepareApp: () => {
      if (hidden && process.platform === 'darwin') app.setActivationPolicy('accessory')
    },
    showWindow: (window) => { if (!hidden) window.show() },
  }
}
