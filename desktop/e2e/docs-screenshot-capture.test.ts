import {
  copyFile,
  mkdtemp,
  mkdir,
  readFile,
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
  loadElectronCapturePlan,
  publishCaptureAtomically,
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
