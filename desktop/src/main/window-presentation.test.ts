/** Verifies production presentation and the isolated source-test hidden policy. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createWindowPresentation } from './window-presentation'
import { createWindowPresentation as createFixturePresentation } from '../../e2e/window-presentation.fixture'

const electronApp = vi.hoisted(() => ({
  isPackaged: false,
  setActivationPolicy: vi.fn(),
}))
vi.mock('electron', () => ({ app: electronApp }))

afterEach(() => {
  vi.unstubAllEnvs()
  electronApp.isPackaged = false
  electronApp.setActivationPolicy.mockClear()
})

describe('window presentation', () => {
  it('always presents production windows even when the test selector is inherited', () => {
    vi.stubEnv('ANCESTRYLLM_E2E_HEADLESS', '1')
    const presentation = createWindowPresentation()
    const window = { show: vi.fn() }
    presentation.prepareApp()
    presentation.showWindow(window)
    expect(presentation.visible).toBe(true)
    expect(window.show).toHaveBeenCalledOnce()
    expect(electronApp.setActivationPolicy).not.toHaveBeenCalled()
  })

  it.each([undefined, '0', 'true'])('keeps source windows visible without the exact opt-in (%s)', (value) => {
    vi.stubEnv('ANCESTRYLLM_E2E_HEADLESS', value)
    const presentation = createFixturePresentation()
    const window = { show: vi.fn() }
    presentation.prepareApp()
    presentation.showWindow(window)
    expect(presentation.visible).toBe(true)
    expect(window.show).toHaveBeenCalledOnce()
    expect(electronApp.setActivationPolicy).not.toHaveBeenCalled()
  })

  it('never shows opted-in source windows, including repeated ready events', () => {
    vi.stubEnv('ANCESTRYLLM_E2E_HEADLESS', '1')
    const presentation = createFixturePresentation()
    const window = { show: vi.fn() }
    presentation.prepareApp()
    presentation.showWindow(window)
    presentation.showWindow(window)
    expect(presentation.visible).toBe(false)
    expect(window.show).not.toHaveBeenCalled()
    if (process.platform === 'darwin') {
      expect(electronApp.setActivationPolicy).toHaveBeenCalledExactlyOnceWith('accessory')
    } else {
      expect(electronApp.setActivationPolicy).not.toHaveBeenCalled()
    }
  })

  it('refuses to hide a packaged application even in an incorrectly selected fixture build', () => {
    vi.stubEnv('ANCESTRYLLM_E2E_HEADLESS', '1')
    electronApp.isPackaged = true
    const presentation = createFixturePresentation()
    const window = { show: vi.fn() }
    presentation.prepareApp()
    presentation.showWindow(window)
    expect(presentation.visible).toBe(true)
    expect(window.show).toHaveBeenCalledOnce()
    expect(electronApp.setActivationPolicy).not.toHaveBeenCalled()
  })
})
