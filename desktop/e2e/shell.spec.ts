import {
  _electron as electron,
  expect,
  test,
  type ElectronApplication,
  type Page,
} from '@playwright/test'
import axe from 'axe-core'
import { PRODUCTION_CSP } from '../src/main/security-policy'
import { bridgeMethods } from './bridge-contract'

async function launchShell(fixture: 'success' | 'degraded' = 'success') {
  return electron.launch({
    args: ['.'],
    env: {
      ...process.env,
      ANCESTRYLLM_DESKTOP_FIXTURE: fixture,
    },
  })
}

async function expectProductionNavigation(page: Page) {
  const navigation = page.getByRole('navigation', { name: 'Primary' })
  await expect(navigation.getByRole('link')).toHaveText(['Home', 'Diagnostics', 'Settings'])
}

async function expectNoUnsupportedSurfaces(page: Page, allowProviderSettings = false) {
  const main = page.getByRole('main')
  const prohibited = [
    /Component gallery/i,
    /Primary action/i,
    /Quiet action/i,
    /\bgenealogy\b/i,
    /\bcloud\b/i,
    /\baccounts?\b/i,
    /\bjobs?\b/i,
    /\bchat\b/i,
    /\bupdaters?\b/i,
  ]
  if (!allowProviderSettings) prohibited.push(/\bproviders?\b/i)
  for (const pattern of prohibited) {
    await expect(main).not.toContainText(pattern)
  }
}

async function expectNoAccessibilityViolations(page: Page) {
  // Playwright evaluates the reviewed, lock-pinned axe source in Chromium's
  // automation world. It does not weaken the production CSP or ship axe in the
  // renderer bundle.
  await page.evaluate(axe.source)
  const violations = await page.evaluate(async () => {
    const axeRunner = (globalThis as unknown as {
      axe: {
        run(
          context: Document,
          options: { runOnly: { type: 'tag'; values: string[] } },
        ): Promise<{
          violations: Array<{
            id: string
            impact: string | null
            nodes: Array<{ target: string[]; failureSummary?: string }>
          }>
        }>
      }
    }).axe
    const result = await axeRunner.run(document, {
      runOnly: {
        type: 'tag',
        values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'],
      },
    })
    return result.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      nodes: violation.nodes.map((node) => ({
        target: node.target,
        failureSummary: node.failureSummary,
      })),
    }))
  })
  expect(violations).toEqual([])
}

async function expectNoHorizontalClipping(page: Page) {
  const layout = await page.evaluate(() => {
    const controls = Array.from(document.querySelectorAll<HTMLElement>(
      'a[href], button, input, select, textarea, [tabindex="0"]',
    ))
    const clippedControls = controls.flatMap((control) => {
      const style = getComputedStyle(control)
      const bounds = control.getBoundingClientRect()
      if (
        style.display === 'none'
        || style.visibility === 'hidden'
        || bounds.width === 0
        || bounds.height === 0
      ) return []
      if (bounds.left >= -0.5 && bounds.right <= window.innerWidth + 0.5) return []
      return [{
        name: control.getAttribute('aria-label') ?? control.textContent?.trim() ?? control.tagName,
        left: bounds.left,
        right: bounds.right,
      }]
    })
    return {
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      clippedControls,
    }
  })
  expect(layout.viewportWidth).toBeGreaterThanOrEqual(340)
  expect(layout.viewportWidth).toBeLessThanOrEqual(365)
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth)
  expect(layout.clippedControls).toEqual([])
}

async function expectBoundedBridgeAndSecurity(app: ElectronApplication, page: Page) {
  expect(await page.evaluate(() => typeof (globalThis as { process?: unknown }).process)).toBe('undefined')
  expect(await page.evaluate(() => {
    const ancestry = (window as unknown as { ancestry: object }).ancestry
    return {
      frozen: Object.isFrozen(ancestry),
      methods: Object.keys(ancestry).sort(),
    }
  })).toEqual({ frozen: true, methods: bridgeMethods })

  const securityState = await app.evaluate(async ({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0]
    if (!window) throw new Error('No BrowserWindow')
    const preferences = (window.webContents as unknown as {
      getLastWebPreferences(): {
        contextIsolation?: boolean
        nodeIntegration?: boolean
        nodeIntegrationInWorker?: boolean
        nodeIntegrationInSubFrames?: boolean
        sandbox?: boolean
        webSecurity?: boolean
        webviewTag?: boolean
        devTools?: boolean
      }
    }).getLastWebPreferences()
    return {
      rendererUrl: window.webContents.getURL(),
      contextIsolation: preferences.contextIsolation,
      nodeIntegration: preferences.nodeIntegration,
      // Electron 39 omits explicit false values for these false-by-default
      // preferences from getLastWebPreferences(). The constructor contract is
      // still asserted directly by security-policy.test.ts.
      nodeIntegrationInWorker: preferences.nodeIntegrationInWorker ?? false,
      nodeIntegrationInSubFrames: preferences.nodeIntegrationInSubFrames ?? false,
      sandbox: preferences.sandbox,
      webSecurity: preferences.webSecurity,
      webviewTag: preferences.webviewTag,
      devToolsOpenBeforeRequest: window.webContents.isDevToolsOpened(),
      devToolsOpenAfterRequest: await (async () => {
        window.webContents.openDevTools({ mode: 'detach' })
        await new Promise((resolve) => setTimeout(resolve, 100))
        const opened = window.webContents.isDevToolsOpened()
        if (opened) window.webContents.closeDevTools()
        return opened
      })(),
    }
  })
  expect(securityState).toEqual({
    rendererUrl: 'app://bundle/index.html',
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInWorker: false,
    nodeIntegrationInSubFrames: false,
    sandbox: true,
    webSecurity: true,
    webviewTag: false,
    devToolsOpenBeforeRequest: false,
    devToolsOpenAfterRequest: false,
  })

  const response = await page.reload()
  expect(await response?.headerValue('content-security-policy')).toBe(PRODUCTION_CSP)

  const externalRequests: string[] = []
  page.on('request', (request) => {
    if (/^(?:https?|wss?):/i.test(request.url())) externalRequests.push(request.url())
  })
  const deniedCapabilities = await page.evaluate(async () => {
    let fetchBlocked = false
    try { await fetch('https://example.invalid/') } catch { fetchBlocked = true }

    const webSocketBlocked = await new Promise<boolean>((resolve) => {
      const socket = new WebSocket('wss://example.invalid/')
      const timer = setTimeout(() => resolve(false), 1_500)
      socket.addEventListener('error', () => { clearTimeout(timer); resolve(true) }, { once: true })
      socket.addEventListener('open', () => { clearTimeout(timer); socket.close(); resolve(false) }, { once: true })
    })

    let serviceWorkerBlocked = !('serviceWorker' in navigator)
    if (!serviceWorkerBlocked) {
      try {
        const registration = await navigator.serviceWorker.register('/service-worker.js')
        await registration.unregister()
      } catch { serviceWorkerBlocked = true }
    }
    return {
      fetchBlocked,
      webSocketBlocked,
      serviceWorkerBlocked,
      childWindowBlocked: window.open('https://github.com/') === null,
    }
  })
  expect(deniedCapabilities).toEqual({
    fetchBlocked: true,
    webSocketBlocked: true,
    serviceWorkerBlocked: true,
    childWindowBlocked: true,
  })
  expect(externalRequests).toEqual([])
}

test('built shell exposes the bounded production Home, Diagnostics, and Settings surfaces', async () => {
  const app = await launchShell()
  try {
    const page = await app.firstWindow()
    const main = page.getByRole('main')

    await expect(page.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeFocused()
    await expectProductionNavigation(page)
    await expect(main.getByText('Your desktop control shell stays local to this device.', { exact: true })).toBeVisible()
    await expect(main.getByText(/No account, provider, API key, genealogy data, or cloud consent is requested here/i)).toBeVisible()
    await expect(main.getByText(/Updates are installed manually/i)).toBeVisible()
    await expect(main.getByRole('link', { name: 'Open Diagnostics' })).toHaveAttribute('href', '#/diagnostics')
    await main.getByRole('button', { name: 'Continue to Home' }).click()

    await expect(page.getByRole('heading', { name: 'Home' })).toBeFocused()
    await expect(main.getByRole('heading', { name: 'Application' })).toBeVisible()
    await expect(main.getByText('AncestryLLM', { exact: true })).toBeVisible()
    await expect(main.getByText('0.5.0-dev', { exact: true })).toBeVisible()
    await expect(main.getByRole('heading', { name: 'Offline posture' })).toBeVisible()
    await expect(main.getByRole('heading', { name: 'Startup state' })).toBeVisible()
    await expect(main.getByText('Ready', { exact: true })).toBeVisible()
    await expect(main.getByRole('heading', { name: 'Capabilities' })).toBeVisible()
    await expect(main.getByText('No control capabilities are currently available.', { exact: true })).toBeVisible()
    await expectNoUnsupportedSurfaces(page)
    await expectBoundedBridgeAndSecurity(app, page)

    await page.reload()
    await expect(page.getByRole('heading', { name: 'Home' })).toBeFocused()
    await expect(main.getByRole('button', { name: 'Review welcome' })).toBeVisible()
    await expect(main.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toHaveCount(0)

    await page.getByRole('link', { name: 'Diagnostics' }).press('Enter')
    await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeFocused()
    await expect(main.getByLabel('Desktop service').getByText('Ready', { exact: true })).toBeVisible()
    await expect(main.getByRole('alert')).toHaveCount(0)
    await expectNoUnsupportedSurfaces(page)

    await page.getByRole('link', { name: 'Settings' }).click()
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeFocused()
    const theme = main.getByRole('group', { name: 'Theme' })
    await expect(theme.getByRole('radio')).toHaveCount(3)
    await expect(theme.getByRole('radio', { name: 'system' })).toBeChecked()
    const reducedMotion = main.getByRole('checkbox', { name: 'Reduce motion' })
    await expect(reducedMotion).not.toBeChecked()
    await reducedMotion.click()
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.reducedMotion)).toBe('true')
    await expect(reducedMotion).toBeChecked()
    await expectNoUnsupportedSurfaces(page, true)
  } finally {
    await app.close()
  }
})

test('built degraded shell offers one bounded recovery and renders the ready result', async () => {
  const app = await launchShell('degraded')
  try {
    const page = await app.firstWindow()
    const main = page.getByRole('main')

    await expect(page.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeFocused()
    await expectProductionNavigation(page)

    await page.getByRole('link', { name: 'Diagnostics', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeFocused()
    await expect(main.getByText('Degraded', { exact: true })).toBeVisible()
    const recovery = main.getByRole('alert')
    await expect(recovery).toContainText('The desktop service did not start.')
    await expect(recovery).not.toContainText(/startup_failed|SIDECAR_UNAVAILABLE|token|port|stderr|\.sock|\/Users\//i)

    const retry = recovery.getByRole('button', { name: 'Retry desktop service' })
    await retry.click()
    await expect(main.getByLabel('Desktop service').getByText('Ready', { exact: true })).toBeVisible()
    await expect(main.getByRole('alert')).toHaveCount(0)
    await expect(main.getByRole('button', { name: 'Retry desktop service' })).toHaveCount(0)
    await expectNoUnsupportedSurfaces(page)

    await page.getByRole('link', { name: 'Home' }).click()
    await expect(page.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeFocused()
    await main.getByRole('button', { name: 'Continue to Home' }).click()
    await expect(page.getByRole('heading', { name: 'Home' })).toBeFocused()
    await expect(main.getByText('Ready', { exact: true })).toBeVisible()
    await expectNoUnsupportedSurfaces(page)
  } finally {
    await app.close()
  }
})

test('built shell has deterministic skip-link and command-palette focus', async () => {
  const app = await launchShell()
  try {
    const page = await app.firstWindow()
    await expect(page.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeFocused()

    const skipLink = page.getByRole('link', { name: 'Skip to workspace' })
    // Route entry deliberately lands on the workspace heading so keyboard and
    // assistive-technology users have already skipped repeated navigation. The
    // explicit skip control must remain reachable when traversing backward.
    for (let attempt = 0; attempt < 8; attempt += 1) {
      if (await skipLink.evaluate((element) => document.activeElement === element)) break
      await page.keyboard.press('Shift+Tab')
    }
    await expect(skipLink).toBeFocused()
    const initialHash = await page.evaluate(() => window.location.hash)
    await skipLink.press('Enter')
    await expect(page.getByRole('main')).toBeFocused()
    expect(await page.evaluate(() => window.location.hash)).toBe(initialHash)

    await page.keyboard.press('Control+K')
    const filter = page.getByRole('searchbox', { name: 'Filter destinations' })
    await expect(filter).toBeFocused()
    await page.keyboard.press('Escape')
    const trigger = page.getByRole('button', { name: /Navigate/ })
    await expect(trigger).toBeFocused()

    await trigger.press('Enter')
    await filter.fill('diagnostics')
    await page.getByRole('dialog').getByRole('link', { name: /Diagnostics/ }).click()
    await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeFocused()
    expect(await page.evaluate(() => window.location.hash)).toBe('#/diagnostics')
  } finally {
    await app.close()
  }
})

test('built shell passes automated WCAG checks across routes and explicit themes', async () => {
  const app = await launchShell()
  try {
    const page = await app.firstWindow()
    const main = page.getByRole('main')

    await expect(page.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeFocused()
    await expectNoAccessibilityViolations(page)
    await main.getByRole('button', { name: 'Continue to Home' }).click()
    await expectNoAccessibilityViolations(page)

    await page.getByRole('link', { name: 'Diagnostics', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeFocused()
    await expectNoAccessibilityViolations(page)

    await page.getByRole('link', { name: 'Settings', exact: true }).click()
    const theme = main.getByRole('group', { name: 'Theme' })
    await theme.getByRole('radio', { name: 'light' }).click()
    await expectNoAccessibilityViolations(page)
    await theme.getByRole('radio', { name: 'dark' }).click()
    await expectNoAccessibilityViolations(page)
  } finally {
    await app.close()
  }
})

test('minimum desktop window at 200 percent zoom keeps every action horizontally reachable', async () => {
  const app = await launchShell()
  try {
    const page = await app.firstWindow()
    await app.evaluate(({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows()[0]
      if (!window) throw new Error('No BrowserWindow')
      window.setSize(720, 560)
      window.webContents.setZoomFactor(2)
    })
    await expect.poll(() => page.evaluate(() => window.innerWidth)).toBeLessThanOrEqual(365)

    await expectNoHorizontalClipping(page)
    await page.getByRole('link', { name: 'Diagnostics', exact: true }).click()
    await expectNoHorizontalClipping(page)
    await page.getByRole('link', { name: 'Settings', exact: true }).click()
    await expectNoHorizontalClipping(page)
  } finally {
    await app.close()
  }
})
