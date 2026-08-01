import {
  _electron as electron,
  expect,
  test,
  type ElectronApplication,
  type Page,
} from '@playwright/test'

const bridgeMethods = [
  'getAppInfo',
  'getCapabilities',
  'getPreferences',
  'getStartupDiagnostics',
  'retrySidecar',
  'updatePreferences',
]

async function launchShell(fixture: 'success' | 'degraded' = 'success') {
  return electron.launch({
    args: ['.'],
    env: {
      ...process.env,
      ANCESTRYLLM_DESKTOP_FIXTURE: fixture,
      ANCESTRYLLM_DESKTOP_SECURITY_E2E: '1',
    },
  })
}

async function expectProductionNavigation(page: Page) {
  const navigation = page.getByRole('navigation', { name: 'Primary' })
  await expect(navigation.getByRole('link')).toHaveText(['Home', 'Diagnostics', 'Settings'])
}

async function expectNoUnsupportedSurfaces(page: Page) {
  const main = page.getByRole('main')
  for (const prohibited of [
    /Component gallery/i,
    /Primary action/i,
    /Quiet action/i,
    /\bgenealogy\b/i,
    /\bproviders?\b/i,
    /\bcloud\b/i,
    /\baccounts?\b/i,
    /\bjobs?\b/i,
    /\bchat\b/i,
    /\bupdaters?\b/i,
  ]) {
    await expect(main).not.toContainText(prohibited)
  }
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

  const securityState = await app.evaluate(() => (
    globalThis as unknown as { __ancestryllmSecurityStateForTests(): unknown }
  ).__ancestryllmSecurityStateForTests())
  expect(securityState).toEqual({
    mode: 'production',
    rendererUrl: 'app://bundle/index.html',
    contentSecurityPolicy: "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'",
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInWorker: false,
    nodeIntegrationInSubFrames: false,
    sandbox: true,
    webSecurity: true,
    webviewTag: false,
    devTools: false,
    permissionsDenied: true,
    navigationDenied: true,
    childWindowsDenied: true,
    downloadsDenied: true,
    serviceWorkersAllowed: false,
    bypassCsp: false,
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
}

test('built shell exposes the bounded production Home, Diagnostics, and Settings surfaces', async () => {
  const app = await launchShell()
  try {
    const page = await app.firstWindow()
    const main = page.getByRole('main')

    await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
    await expectProductionNavigation(page)
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

    await page.getByRole('link', { name: 'Diagnostics' }).press('Enter')
    await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeFocused()
    await expect(main.getByText('Ready', { exact: true })).toBeVisible()
    await expect(main.getByRole('alert')).toHaveCount(0)
    await expectNoUnsupportedSurfaces(page)

    await page.getByRole('link', { name: 'Settings' }).click()
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeFocused()
    const theme = main.getByRole('group', { name: 'Theme' })
    await expect(theme.getByRole('radio')).toHaveCount(3)
    await expect(theme.getByRole('radio', { name: 'system' })).toBeChecked()
    const reducedMotion = main.getByRole('checkbox', { name: 'Reduce motion' })
    await expect(reducedMotion).not.toBeChecked()
    await reducedMotion.click()
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.reducedMotion)).toBe('true')
    await expect(reducedMotion).toBeChecked()
    await expectNoUnsupportedSurfaces(page)
  } finally {
    await app.close()
  }
})

test('built degraded shell offers one bounded recovery and renders the ready result', async () => {
  const app = await launchShell('degraded')
  try {
    const page = await app.firstWindow()
    const main = page.getByRole('main')

    await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
    await expectProductionNavigation(page)
    await expect(main.getByRole('heading', { name: 'Startup state' })).toBeVisible()
    await expect(main.getByText('Degraded', { exact: true })).toBeVisible()
    await expectNoUnsupportedSurfaces(page)

    await page.getByRole('link', { name: 'Diagnostics' }).click()
    await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeFocused()
    await expect(main.getByText('Degraded', { exact: true })).toBeVisible()
    const recovery = main.getByRole('alert')
    await expect(recovery).toContainText('The desktop service did not start.')
    await expect(recovery).not.toContainText(/startup_failed|SIDECAR_UNAVAILABLE|token|port|stderr|\.sock|\/Users\//i)

    const retry = recovery.getByRole('button', { name: 'Retry desktop service' })
    await retry.click()
    await expect(main.getByText('Ready', { exact: true })).toBeVisible()
    await expect(main.getByRole('alert')).toHaveCount(0)
    await expect(main.getByRole('button', { name: 'Retry desktop service' })).toHaveCount(0)
    await expectNoUnsupportedSurfaces(page)
  } finally {
    await app.close()
  }
})
