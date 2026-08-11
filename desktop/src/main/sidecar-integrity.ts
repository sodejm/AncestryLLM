// Authenticates the packaged native sidecar payload before Electron may execute it.
import { createHash, timingSafeEqual } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { lstat, readFile, readdir, readlink } from 'node:fs/promises'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'

export const MANIFEST_SCHEMA = 'ancestryllm.sidecar-payload/1'
const MANIFEST_NAME = 'sidecar-manifest.json'
const MAX_MANIFEST_BYTES = 4 * 1024 * 1024
const SHA256_PATTERN = /^[a-f0-9]{64}$/

interface FileManifestEntry {
  bytes: number
  path: string
  sha256: string
  type: 'file'
}

interface SymlinkManifestEntry {
  path: string
  target: string
  type: 'symlink'
}

type ManifestEntry = FileManifestEntry | SymlinkManifestEntry

interface PayloadManifest {
  app_build: string
  files: ManifestEntry[]
  schema: typeof MANIFEST_SCHEMA
  target: string
}

interface SidecarIntegrityOptions {
  targetRoot: string
  expectedManifestSha256: string
  expectedTarget: string
  appBuild: string
}

export class SidecarIntegrityError extends Error {
  constructor() {
    super('The packaged sidecar failed integrity verification.')
    this.name = 'SidecarIntegrityError'
  }
}

function fail(): never {
  throw new SidecarIntegrityError()
}

function exactKeys(value: Record<string, unknown>, expected: string[]): boolean {
  return Object.keys(value).sort().join(',') === [...expected].sort().join(',')
}

function safeRelativePath(path: unknown): path is string {
  if (typeof path !== 'string' || path.length === 0 || path.includes('\\')) return false
  if (isAbsolute(path)) return false
  const parts = path.split('/')
  return parts.every((part) => part.length > 0 && part !== '.' && part !== '..')
}

function parseEntry(value: unknown): ManifestEntry {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail()
  const entry = value as Record<string, unknown>
  if (!safeRelativePath(entry.path)) return fail()
  if (entry.type === 'file') {
    if (
      !exactKeys(entry, ['bytes', 'path', 'sha256', 'type'])
      || !Number.isSafeInteger(entry.bytes)
      || (entry.bytes as number) < 0
      || typeof entry.sha256 !== 'string'
      || !SHA256_PATTERN.test(entry.sha256)
    ) return fail()
    return entry as unknown as FileManifestEntry
  }
  if (entry.type === 'symlink') {
    if (
      !exactKeys(entry, ['path', 'target', 'type'])
      || typeof entry.target !== 'string'
      || entry.target.length === 0
      || entry.target.includes('\\')
    ) return fail()
    return entry as unknown as SymlinkManifestEntry
  }
  return fail()
}

function parseManifest(bytes: Buffer): PayloadManifest {
  let value: unknown
  try {
    value = JSON.parse(bytes.toString('utf8'))
  } catch {
    return fail()
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail()
  const manifest = value as Record<string, unknown>
  if (
    !exactKeys(manifest, ['app_build', 'files', 'schema', 'target'])
    || manifest.schema !== MANIFEST_SCHEMA
    || typeof manifest.app_build !== 'string'
    || typeof manifest.target !== 'string'
    || !Array.isArray(manifest.files)
    || manifest.files.length === 0
  ) return fail()
  const files = manifest.files.map(parseEntry)
  const paths = files.map((entry) => entry.path)
  if (
    new Set(paths).size !== paths.length
    || paths.some((path, index) => index > 0 && path <= paths[index - 1]!)
  ) return fail()
  return {
    app_build: manifest.app_build,
    files,
    schema: MANIFEST_SCHEMA,
    target: manifest.target,
  }
}

function digestBytes(bytes: Buffer): Buffer {
  return createHash('sha256').update(bytes).digest()
}

async function digestFile(path: string): Promise<string> {
  return new Promise((resolveDigest, reject) => {
    const digest = createHash('sha256')
    const stream = createReadStream(path)
    stream.on('data', (chunk) => digest.update(chunk))
    stream.once('error', reject)
    stream.once('end', () => resolveDigest(digest.digest('hex')))
  })
}

function withinTarget(targetRoot: string, path: string): boolean {
  const fromTarget = relative(targetRoot, path)
  return fromTarget !== '..'
    && !fromTarget.startsWith(`..${sep}`)
    && !isAbsolute(fromTarget)
}

async function inventory(root: string, targetRoot: string): Promise<string[]> {
  const entries: string[] = []
  for (const name of (await readdir(root)).sort()) {
    const path = join(root, name)
    const metadata = await lstat(path)
    const relativePath = relative(targetRoot, path).split(sep).join('/')
    if (relativePath === MANIFEST_NAME) continue
    if (metadata.isDirectory()) {
      entries.push(...await inventory(path, targetRoot))
    } else if (metadata.isFile() || metadata.isSymbolicLink()) {
      entries.push(relativePath)
    } else {
      return fail()
    }
  }
  return entries
}

async function verifyEntry(
  targetRoot: string,
  entry: ManifestEntry,
): Promise<void> {
  const path = resolve(targetRoot, entry.path)
  if (!withinTarget(targetRoot, path)) return fail()
  let metadata
  try {
    metadata = await lstat(path)
  } catch {
    return fail()
  }
  if (entry.type === 'file') {
    if (
      !metadata.isFile()
      || metadata.isSymbolicLink()
      || metadata.size !== entry.bytes
      || await digestFile(path) !== entry.sha256
    ) return fail()
    return
  }
  if (!metadata.isSymbolicLink()) return fail()
  const actualTarget = await readlink(path)
  if (actualTarget !== entry.target) return fail()
  const resolvedTarget = resolve(dirname(path), actualTarget)
  if (!withinTarget(targetRoot, resolvedTarget)) return fail()
}

async function verifyPayload(
  options: SidecarIntegrityOptions,
): Promise<void> {
  const targetRoot = resolve(options.targetRoot)
  const manifestPath = join(targetRoot, MANIFEST_NAME)
  let manifestMetadata
  let manifestBytes
  try {
    manifestMetadata = await lstat(manifestPath)
    if (
      !manifestMetadata.isFile()
      || manifestMetadata.isSymbolicLink()
      || manifestMetadata.size > MAX_MANIFEST_BYTES
    ) return fail()
    manifestBytes = await readFile(manifestPath)
  } catch {
    return fail()
  }
  if (!SHA256_PATTERN.test(options.expectedManifestSha256)) return fail()
  const expectedDigest = Buffer.from(options.expectedManifestSha256, 'hex')
  const actualDigest = digestBytes(manifestBytes)
  if (
    expectedDigest.length !== actualDigest.length
    || !timingSafeEqual(expectedDigest, actualDigest)
  ) return fail()

  const manifest = parseManifest(manifestBytes)
  if (
    manifest.target !== options.expectedTarget
    || manifest.app_build !== options.appBuild
  ) return fail()
  const actualPaths = (await inventory(targetRoot, targetRoot)).sort()
  const expectedPaths = manifest.files.map((entry) => entry.path)
  if (
    actualPaths.length !== expectedPaths.length
    || actualPaths.some((path, index) => path !== expectedPaths[index])
  ) return fail()
  for (const entry of manifest.files) {
    await verifyEntry(targetRoot, entry)
  }
}

export async function verifySidecarPayload(
  options: SidecarIntegrityOptions,
): Promise<void> {
  try {
    await verifyPayload(options)
  } catch (error) {
    if (error instanceof SidecarIntegrityError) throw error
    return fail()
  }
}
