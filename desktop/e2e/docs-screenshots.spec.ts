/** Captures each manifest-declared Electron documentation state twice for exact comparison. */

import { createRequire } from 'node:module'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import {
  _electron as electron,
  expect,
  test,
  type ElectronApplication,
  type Page,
} from '@playwright/test'
import {
  assertExactCapture,
  assertNoUnexpectedNetwork,
  assertPlanCaptureIsPrivate,
  assertTrustedElectronResolution,
  captureDeterminismStyles,
  captureRuntimeEnvironment,
  declaredFixtureContent,
  electronLaunchArguments,
  loadElectronCapturePlan,
  publishCaptureAtomically,
  requireCaptureOutputRoot,
  selectElectronCaptureScenarios,
  type ElectronCapturePlan,
  type ElectronCaptureScenario,
} from './docs-screenshot-capture'
import { APP_ENTRY_URL as TRUSTED_RENDERER_URL } from '../src/main/security-policy'

const desktopRoot = process.cwd()
const repositoryRoot = resolve(desktopRoot, '..')
const loadNodeModule = createRequire(import.meta.url)
assertTrustedElectronResolution(process.env.ELECTRON_OVERRIDE_DIST_PATH)
const electronExecutablePath = loadNodeModule('electron') as string
const fontPath = resolve(
  desktopRoot,
  'node_modules/@fontsource/inter/files/inter-latin-400-normal.woff2',
)

test('captures the declared Electron documentation states deterministically', async () => {
  test.setTimeout(180_000)
  const outputRoot = requireCaptureOutputRoot(
    process.env.ANCESTRYLLM_DOCS_SCREENSHOT_OUTPUT_ROOT,
  )
  const plan = await loadElectronCapturePlan({
    repositoryRoot,
    outputRoot,
    electronExecutablePath,
    fontPath,
  })
  const scenarios = selectElectronCaptureScenarios(
    plan,
    process.env.ANCESTRYLLM_DOCS_SCREENSHOT_SCENARIOS,
  )

  for (const scenario of scenarios) {
    const first = await captureScenario(plan, scenario)
    const second = await captureScenario(plan, scenario)
    assertExactCapture(first, second)
    await publishCaptureAtomically(plan, scenario.outputPath, first)
  }
})

async function captureScenario(
  plan: ElectronCapturePlan,
  scenario: Readonly<ElectronCaptureScenario>,
): Promise<Buffer> {
  const userDataDirectory = await mkdtemp(join(tmpdir(), 'ancestryllm-docshot-electron-'))
  let app: ElectronApplication | undefined
  try {
    app = await electron.launch({
      executablePath: electronExecutablePath,
      cwd: desktopRoot,
      args: [...electronLaunchArguments(scenario.geometry, userDataDirectory)],
      env: captureRuntimeEnvironment(plan, scenario, userDataDirectory),
    })

    const unexpectedNetwork = new Set<string>()
    const context = app.context()
    context.on('request', (request) => {
      if (isNetworkUrl(request.url())) unexpectedNetwork.add(request.url())
    })
    await context.route(/^https?:\/\//, async (route) => {
      unexpectedNetwork.add(route.request().url())
      await route.abort('blockedbyclient')
    })
    await context.routeWebSocket(/^wss?:\/\//, async (webSocket) => {
      unexpectedNetwork.add(webSocket.url())
      await webSocket.close({
        code: 1008,
        reason: 'Documentation capture is network-free.',
      })
    })

    const page = await app.firstWindow()
    await page.waitForURL(TRUSTED_RENDERER_URL, { waitUntil: 'load' })
    await configureWindow(app, page, plan, scenario)
    await assertNoNetworkActivity(page, unexpectedNetwork)

    if (scenario.fixture.state === 'success') {
      await prepareReadyHome(page, scenario)
    } else {
      await prepareDegradedDiagnostics(page, scenario)
    }

    const main = page.getByRole('main')
    for (const value of declaredFixtureContent(scenario)) {
      await expect(main.getByText(value, { exact: true })).toBeVisible()
    }

    await assertNoNetworkActivity(page, unexpectedNetwork)
    const capturedDom = await page.evaluate(() => document.documentElement.outerHTML)
    assertPlanCaptureIsPrivate(plan, capturedDom)
    const screenshot = await page.screenshot({
      animations: 'disabled',
      caret: 'hide',
      scale: 'css',
      type: 'png',
    })
    await assertNoNetworkActivity(page, unexpectedNetwork)
    return screenshot
  } finally {
    await app?.close().catch(() => undefined)
    await rm(userDataDirectory, { force: true, recursive: true })
  }
}

async function configureWindow(
  app: ElectronApplication,
  page: Page,
  plan: ElectronCapturePlan,
  scenario: Readonly<ElectronCaptureScenario>,
): Promise<void> {
  await app.evaluate(({ BrowserWindow }, geometry) => {
    const window = BrowserWindow.getAllWindows()[0]
    if (!window) throw new Error('Documentation capture BrowserWindow is unavailable.')
    window.setContentSize(geometry.width, geometry.height)
    window.webContents.setZoomFactor(1)
  }, scenario.geometry)
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' })
  await page.clock.setFixedTime(plan.determinism.fixedTimestamp)

  const fontBytes = await readFile(fontPath)
  const determinismStyles = captureDeterminismStyles(plan.determinism.font)
  await page.evaluate(async ({ determinismStyles, fontBase64, font, theme }) => {
    const binary = atob(fontBase64)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index)
    }
    const face = new FontFace(font.family, bytes.buffer, {
      style: 'normal',
      weight: String(font.weight),
    })
    await face.load()
    document.fonts.add(face)

    const style = document.createElement('style')
    style.dataset.docsScreenshotDeterminism = 'true'
    style.textContent = determinismStyles
    document.head.append(style)
    document.documentElement.dataset.theme = theme
    document.documentElement.dataset.reducedMotion = 'true'
    document.documentElement.style.colorScheme = theme
    await document.fonts.ready
    if (!document.fonts.check(`${font.weight} ${font.sizePx}px ${JSON.stringify(font.family)}`)) {
      throw new Error('Bundled documentation capture font did not load.')
    }
  }, {
    determinismStyles,
    fontBase64: fontBytes.toString('base64'),
    font: plan.determinism.font,
    theme: plan.determinism.theme,
  })

  await expect.poll(() => page.evaluate(() => ({
    deviceScaleFactor: window.devicePixelRatio,
    height: window.innerHeight,
    locale: navigator.language,
    theme: document.documentElement.dataset.theme,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    width: window.innerWidth,
  }))).toEqual({
    deviceScaleFactor: scenario.geometry.deviceScaleFactor,
    height: scenario.geometry.height,
    locale: 'en-US',
    theme: plan.determinism.theme,
    timezone: plan.determinism.timezone,
    width: scenario.geometry.width,
  })
}

async function prepareReadyHome(
  page: Page,
  scenario: Readonly<ElectronCaptureScenario>,
): Promise<void> {
  const main = page.getByRole('main')
  await expect(page.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeFocused()
  await expect(main.getByText(/No account, provider, API key, genealogy data, or cloud consent is requested here/i)).toBeVisible()

  const providerPosture = await page.evaluate(async () => {
    const bridge = (window as unknown as {
      ancestry: {
        getProviderConfiguration: () => Promise<{
          ok: boolean
          data?: { profiles: readonly unknown[] }
        }>
        getSettings: () => Promise<{
          ok: boolean
          data?: { fields: readonly { key: string, value: unknown }[] }
        }>
      }
    }).ancestry
    const [settings, configuration] = await Promise.all([
      bridge.getSettings(),
      bridge.getProviderConfiguration(),
    ])
    const defaultProvider = settings.ok
      ? settings.data?.fields.find((field) => field.key === 'providers.default')?.value
      : undefined
    return {
      configurationOk: configuration.ok,
      defaultProvider,
      profileCount: configuration.ok ? configuration.data?.profiles.length : undefined,
      settingsOk: settings.ok,
    }
  })
  expect(providerPosture).toEqual({
    configurationOk: true,
    defaultProvider: 'none',
    profileCount: 0,
    settingsOk: true,
  })

  await page.getByRole('button', { name: 'Continue to Home' }).click()
  await expect(page.getByRole('heading', { name: 'Home', exact: true })).toBeFocused()
  await expect(main.getByText(scenario.readySignal.value, { exact: true })).toBeVisible()
  await expect(main.getByText('Ready', { exact: true })).toBeVisible()
  await expect(main.getByText('No control capabilities are currently available.', { exact: true })).toBeVisible()
}

async function prepareDegradedDiagnostics(
  page: Page,
  scenario: Readonly<ElectronCaptureScenario>,
): Promise<void> {
  const main = page.getByRole('main')
  await page.getByRole('link', { name: 'Diagnostics', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Diagnostics', exact: true })).toBeFocused()
  await expect(main.getByText('Degraded', { exact: true })).toBeVisible()
  await expect(main.getByRole('alert')).toContainText(scenario.readySignal.value)
}

function isNetworkUrl(url: string): boolean {
  return /^(?:https?|wss?):\/\//.test(url)
}

async function assertNoNetworkActivity(page: Page, observed: ReadonlySet<string>): Promise<void> {
  const rendererResources = await page.evaluate(() => [
    document.location.href,
    ...performance.getEntriesByType('resource').map((entry) => entry.name),
  ])
  assertNoUnexpectedNetwork([
    ...observed,
    ...rendererResources.filter((url) => isNetworkUrl(url)),
  ])
}
