import { _electron as electron, expect, test } from '@playwright/test'

test('packaged shell exposes only the bounded bridge and keyboard navigation', async () => {
  const app = await electron.launch({
    args: ['.'],
    env: { ...process.env, ANCESTRYLLM_DESKTOP_SECURITY_E2E: '1' },
  })
  try {
    const page = await app.firstWindow()
    await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible()
    expect(await page.evaluate(() => typeof (globalThis as { process?: unknown }).process)).toBe('undefined')
    expect(await page.evaluate(() => {
      const ancestry = (window as unknown as { ancestry: object }).ancestry
      return ['securityState', 'openExternalLink'].filter((key) => key in ancestry)
    })).toEqual([])
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
    await page.getByRole('link', { name: 'Diagnostics' }).press('Enter')
    await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeVisible()
    await expect(page.getByRole('main').getByText('Ready', { exact: true })).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await page.getByRole('link', { name: 'Settings' }).click()
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeFocused()
  } finally { await app.close() }
})
