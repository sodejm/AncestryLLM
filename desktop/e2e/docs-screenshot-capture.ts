// Enforces the deterministic, private Electron documentation-capture contract.

import { randomUUID } from 'node:crypto'
import { constants } from 'node:fs'
import {
  access,
  lstat,
  mkdir,
  open,
  readFile,
  realpath,
  rename,
  unlink,
} from 'node:fs/promises'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import type { AnySchema } from 'ajv'
import Ajv2020 from 'ajv/dist/2020.js'

const MANIFEST_PATH = 'config/docs-screenshot-manifest.json'
const MANIFEST_SCHEMA_PATH = 'config/docs-screenshot-manifest-v1.schema.json'
const FIXTURE_SCHEMA_PATH = 'config/docs-screenshot-fixture-v1.schema.json'
const ELECTRON_CAPTURE_COMMAND = Object.freeze([
  'pnpm',
  '--dir',
  'desktop',
  'capture:docs',
])
const SAFE_OUTPUT_PREFIX = 'docs/assets/screenshots/electron/'

type CaptureFailureCode =
  | 'DOCSHOT_BINARY_MISSING'
  | 'DOCSHOT_CAPTURE_MISMATCH'
  | 'DOCSHOT_FIXTURE_INVALID'
  | 'DOCSHOT_FIXTURE_MISSING'
  | 'DOCSHOT_FONT_MISSING'
  | 'DOCSHOT_MANIFEST_INVALID'
  | 'DOCSHOT_MANIFEST_MISSING'
  | 'DOCSHOT_NETWORK_UNEXPECTED'
  | 'DOCSHOT_OUTPUT_ROOT_MISSING'
  | 'DOCSHOT_OUTPUT_UNDECLARED'
  | 'DOCSHOT_OUTPUT_WRITE_FAILED'
  | 'DOCSHOT_PRIVACY_CANARY_LEAKED'
  | 'DOCSHOT_READY_SIGNAL_INVALID'
  | 'DOCSHOT_SCHEMA_INVALID'
  | 'DOCSHOT_SCHEMA_MISSING'

/** Stable, sanitized failure emitted by the documentation capture boundary. */
export class DocsScreenshotCaptureError extends Error {
  readonly code: CaptureFailureCode

  constructor(code: CaptureFailureCode) {
    super(`Documentation screenshot capture failed (${code}).`)
    this.name = 'DocsScreenshotCaptureError'
    this.code = code
  }
}

interface FixtureContent {
  readonly title: string
  readonly status: string
  readonly prompt: string
  readonly response: string
  readonly detail: string
}

interface FixturePayload {
  readonly schema_version: 1
  readonly fixture_id: string
  readonly state: 'success' | 'degraded' | 'privacy-canary'
  readonly provider: string
  readonly network: 'disabled' | 'enabled'
  readonly fictional: boolean
  readonly content: FixtureContent
  readonly privacy_canaries: readonly string[]
}

interface FixtureDescriptor {
  readonly id: string
  readonly state: FixturePayload['state']
  readonly path: string
  readonly provider: string
  readonly network: FixturePayload['network']
  readonly fictional: boolean
}

interface ManifestScenario {
  readonly id: string
  readonly surface: 'electron' | 'terminal'
  readonly launch: readonly string[]
  readonly fixture_id: string
  readonly geometry:
    | Readonly<{
      kind: 'viewport'
      width: number
      height: number
      device_scale_factor: number
    }>
    | Readonly<{ kind: 'terminal'; columns: number; rows: number }>
  readonly ready_signal: Readonly<{ kind: 'text'; value: string }>
  readonly output_path: string
  readonly comparison:
    | Readonly<{ mode: 'exact' }>
    | Readonly<{ mode: 'tolerance'; max_differing_pixels: number; rationale: string }>
}

interface ManifestPayload {
  readonly schema_version: 1
  readonly output_allowlist: readonly string[]
  readonly determinism: Readonly<{
    locale: string
    timezone: string
    theme: string
    fonts: Readonly<{
      electron: Readonly<{
        family: string
        size_px: number
        weight: number
        source_policy: string
      }>
    }>
    animations: string
    fixed_timestamp: string
    fixed_username: string
    id_seed: string
    network: string
  }>
  readonly fixtures: readonly FixtureDescriptor[]
  readonly scenarios: readonly ManifestScenario[]
}

export interface ElectronCaptureScenario {
  readonly id: string
  readonly outputPath: string
  readonly fixture: Readonly<{
    id: string
    state: 'success' | 'degraded'
    content: Readonly<FixtureContent>
  }>
  readonly geometry: Readonly<{
    width: number
    height: number
    deviceScaleFactor: number
  }>
  readonly readySignal: Readonly<{ kind: 'text'; value: string }>
  readonly comparison: ManifestScenario['comparison']
}

export interface ElectronCapturePlan {
  readonly schemaVersion: 1
  readonly determinism: Readonly<{
    locale: string
    timezone: string
    theme: string
    font: Readonly<{
      family: string
      sizePx: number
      weight: number
      sourcePolicy: string
    }>
    animations: string
    fixedTimestamp: string
    fixedUsername: string
    idSeed: string
    network: string
  }>
  readonly scenarios: readonly Readonly<ElectronCaptureScenario>[]
}

interface CaptureRuntime {
  readonly outputRoot: string
  readonly outputRootRealPath: string
  readonly allowedOutputs: ReadonlySet<string>
  readonly privacyCanaries: readonly string[]
}

const captureRuntime = new WeakMap<ElectronCapturePlan, CaptureRuntime>()

export async function loadElectronCapturePlan(options: Readonly<{
  repositoryRoot: string
  outputRoot: string
  electronExecutablePath: string
  fontPath: string
}>): Promise<ElectronCapturePlan> {
  const repositoryRoot = resolve(options.repositoryRoot)
  const outputRoot = resolve(options.outputRoot)
  await requireDirectory(outputRoot, 'DOCSHOT_OUTPUT_ROOT_MISSING')
  await requireFile(options.electronExecutablePath, 'DOCSHOT_BINARY_MISSING', true)
  await requireFile(options.fontPath, 'DOCSHOT_FONT_MISSING')

  const manifestSchema = await loadJson(
    join(repositoryRoot, MANIFEST_SCHEMA_PATH),
    'DOCSHOT_SCHEMA_MISSING',
    'DOCSHOT_SCHEMA_INVALID',
  )
  const fixtureSchema = await loadJson(
    join(repositoryRoot, FIXTURE_SCHEMA_PATH),
    'DOCSHOT_SCHEMA_MISSING',
    'DOCSHOT_SCHEMA_INVALID',
  )
  const manifest = await loadJson(
    join(repositoryRoot, MANIFEST_PATH),
    'DOCSHOT_MANIFEST_MISSING',
    'DOCSHOT_MANIFEST_INVALID',
  )

  const ajv = new Ajv2020({ allErrors: true, strict: true })
  let validateManifest: ReturnType<typeof ajv.compile>
  let validateFixture: ReturnType<typeof ajv.compile>
  try {
    validateManifest = ajv.compile(manifestSchema as AnySchema)
    validateFixture = ajv.compile(fixtureSchema as AnySchema)
  } catch {
    fail('DOCSHOT_SCHEMA_INVALID')
  }
  if (!validateManifest(manifest)) fail('DOCSHOT_MANIFEST_INVALID')
  const payload = manifest as ManifestPayload

  const fixtureById = new Map<string, Readonly<FixturePayload>>()
  const privacyCanaries: string[] = []
  for (const descriptor of payload.fixtures) {
    const fixturePath = safeRepositoryPath(repositoryRoot, descriptor.path)
    if (fixturePath === null) fail('DOCSHOT_FIXTURE_INVALID')
    await requireFixtureFile(repositoryRoot, fixturePath)
    const fixture = await loadJson(
      fixturePath,
      'DOCSHOT_FIXTURE_MISSING',
      'DOCSHOT_FIXTURE_INVALID',
    )
    if (!validateFixture(fixture)) fail('DOCSHOT_FIXTURE_INVALID')
    const fixturePayload = fixture as FixturePayload
    if (
      fixturePayload.fixture_id !== descriptor.id
      || fixturePayload.state !== descriptor.state
      || fixturePayload.provider !== descriptor.provider
      || fixturePayload.network !== descriptor.network
      || fixturePayload.fictional !== descriptor.fictional
      || fixturePayload.provider !== 'none'
      || fixturePayload.network !== 'disabled'
      || !fixturePayload.fictional
    ) fail('DOCSHOT_FIXTURE_INVALID')
    if (fixtureById.has(descriptor.id)) fail('DOCSHOT_FIXTURE_INVALID')
    fixtureById.set(descriptor.id, deepFreeze(fixturePayload))
    if (fixturePayload.state === 'privacy-canary') {
      privacyCanaries.push(...fixturePayload.privacy_canaries)
    }
  }

  const electronScenarios: ElectronCaptureScenario[] = []
  for (const scenario of payload.scenarios) {
    if (scenario.surface !== 'electron') continue
    if (!sameCommand(scenario.launch, ELECTRON_CAPTURE_COMMAND)) {
      fail('DOCSHOT_MANIFEST_INVALID')
    }
    if (scenario.geometry.kind !== 'viewport') fail('DOCSHOT_MANIFEST_INVALID')
    if (!isSafeDeclaredOutput(scenario.output_path, payload.output_allowlist)) {
      fail('DOCSHOT_OUTPUT_UNDECLARED')
    }
    const fixture = fixtureById.get(scenario.fixture_id)
    if (fixture === undefined || fixture.state === 'privacy-canary') {
      fail('DOCSHOT_FIXTURE_INVALID')
    }
    const fixtureText = Object.values(fixture.content).join('\n')
    if (!fixtureText.includes(scenario.ready_signal.value)) {
      fail('DOCSHOT_READY_SIGNAL_INVALID')
    }
    electronScenarios.push(deepFreeze({
      id: scenario.id,
      outputPath: scenario.output_path,
      fixture: {
        id: fixture.fixture_id,
        state: fixture.state,
        content: fixture.content,
      },
      geometry: {
        width: scenario.geometry.width,
        height: scenario.geometry.height,
        deviceScaleFactor: scenario.geometry.device_scale_factor,
      },
      readySignal: {
        kind: scenario.ready_signal.kind,
        value: scenario.ready_signal.value,
      },
      comparison: scenario.comparison,
    }))
  }
  electronScenarios.sort((left, right) => left.id.localeCompare(right.id, 'en'))
  if (
    electronScenarios.length !== 2
    || new Set(electronScenarios.map(({ fixture }) => fixture.state)).size !== 2
    || !electronScenarios.some(({ fixture }) => fixture.state === 'success')
    || !electronScenarios.some(({ fixture }) => fixture.state === 'degraded')
  ) fail('DOCSHOT_MANIFEST_INVALID')

  const plan: ElectronCapturePlan = deepFreeze({
    schemaVersion: 1,
    determinism: {
      locale: payload.determinism.locale,
      timezone: payload.determinism.timezone,
      theme: payload.determinism.theme,
      font: {
        family: payload.determinism.fonts.electron.family,
        sizePx: payload.determinism.fonts.electron.size_px,
        weight: payload.determinism.fonts.electron.weight,
        sourcePolicy: payload.determinism.fonts.electron.source_policy,
      },
      animations: payload.determinism.animations,
      fixedTimestamp: payload.determinism.fixed_timestamp,
      fixedUsername: payload.determinism.fixed_username,
      idSeed: payload.determinism.id_seed,
      network: payload.determinism.network,
    },
    scenarios: electronScenarios,
  })
  captureRuntime.set(plan, {
    outputRoot,
    outputRootRealPath: await realpath(outputRoot),
    allowedOutputs: new Set(electronScenarios.map(({ outputPath }) => outputPath)),
    privacyCanaries: Object.freeze([...privacyCanaries]),
  })
  return plan
}

export function assertCaptureIsPrivate(text: string, canaries: readonly string[]): void {
  if (canaries.some((canary) => canary.length > 0 && text.includes(canary))) {
    fail('DOCSHOT_PRIVACY_CANARY_LEAKED')
  }
}

export function assertPlanCaptureIsPrivate(plan: ElectronCapturePlan, text: string): void {
  const runtime = captureRuntime.get(plan)
  if (runtime === undefined) fail('DOCSHOT_MANIFEST_INVALID')
  assertCaptureIsPrivate(text, runtime.privacyCanaries)
}

export function assertNoUnexpectedNetwork(urls: readonly string[]): void {
  if (urls.length > 0) fail('DOCSHOT_NETWORK_UNEXPECTED')
}

export function assertExactCapture(first: Uint8Array, second: Uint8Array): void {
  if (!Buffer.from(first).equals(Buffer.from(second))) fail('DOCSHOT_CAPTURE_MISMATCH')
}

export async function publishCaptureAtomically(
  plan: ElectronCapturePlan,
  outputPath: string,
  bytes: Uint8Array,
): Promise<string> {
  const runtime = captureRuntime.get(plan)
  if (runtime === undefined || !runtime.allowedOutputs.has(outputPath)) {
    fail('DOCSHOT_OUTPUT_UNDECLARED')
  }
  if (!isSafeOutputPath(outputPath)) fail('DOCSHOT_OUTPUT_UNDECLARED')

  const destination = resolve(runtime.outputRoot, outputPath)
  if (!isWithin(runtime.outputRoot, destination)) fail('DOCSHOT_OUTPUT_UNDECLARED')
  const parent = dirname(destination)
  try {
    await mkdir(parent, { recursive: true })
    await rejectSymlinkComponents(runtime.outputRoot, parent)
    const parentRealPath = await realpath(parent)
    if (!isWithin(runtime.outputRootRealPath, parentRealPath)) fail('DOCSHOT_OUTPUT_UNDECLARED')
  } catch (error) {
    if (error instanceof DocsScreenshotCaptureError) throw error
    fail('DOCSHOT_OUTPUT_WRITE_FAILED')
  }

  const temporaryPath = `${destination}.tmp-${process.pid}-${randomUUID()}`
  let handle: Awaited<ReturnType<typeof open>> | undefined
  try {
    handle = await open(temporaryPath, 'wx', 0o600)
    await handle.writeFile(bytes)
    await handle.sync()
    await handle.close()
    handle = undefined
    await rename(temporaryPath, destination)
  } catch {
    await handle?.close().catch(() => undefined)
    await unlink(temporaryPath).catch(() => undefined)
    fail('DOCSHOT_OUTPUT_WRITE_FAILED')
  }
  return destination
}

async function loadJson(
  path: string,
  missingCode: CaptureFailureCode,
  invalidCode: CaptureFailureCode,
): Promise<unknown> {
  let source: string
  try {
    source = await readFile(path, 'utf8')
  } catch {
    fail(missingCode)
  }
  try {
    return JSON.parse(source) as unknown
  } catch {
    fail(invalidCode)
  }
}

async function requireDirectory(path: string, code: CaptureFailureCode): Promise<void> {
  try {
    const metadata = await lstat(path)
    if (!metadata.isDirectory() || metadata.isSymbolicLink()) fail(code)
  } catch (error) {
    if (error instanceof DocsScreenshotCaptureError) throw error
    fail(code)
  }
}

async function requireFile(
  path: string,
  code: CaptureFailureCode,
  executable = false,
): Promise<void> {
  try {
    const metadata = await lstat(path)
    if (!metadata.isFile() || metadata.isSymbolicLink()) fail(code)
    if (executable) await access(path, constants.X_OK)
  } catch (error) {
    if (error instanceof DocsScreenshotCaptureError) throw error
    fail(code)
  }
}

async function requireFixtureFile(repositoryRoot: string, fixturePath: string): Promise<void> {
  if (!isWithin(repositoryRoot, fixturePath)) fail('DOCSHOT_FIXTURE_INVALID')
  const pathFromRoot = relative(repositoryRoot, fixturePath)
  let current = repositoryRoot
  let metadata: Awaited<ReturnType<typeof lstat>> | undefined
  try {
    for (const component of pathFromRoot.split(sep).filter(Boolean)) {
      current = join(current, component)
      metadata = await lstat(current)
      if (metadata.isSymbolicLink()) fail('DOCSHOT_FIXTURE_INVALID')
    }
  } catch (error) {
    if (error instanceof DocsScreenshotCaptureError) throw error
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') fail('DOCSHOT_FIXTURE_MISSING')
    fail('DOCSHOT_FIXTURE_INVALID')
  }
  if (metadata === undefined || !metadata.isFile()) {
    fail('DOCSHOT_FIXTURE_INVALID')
  }
}

function safeRepositoryPath(repositoryRoot: string, relativePath: string): string | null {
  if (isAbsolute(relativePath) || relativePath.includes('\\')) return null
  const candidate = resolve(repositoryRoot, relativePath)
  return isWithin(repositoryRoot, candidate) ? candidate : null
}

function isSafeDeclaredOutput(path: string, allowlist: readonly string[]): boolean {
  return isSafeOutputPath(path) && allowlist.includes(path)
}

function isSafeOutputPath(path: string): boolean {
  return path.startsWith(SAFE_OUTPUT_PREFIX)
    && path.endsWith('.png')
    && !isAbsolute(path)
    && !path.includes('\\')
    && !path.split('/').includes('..')
    && path.split('/').every((part) => part.length > 0 && part !== '.')
}

function isWithin(parent: string, child: string): boolean {
  const pathFromParent = relative(parent, child)
  return pathFromParent === ''
    || (!pathFromParent.startsWith(`..${sep}`) && pathFromParent !== '..' && !isAbsolute(pathFromParent))
}

async function rejectSymlinkComponents(root: string, target: string): Promise<void> {
  const pathFromRoot = relative(root, target)
  if (!isWithin(root, target)) fail('DOCSHOT_OUTPUT_UNDECLARED')
  let current = root
  for (const component of pathFromRoot.split(sep).filter(Boolean)) {
    current = join(current, component)
    const metadata = await lstat(current)
    if (metadata.isSymbolicLink()) fail('DOCSHOT_OUTPUT_UNDECLARED')
  }
}

function sameCommand(actual: readonly string[], expected: readonly string[]): boolean {
  return actual.length === expected.length && actual.every((value, index) => value === expected[index])
}

function fail(code: CaptureFailureCode): never {
  throw new DocsScreenshotCaptureError(code)
}

function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    for (const child of Object.values(value as Record<string, unknown>)) deepFreeze(child)
  }
  return value
}
