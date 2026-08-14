// Verifies Electron documentation captures fail closed and publish atomically.

import {
  copyFile,
  lstat,
  mkdtemp,
  mkdir,
  readFile,
  rename,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { afterEach, describe, expect, test } from 'vitest'
import {
  DocsScreenshotCaptureError,
  assertCaptureIsPrivate,
  assertExactCapture,
  assertNoUnexpectedNetwork,
  assertTrustedElectronResolution,
  captureDeterminismStyles,
  captureRuntimeEnvironment,
  declaredFixtureContent,
  electronLaunchArguments,
  loadElectronCapturePlan,
  publishCaptureAtomically,
  requireCaptureOutputRoot,
  selectElectronCaptureScenarios,
} from './docs-screenshot-capture'

const repositoryRoot = resolve(process.cwd(), '..')
const temporaryRoots: string[] = []

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-docshot-'))
  temporaryRoots.push(root)
  return root
}

async function fixtureDependencies(root: string): Promise<{
  electronExecutablePath: string
  fontPath: string
  outputRoot: string
}> {
  const electronExecutablePath = join(root, 'electron')
  const fontPath = join(root, 'inter.woff2')
  const outputRoot = join(root, 'output')
  await writeFile(electronExecutablePath, 'fixture executable', { mode: 0o700 })
  await writeFile(fontPath, 'fixture font')
  await mkdir(outputRoot)
  return { electronExecutablePath, fontPath, outputRoot }
}

async function contractRepository(root: string): Promise<string> {
  const contractRoot = join(root, 'repository')
  const relativePaths = [
    'config/docs-screenshot-manifest.json',
    'config/docs-screenshot-manifest-v1.schema.json',
    'config/docs-screenshot-fixture-v1.schema.json',
    'tests/fixtures/docs_screenshots/success.json',
    'tests/fixtures/docs_screenshots/degraded.json',
    'tests/fixtures/docs_screenshots/electron-degraded.json',
    'tests/fixtures/docs_screenshots/privacy-canary.json',
  ]
  for (const relativePath of relativePaths) {
    const destination = join(contractRoot, relativePath)
    await mkdir(dirname(destination), { recursive: true })
    await copyFile(join(repositoryRoot, relativePath), destination)
  }
  return contractRoot
}

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map(async (root) => rm(root, {
    force: true,
    recursive: true,
  })))
})

describe('Electron documentation screenshot capture contract', () => {
  test('requires an explicitly supplied output root', () => {
    expect(() => requireCaptureOutputRoot(undefined)).toThrowError(
      expect.objectContaining({ code: 'DOCSHOT_OUTPUT_ROOT_MISSING' }),
    )
    expect(() => requireCaptureOutputRoot('   ')).toThrowError(
      expect.objectContaining({ code: 'DOCSHOT_OUTPUT_ROOT_MISSING' }),
    )

    const suppliedRoot = join(tmpdir(), 'ancestryllm-docshot-output')
    expect(requireCaptureOutputRoot(suppliedRoot)).toBe(resolve(suppliedRoot))
  })

  test('loads only declared Electron success and degraded scenarios', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)

    const plan = await loadElectronCapturePlan({ repositoryRoot, ...dependencies })

    expect(plan.scenarios.map((scenario) => scenario.id)).toEqual([
      'electron-degraded-diagnostics',
      'electron-ready-home',
    ])
    expect(plan.scenarios.map((scenario) => scenario.fixture.state)).toEqual([
      'degraded',
      'success',
    ])
    expect(JSON.stringify(plan)).not.toContain('SCREENSHOT-PRIVATE-CANARY-7F4C')
    expect(JSON.stringify(plan)).not.toContain(repositoryRoot)
  })

  test('selects declared Electron scenarios in manifest order and fails closed', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const plan = await loadElectronCapturePlan({ repositoryRoot, ...dependencies })

    expect(selectElectronCaptureScenarios(plan, undefined)).toEqual(plan.scenarios)
    expect(selectElectronCaptureScenarios(plan, '')).toEqual(plan.scenarios)
    expect(selectElectronCaptureScenarios(
      plan,
      'electron-ready-home,electron-degraded-diagnostics',
    ).map(({ id }) => id)).toEqual([
      'electron-degraded-diagnostics',
      'electron-ready-home',
    ])
    for (const selection of [
      'electron-ready-home,',
      'electron-ready-home,electron-ready-home',
      'electron-not-declared',
    ]) {
      expect(() => selectElectronCaptureScenarios(plan, selection)).toThrowError(
        expect.objectContaining({ code: 'DOCSHOT_SCENARIO_SELECTION_INVALID' }),
      )
    }
  })

  test('derives the Electron scale factor and universal font from the capture plan', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const plan = await loadElectronCapturePlan({ repositoryRoot, ...dependencies })
    const scenario = plan.scenarios[0]
    if (scenario === undefined) throw new Error('fixture scenario missing')

    expect(electronLaunchArguments({
      ...scenario.geometry,
      deviceScaleFactor: 2,
    }, join(root, 'profile'))).toContain('--force-device-scale-factor=2')

    const styles = captureDeterminismStyles(plan.determinism.font)
    expect(styles).toContain('*, *::before, *::after {')
    expect(styles).toContain('font-family: "Inter", sans-serif !important;')
    expect(styles).not.toContain('html, body, button, input, select, textarea')
  })

  test('rejects an Electron executable override before package resolution', async () => {
    expect(() => assertTrustedElectronResolution('/tmp/untrusted-electron')).toThrowError(
      expect.objectContaining({ code: 'DOCSHOT_BINARY_UNTRUSTED' }),
    )
    expect(() => assertTrustedElectronResolution('')).toThrowError(
      expect.objectContaining({ code: 'DOCSHOT_BINARY_UNTRUSTED' }),
    )
    expect(() => assertTrustedElectronResolution(undefined)).not.toThrow()

    const captureSource = await readFile(
      join(process.cwd(), 'e2e/docs-screenshots.spec.ts'),
      'utf8',
    )
    const trustCheck = captureSource.indexOf(
      'assertTrustedElectronResolution(process.env.ELECTRON_OVERRIDE_DIST_PATH)',
    )
    const packageResolution = captureSource.indexOf("loadNodeModule('electron')")
    expect(trustCheck).toBeGreaterThan(-1)
    expect(packageResolution).toBeGreaterThan(trustCheck)
  })

  test('rejects duplicate Electron destinations and tolerance comparisons', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const copiedRepository = await contractRepository(root)
    const manifestPath = join(copiedRepository, 'config/docs-screenshot-manifest.json')
    const source = await readFile(manifestPath, 'utf8')
    const manifest = JSON.parse(source) as {
      scenarios: Array<{
        id: string
        output_path: string
        comparison: Record<string, unknown>
      }>
    }
    const ready = manifest.scenarios.find(({ id }) => id === 'electron-ready-home')
    const degraded = manifest.scenarios.find(
      ({ id }) => id === 'electron-degraded-diagnostics',
    )
    if (ready === undefined || degraded === undefined) {
      throw new Error('Electron fixture scenarios missing')
    }

    degraded.output_path = ready.output_path
    await writeFile(manifestPath, JSON.stringify(manifest))
    await expect(loadElectronCapturePlan({
      repositoryRoot: copiedRepository,
      ...dependencies,
    })).rejects.toMatchObject({ code: 'DOCSHOT_MANIFEST_INVALID' })

    const toleranceManifest = JSON.parse(source) as typeof manifest
    const toleranceScenario = toleranceManifest.scenarios.find(
      ({ id }) => id === 'electron-ready-home',
    )
    if (toleranceScenario === undefined) throw new Error('ready-home fixture scenario missing')
    toleranceScenario.comparison = {
      mode: 'tolerance',
      max_differing_pixels: 1,
      rationale: 'Test-only unsupported comparison',
    }
    await writeFile(manifestPath, JSON.stringify(toleranceManifest))
    await expect(loadElectronCapturePlan({
      repositoryRoot: copiedRepository,
      ...dependencies,
    })).rejects.toMatchObject({ code: 'DOCSHOT_MANIFEST_INVALID' })
  })

  test('preserves only required host runtime locators', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const plan = await loadElectronCapturePlan({ repositoryRoot, ...dependencies })
    const scenario = plan.scenarios[0]
    if (scenario === undefined) throw new Error('fixture scenario missing')

    const environment = captureRuntimeEnvironment(
      plan,
      scenario,
      join(root, 'profile'),
      'linux',
      {
        DISPLAY: ':1',
        OPENAI_API_KEY: 'must-not-leak',
        XAUTHORITY: '/tmp/xauthority',
      },
    )
    expect(environment).toMatchObject({
      ANCESTRYLLM_DESKTOP_FIXTURE: scenario.fixture.state,
      DISPLAY: ':1',
      XAUTHORITY: '/tmp/xauthority',
    })
    expect(environment).not.toHaveProperty('OPENAI_API_KEY')
  })

  test('exposes every nonblank declared fixture value for renderer parity', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const plan = await loadElectronCapturePlan({ repositoryRoot, ...dependencies })
    const ready = plan.scenarios.find(({ fixture }) => fixture.state === 'success')
    const degraded = plan.scenarios.find(({ fixture }) => fixture.state === 'degraded')
    if (ready === undefined || degraded === undefined) {
      throw new Error('Electron fixture scenarios missing')
    }

    expect(declaredFixtureContent(ready)).toEqual([
      'Home',
      'Ready',
      'Local desktop shell',
      'No control capabilities are currently available.',
    ])
    expect(declaredFixtureContent(degraded)).toEqual([
      'Diagnostics',
      'Degraded',
      'The desktop service did not start.',
    ])
  })

  test('capture source blocks WebSockets and waits for the trusted renderer', async () => {
    const captureSource = await readFile(
      join(process.cwd(), 'e2e/docs-screenshots.spec.ts'),
      'utf8',
    )
    expect(captureSource).toContain('context.routeWebSocket(')
    expect(captureSource).toContain('await page.waitForURL(TRUSTED_RENDERER_URL')
    expect(captureSource).toContain('declaredFixtureContent(scenario)')
    expect(captureSource).not.toContain('.connectToServer(')
  })

  test.each([
    ['DOCSHOT_OUTPUT_ROOT_MISSING', 'outputRoot'],
    ['DOCSHOT_BINARY_MISSING', 'electronExecutablePath'],
    ['DOCSHOT_FONT_MISSING', 'fontPath'],
  ] as const)('fails closed with %s', async (code, missingDependency) => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const missingPath = join(root, 'missing')

    await expect(loadElectronCapturePlan({
      repositoryRoot,
      ...dependencies,
      [missingDependency]: missingPath,
    })).rejects.toMatchObject({ code })
  })

  test('rejects missing and symlinked fixture inputs with stable codes', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const copiedRepository = await contractRepository(root)
    const successFixture = join(
      copiedRepository,
      'tests/fixtures/docs_screenshots/success.json',
    )

    await rm(successFixture)
    await expect(loadElectronCapturePlan({
      repositoryRoot: copiedRepository,
      ...dependencies,
    })).rejects.toMatchObject({ code: 'DOCSHOT_FIXTURE_MISSING' })

    const symlinkTarget = join(root, 'success-fixture.json')
    await copyFile(join(repositoryRoot, 'tests/fixtures/docs_screenshots/success.json'), symlinkTarget)
    await symlink(symlinkTarget, successFixture)
    await expect(loadElectronCapturePlan({
      repositoryRoot: copiedRepository,
      ...dependencies,
    })).rejects.toMatchObject({ code: 'DOCSHOT_FIXTURE_INVALID' })
  })

  test('rejects a regular fixture reached through a symlinked parent', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const copiedRepository = await contractRepository(root)
    const manifestPath = join(copiedRepository, 'config/docs-screenshot-manifest.json')
    const manifest = JSON.parse(await readFile(manifestPath, 'utf8')) as {
      fixtures: Array<{ id: string; path: string }>
    }
    const success = manifest.fixtures.find(({ id }) => id === 'success')
    if (success === undefined) throw new Error('success fixture descriptor missing')

    const externalFixtures = join(root, 'external-fixtures')
    await mkdir(externalFixtures)
    await copyFile(
      join(repositoryRoot, 'tests/fixtures/docs_screenshots/success.json'),
      join(externalFixtures, 'success.json'),
    )
    await symlink(externalFixtures, join(copiedRepository, 'fixture-link'), 'dir')
    success.path = 'fixture-link/success.json'
    await writeFile(manifestPath, JSON.stringify(manifest))

    await expect(loadElectronCapturePlan({
      repositoryRoot: copiedRepository,
      ...dependencies,
    })).rejects.toMatchObject({ code: 'DOCSHOT_FIXTURE_INVALID' })
  })

  test('rejects a ready signal absent from its declared fictional fixture', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const copiedRepository = await contractRepository(root)
    const manifestPath = join(copiedRepository, 'config/docs-screenshot-manifest.json')
    const manifest = JSON.parse(await readFile(manifestPath, 'utf8')) as {
      scenarios: Array<{ id: string; ready_signal: { value: string } }>
    }
    const scenario = manifest.scenarios.find(({ id }) => id === 'electron-ready-home')
    if (scenario === undefined) throw new Error('ready-home fixture scenario missing')
    scenario.ready_signal.value = 'Missing fixture signal'
    await writeFile(manifestPath, JSON.stringify(manifest))

    await expect(loadElectronCapturePlan({
      repositoryRoot: copiedRepository,
      ...dependencies,
    })).rejects.toMatchObject({ code: 'DOCSHOT_READY_SIGNAL_INVALID' })
  })

  test('rejects privacy canaries and unexpected network without exposing inputs', () => {
    const canary = 'SCREENSHOT-PRIVATE-CANARY-7F4C'
    const privateText = `Visible response: ${canary}`
    const privateFailure = captureFailure(() => assertCaptureIsPrivate(privateText, [canary]))
    expect(privateFailure.code).toBe('DOCSHOT_PRIVACY_CANARY_LEAKED')
    expect(privateFailure.message).not.toContain(canary)

    const privateUrl = 'https://example.invalid/path?token=private'
    const networkFailure = captureFailure(() => assertNoUnexpectedNetwork([privateUrl]))
    expect(networkFailure.code).toBe('DOCSHOT_NETWORK_UNEXPECTED')
    expect(networkFailure.message).not.toContain(privateUrl)
  })

  test('requires byte-identical repeats and publishes only a declared destination', async () => {
    expect(() => assertExactCapture(Buffer.from('first'), Buffer.from('second'))).toThrowError(
      expect.objectContaining({ code: 'DOCSHOT_CAPTURE_MISMATCH' }),
    )

    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const plan = await loadElectronCapturePlan({ repositoryRoot, ...dependencies })
    const scenario = plan.scenarios[0]
    if (scenario === undefined) throw new Error('fixture scenario missing')

    const destination = await publishCaptureAtomically(plan, scenario.outputPath, Buffer.from('png'))
    expect(destination).toBe(join(dependencies.outputRoot, scenario.outputPath))

    await expect(publishCaptureAtomically(
      plan,
      'docs/assets/screenshots/electron/undeclared.png',
      Buffer.from('png'),
    )).rejects.toMatchObject({ code: 'DOCSHOT_OUTPUT_UNDECLARED' })
  })

  test('rejects a symlinked output parent before creating external descendants', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const plan = await loadElectronCapturePlan({ repositoryRoot, ...dependencies })
    const scenario = plan.scenarios[0]
    if (scenario === undefined) throw new Error('fixture scenario missing')

    const externalRoot = join(root, 'external-output')
    await mkdir(externalRoot)
    await symlink(externalRoot, join(dependencies.outputRoot, 'docs'), 'dir')

    await expect(publishCaptureAtomically(
      plan,
      scenario.outputPath,
      Buffer.from('png'),
    )).rejects.toMatchObject({ code: 'DOCSHOT_OUTPUT_UNDECLARED' })
    await expect(lstat(join(externalRoot, 'assets'))).rejects.toMatchObject({ code: 'ENOENT' })
  })

  test('rejects an output root replaced by a symlink before creating descendants', async () => {
    const root = await temporaryRoot()
    const dependencies = await fixtureDependencies(root)
    const plan = await loadElectronCapturePlan({ repositoryRoot, ...dependencies })
    const scenario = plan.scenarios[0]
    if (scenario === undefined) throw new Error('fixture scenario missing')

    await rename(dependencies.outputRoot, join(root, 'original-output'))
    const externalRoot = join(root, 'external-output')
    await mkdir(externalRoot)
    await symlink(externalRoot, dependencies.outputRoot, 'dir')

    await expect(publishCaptureAtomically(
      plan,
      scenario.outputPath,
      Buffer.from('png'),
    )).rejects.toMatchObject({ code: 'DOCSHOT_OUTPUT_UNDECLARED' })
    await expect(lstat(join(externalRoot, 'docs'))).rejects.toMatchObject({ code: 'ENOENT' })
  })
})

function captureFailure(action: () => void): DocsScreenshotCaptureError {
  try {
    action()
  } catch (error) {
    if (error instanceof DocsScreenshotCaptureError) return error
    throw error
  }
  throw new Error('expected capture failure')
}
