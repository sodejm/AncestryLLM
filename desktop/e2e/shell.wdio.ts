/** Verifies the source-built Electron shell through the WebdriverIO service. */
import assert from 'node:assert/strict'
import { realpathSync } from 'node:fs'
import { $, $$, browser } from '@wdio/globals'
import '@wdio/native-types'
import axe from 'axe-core'
import { PRODUCTION_CSP } from '../src/main/security-policy'
import { bridgeMethods } from './bridge-contract'

const visible = async (selector: string) => {
  const element = await $(selector)
  await element.waitForDisplayed()
  return element
}

const click = async (selector: string) => {
  const element = await visible(selector)
  await element.click()
}

const text = async (selector: string) => (await visible(selector)).getText()

const labeledInput = async (label: string) => visible(
  `//label[normalize-space(.)="${label}"]//input`,
)

async function expectFocusedHeading(name: string) {
  try {
    await browser.waitUntil(async () => browser.execute((expected) => {
      const active = document.activeElement
      return active?.matches('h1, h2, h3') === true && active.textContent?.trim() === expected
    }, name), { timeoutMsg: `Expected ${name} heading to receive focus` })
  } catch {
    const state = await browser.execute(() => ({
      url: location.href,
      readyState: document.readyState,
      activeElement: document.activeElement
        ? `${document.activeElement.tagName}:${document.activeElement.textContent?.trim() ?? ''}`
        : null,
      headings: Array.from(document.querySelectorAll('h1, h2, h3'), (heading) => (
        `${heading.tagName}:${heading.textContent?.trim() ?? ''}`
      )),
    }))
    assert.fail(`Expected ${name} heading to receive focus; renderer state: ${JSON.stringify(state)}`)
  }
}

async function expectIsolatedUserData() {
  const expected = process.env.ANCESTRYLLM_WDIO_USER_DATA
  assert.ok(expected, 'ANCESTRYLLM_WDIO_USER_DATA must identify the isolated Electron profile')
  const actual = await browser.electron.execute((electron) => electron.app.getPath('userData'))
  assert.equal(realpathSync(actual), realpathSync(expected))
}

async function expectProductionNavigation() {
  const labels = await browser.execute(() => Array.from(
    document.querySelectorAll<HTMLElement>('nav[aria-label="Primary"] a'),
    (link) => link.textContent?.trim(),
  ))
  assert.deepEqual(labels, ['Home', 'Chat', 'Tasks', 'Diagnostics', 'Settings'])
}

async function expectNoUnsupportedSurfaces(allowProviderSettings = false) {
  const mainText = await text('main')
  const prohibited = [
    /Component gallery/i,
    /Primary action/i,
    /Quiet action/i,
    /\baccounts?\b/i,
    /\bjobs?\b/i,
    /\bchat\b/i,
    /\bupdaters?\b/i,
  ]
  if (!allowProviderSettings) prohibited.push(/\bgenealogy\b/i, /\bcloud\b/i, /\bproviders?\b/i)
  for (const pattern of prohibited) assert.doesNotMatch(mainText, pattern)
}

async function expectNoAccessibilityViolations() {
  await browser.execute(axe.source)
  const violations = await browser.execute(async () => {
    const axeRunner = (globalThis as unknown as {
      axe: {
        run(
          context: Document,
          options: { runOnly: { type: 'tag'; values: string[] } },
        ): Promise<{ violations: Array<{ id: string; impact: string | null; nodes: unknown[] }> }>
      }
    }).axe
    const result = await axeRunner.run(document, {
      runOnly: {
        type: 'tag',
        values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'],
      },
    })
    return result.violations.map(({ id, impact, nodes }) => ({ id, impact, nodes }))
  })
  assert.deepEqual(violations, [])
}

async function expectNoHorizontalClipping() {
  const layout = await browser.execute(() => {
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
  assert.ok(layout.viewportWidth >= 340)
  assert.ok(layout.viewportWidth <= 365)
  assert.ok(layout.documentWidth <= layout.viewportWidth)
  assert.deepEqual(layout.clippedControls, [])
}

async function expectBoundedBridgeAndSecurity() {
  assert.equal(await browser.execute(() => typeof (globalThis as { process?: unknown }).process), 'undefined')
  assert.deepEqual(await browser.execute(() => {
    const ancestry = (window as unknown as { ancestry: object }).ancestry
    return {
      frozen: Object.isFrozen(ancestry),
      methods: Object.keys(ancestry).sort(),
    }
  }), { frozen: true, methods: bridgeMethods })

  const securityState = await browser.electron.execute(async (electron) => {
    const window = electron.BrowserWindow.getAllWindows()[0]
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
      }
    }).getLastWebPreferences()
    const response = await electron.net.fetch(window.webContents.getURL())
    return {
      rendererUrl: window.webContents.getURL(),
      contentSecurityPolicy: response.headers.get('content-security-policy'),
      contextIsolation: preferences.contextIsolation,
      nodeIntegration: preferences.nodeIntegration,
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
  assert.deepEqual(securityState, {
    rendererUrl: 'app://bundle/index.html',
    contentSecurityPolicy: PRODUCTION_CSP,
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

  const deniedCapabilities = await browser.execute(async () => {
    let fetchBlocked = false
    try { await fetch('https://example.invalid/') } catch { fetchBlocked = true }

    const webSocketBlocked = await new Promise<boolean>((resolve) => {
      const target = 'wss://example.invalid/'
      let settled = false
      let socket: WebSocket | undefined
      function finish(blocked: boolean): void {
        if (settled) return
        settled = true
        clearTimeout(timer)
        document.removeEventListener('securitypolicyviolation', onViolation)
        if (socket && socket.readyState !== WebSocket.CLOSED) socket.close()
        resolve(blocked)
      }
      function onViolation(event: SecurityPolicyViolationEvent): void {
        if (
          event.disposition === 'enforce'
          && event.effectiveDirective === 'connect-src'
          && event.blockedURI.startsWith('wss://example.invalid')
        ) finish(true)
      }
      const timer = setTimeout(() => finish(false), 1_500)
      document.addEventListener('securitypolicyviolation', onViolation)
      try {
        socket = new WebSocket(target)
        socket.addEventListener('error', () => undefined, { once: true })
        socket.addEventListener('open', () => finish(false), { once: true })
      } catch {
        // Constructor errors alone do not prove CSP enforcement.
      }
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
      externalResources: performance.getEntriesByType('resource')
        .map((entry) => entry.name)
        .filter((url) => /^(?:https?|wss?):/i.test(url)),
    }
  })
  assert.deepEqual(deniedCapabilities, {
    fetchBlocked: true,
    webSocketBlocked: true,
    serviceWorkerBlocked: true,
    childWindowBlocked: true,
    externalResources: [],
  })
}

describe('source-built desktop shell', () => {
  it('built shell exposes the bounded production Home, Chat, Tasks, Diagnostics, and Settings surfaces', async () => {
    await expectIsolatedUserData()
    await expectFocusedHeading('Welcome to AncestryLLM')
    await expectProductionNavigation()
    assert.match(await text('main'), /Your desktop control shell stays local to this device\./u)
    assert.match(await text('main'), /No account, provider, API key, genealogy data, or cloud consent is requested here/iu)
    assert.match(await text('main'), /Updates are installed manually/iu)
    assert.equal(await (await $('a=Open Diagnostics')).getAttribute('href'), '#/diagnostics')
    await click('button=Continue to Home')

    await expectFocusedHeading('Home')
    const homeText = await text('main')
    for (const expected of [
      'Application',
      'AncestryLLM',
      '0.6.0-dev',
      'Offline posture',
      'Startup state',
      'Ready',
      'Capabilities',
      'No control capabilities are currently available.',
    ]) assert.match(homeText, new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&'), 'u'))
    await expectNoUnsupportedSurfaces()
    await expectBoundedBridgeAndSecurity()

    // The source build intentionally aliases the production runtime bridge to
    // one deterministic in-memory fixture. A renderer refresh verifies the
    // same bridge-owned persistence contract without restarting that fixture;
    // the packaged suite separately proves file persistence across sessions.
    await browser.refresh()
    await expectIsolatedUserData()
    await expectFocusedHeading('Home')
    await (await $('button=Review welcome')).waitForDisplayed()
    assert.equal(await $$('h1=Welcome to AncestryLLM').length, 0)

    await click('a=Diagnostics')
    await expectFocusedHeading('Diagnostics')
    assert.match(await text('main'), /Desktop service[\s\S]*Ready/u)
    assert.equal(await $$('[role="alert"]').length, 0)
    await expectNoUnsupportedSurfaces()

    await click('a=Settings')
    await expectFocusedHeading('Settings')
    const themeRadios = await $$('fieldset input[type="radio"]')
    assert.equal(themeRadios.length, 3)
    assert.equal(await (await labeledInput('system')).isSelected(), true)
    const reducedMotion = await labeledInput('Reduce motion')
    assert.equal(await reducedMotion.isSelected(), false)
    await reducedMotion.click()
    await browser.waitUntil(async () => browser.execute(
      () => document.documentElement.dataset.reducedMotion === 'true',
    ))
    assert.equal(await reducedMotion.isSelected(), true)

    for (const titleId of [
      'general-settings-title',
      'storage-settings-title',
      'local-providers-title',
      'cloud-providers-title',
      'consent-title',
      'privacy-settings-title',
      'limits-title',
      'secrets-title',
    ]) await visible(`[aria-labelledby="${titleId}"]`)

    const deploymentText = await text('[aria-labelledby="deployment-mode-title"]')
    assert.match(deploymentText, /Local Desktop/u)
    assert.match(deploymentText, /Connect Remote/u)
    assert.match(deploymentText, /Host Remote/u)
    assert.equal((deploymentText.match(/Not available in this release/gu) ?? []).length, 2)

    await (await visible('#local-profile-name')).setValue('private-local')
    await (await visible('#local-model')).setValue('fictional-local-model')
    const saveLocal = await $('button=Save local provider profile')
    assert.equal(await saveLocal.isEnabled(), false)
    await click('button=Test local provider endpoint')
    await browser.waitUntil(async () => (await text('[aria-labelledby="local-providers-title"]'))
      .includes('Endpoint tested: reachable on this device.'))
    await browser.waitUntil(async () => saveLocal.isEnabled())
    await saveLocal.click()
    await visible('h3=private-local')

    await (await visible('#cloud-profile-name')).setValue('reviewed-cloud')
    await (await visible('#cloud-model')).setValue('fictional-cloud-model')
    const saveCloud = await $('button=Save cloud provider profile')
    assert.equal(await saveCloud.isEnabled(), false)
    await click('button=Test cloud provider endpoint')
    await browser.waitUntil(async () => (await text('[aria-labelledby="cloud-providers-title"]'))
      .includes('Endpoint tested: reviewed remote destination is reachable.'))
    await browser.waitUntil(async () => saveCloud.isEnabled())
    await saveCloud.click()
    await visible('h3=reviewed-cloud')

    await (await visible('#consent-name')).setValue('reviewed-consent')
    await (await visible('#consent-profile')).selectByAttribute('value', 'reviewed-cloud')
    await (await labeledInput('Living person')).click()
    await (await visible('#consent-max-cost')).setValue('1.25')
    await (await labeledInput('Allow provider retention')).click()
    const saveConsent = await $('button=Save consent')
    assert.equal(await saveConsent.isEnabled(), false)
    await click('button=Review consent')
    await browser.waitUntil(async () => (await text('[aria-labelledby="consent-review-title"]'))
      .includes('Budget: $1.25 USD'))
    const reviewText = await text('[aria-labelledby="consent-review-title"]')
    for (const expected of [
      'Provider: openai',
      'Profile: reviewed-cloud',
      'Model: fictional-cloud-model',
      'Purpose: genealogy-analysis',
      'Data classes: Living person',
      'Retention: Allowed',
      'Budget: $1.25 USD',
      'Living-person data will leave this device.',
      'This provider endpoint is remote.',
      'The remote provider may retain payloads.',
    ]) assert.ok(reviewText.includes(expected), `Missing consent review text: ${expected}`)
    await browser.waitUntil(async () => saveConsent.isEnabled())
    await saveConsent.click()
    await visible('h3=reviewed-consent')
    await browser.waitUntil(async () => (await text('[aria-labelledby="consent-title"]')).includes('Active'))
    await click('button=Revoke reviewed-consent')
    await browser.waitUntil(async () => (await text('[aria-labelledby="consent-title"]')).includes('Revoked'))
    await expectNoUnsupportedSurfaces(true)
  })

  it('task center streams one safe cancellation lifecycle and reloads the terminal backend snapshot', async () => {
    await click('a=Tasks')
    await expectFocusedHeading('Tasks')
    const main = await visible('main')
    assert.match(await main.getText(), /Task activity/u)
    assert.equal(await $$('main [aria-label="Task activity"] [role="listitem"]').length, 2)
    assert.equal(await $$('main [aria-live="polite"]').length, 1)
    const activeTask = await visible('//article[@role="listitem"][.//h3[normalize-space()="Prepare fictional export"]]')
    const completedTask = await visible('//article[@role="listitem"][.//h3[normalize-space()="Review fictional matches"]]')
    const progress = await activeTask.$('progress[aria-label="Prepare fictional export progress"]')
    assert.equal(await progress.getAttribute('value'), '2')
    assert.equal(await progress.getAttribute('max'), '4')
    const completedText = await completedTask.getText()
    assert.match(completedText, /match-report/u)
    assert.match(completedText, /application\/json/u)
    assert.match(completedText, /Available through a grant-mediated product action\./u)
    const completedTaskButtons = await completedTask.$$('button')
    assert.equal(completedTaskButtons.length, 0)
    assert.doesNotMatch(await main.getText(), /art_[a-f0-9]{32}|[a-f0-9]{64}/iu)

    await browser.execute(() => {
      type TaskTrace = { statuses: string[]; announcements: string[]; safePointCopySeen: boolean }
      const task = Array.from(document.querySelectorAll<HTMLElement>('article[role="listitem"]'))
        .find((card) => card.querySelector('h3')?.textContent?.trim() === 'Prepare fictional export')
      const live = document.querySelector<HTMLElement>('main [aria-live="polite"]')
      if (!task || !live) throw new Error('Task trace targets are unavailable')
      const trace: TaskTrace = { statuses: [], announcements: [], safePointCopySeen: false }
      const capture = () => {
        const status = task.querySelector<HTMLElement>('.job-status')?.textContent?.trim() ?? ''
        const announcement = live.textContent?.trim() ?? ''
        if (status && trace.statuses.at(-1) !== status) trace.statuses.push(status)
        if (announcement && trace.announcements.at(-1) !== announcement) {
          trace.announcements.push(announcement)
        }
        if (task.textContent?.includes('Cancellation will happen after the current safe operation completes.')) {
          trace.safePointCopySeen = true
        }
      }
      capture()
      new MutationObserver(capture).observe(document.querySelector('main')!, {
        subtree: true,
        childList: true,
        characterData: true,
      })
      ;(window as unknown as { __ancestryTaskTrace: TaskTrace }).__ancestryTaskTrace = trace
    })

    await click('button[aria-label="Cancel Prepare fictional export"]')
    await browser.waitUntil(async () => (await main.getText()).includes('Cancelled'), {
      timeoutMsg: 'Expected the task cancellation to reach a terminal state',
    })
    const trace = await browser.execute(() => (
      window as unknown as {
        __ancestryTaskTrace: { statuses: string[]; announcements: string[]; safePointCopySeen: boolean }
      }
    ).__ancestryTaskTrace)
    const cancellingIndex = trace.statuses.indexOf('Cancelling')
    const waitingIndex = trace.statuses.indexOf('Waiting for a safe point')
    const cancelledIndex = trace.statuses.indexOf('Cancelled')
    assert.ok(cancellingIndex >= 0)
    assert.ok(waitingIndex > cancellingIndex)
    assert.ok(cancelledIndex > waitingIndex)
    assert.deepEqual(trace.announcements, [
      'Cancellation requested for Prepare fictional export.',
      'Cancellation for Prepare fictional export is waiting for a safe point.',
      'Prepare fictional export was cancelled.',
    ])
    assert.equal(trace.safePointCopySeen, true)
    const activeTaskButtons = await activeTask.$$('button')
    assert.equal(activeTaskButtons.length, 0)

    await browser.refresh()
    await expectFocusedHeading('Tasks')
    assert.match(await text('main'), /Prepare fictional export[\s\S]*Cancelled/u)
    assert.equal(await $$('main [aria-label="Task activity"] [role="listitem"]').length, 2)
    assert.equal(await $$('main [aria-live="polite"]').length, 1)
  })

  it('built degraded shell offers one bounded recovery and renders the ready result', async () => {
    await expectFocusedHeading('Welcome to AncestryLLM')
    await expectProductionNavigation()
    await click('a=Diagnostics')
    await expectFocusedHeading('Diagnostics')
    const recovery = await visible('[role="alert"]')
    assert.match(await recovery.getText(), /The desktop service did not start\./u)
    assert.doesNotMatch(await recovery.getText(), /startup_failed|SIDECAR_UNAVAILABLE|token|port|stderr|\.sock|\/Users\//iu)
    await click('button=Retry desktop service')
    await browser.waitUntil(async () => (await text('main')).includes('Ready'))
    assert.equal(await $$('[role="alert"]').length, 0)
    assert.equal(await $$('button=Retry desktop service').length, 0)
    await expectNoUnsupportedSurfaces()

    await click('a=Home')
    await expectFocusedHeading('Welcome to AncestryLLM')
    await click('button=Continue to Home')
    await expectFocusedHeading('Home')
    assert.match(await text('main'), /Startup state[\s\S]*Ready/u)
    assert.equal(await $$('[role="alert"]').length, 0)
    assert.equal(await $$('button=Retry desktop service').length, 0)
    await expectNoUnsupportedSurfaces()
  })

  it('built shell has deterministic skip-link and command-palette focus', async () => {
    await expectFocusedHeading('Welcome to AncestryLLM')
    const skipLink = await $('a=Skip to workspace')
    for (let attempt = 0; attempt < 8 && !(await skipLink.isFocused()); attempt += 1) {
      await browser.keys(['Shift', 'Tab'])
    }
    assert.equal(await skipLink.isFocused(), true)
    const initialHash = await browser.execute(() => window.location.hash)
    await browser.keys('Enter')
    assert.equal(await (await $('main')).isFocused(), true)
    assert.equal(await browser.execute(() => window.location.hash), initialHash)

    await browser.keys(['Control', 'k'])
    const filter = await $('input[placeholder="Filter destinations"]')
    await browser.waitUntil(async () => filter.isFocused())
    await browser.keys('Escape')
    const trigger = await $('button*=Navigate')
    await browser.waitUntil(async () => trigger.isFocused())
    await browser.keys('Enter')
    await browser.waitUntil(async () => filter.isFocused())
    await filter.setValue('diagnostics')
    await click('dialog.command-palette a[href="#/diagnostics"]')
    await expectFocusedHeading('Diagnostics')
    assert.equal(await browser.execute(() => window.location.hash), '#/diagnostics')
  })

  it('built shell passes automated WCAG checks across routes and explicit themes', async () => {
    await expectFocusedHeading('Welcome to AncestryLLM')
    await expectNoAccessibilityViolations()
    await click('button=Continue to Home')
    await expectNoAccessibilityViolations()
    for (const destination of ['Tasks', 'Diagnostics', 'Settings']) {
      await click(`a=${destination}`)
      await expectFocusedHeading(destination)
      await expectNoAccessibilityViolations()
    }
    await (await labeledInput('light')).click()
    await expectNoAccessibilityViolations()
    await (await labeledInput('dark')).click()
    await expectNoAccessibilityViolations()
  })

  it('minimum desktop window at 200 percent zoom keeps every action horizontally reachable', async () => {
    await browser.electron.execute((electron) => {
      const window = electron.BrowserWindow.getAllWindows()[0]
      if (!window) throw new Error('No BrowserWindow')
      window.setSize(720, 560)
      window.webContents.setZoomFactor(2)
    })
    await browser.waitUntil(async () => (await browser.execute(() => window.innerWidth)) <= 365)
    await expectNoHorizontalClipping()
    for (const destination of ['Tasks', 'Diagnostics', 'Settings']) {
      await click(`a=${destination}`)
      await expectNoHorizontalClipping()
    }
  })
})
