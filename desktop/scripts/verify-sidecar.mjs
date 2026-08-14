/** Verifies packaged sidecar identity, manifest integrity, permissions, and containment. */
import { createHash } from 'node:crypto'
import { constants } from 'node:fs'
import {
  access,
  lstat,
  readFile,
  readdir,
  readlink,
} from 'node:fs/promises'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const MANIFEST_NAME = 'sidecar-manifest.json'
const MANIFEST_SCHEMA = 'ancestryllm.sidecar-payload/1'
const MAX_MANIFEST_BYTES = 4 * 1024 * 1024
const SHA256_PATTERN = /^[a-f0-9]{64}$/
const PACKAGE_BUILD = JSON.parse(
  await readFile(fileURLToPath(new URL('../package.json', import.meta.url)), 'utf8'),
).version

const SUPPORTED_TARGETS = new Set([
  'darwin-arm64',
  'darwin-x64',
  'win32-arm64',
  'win32-x64',
  'linux-x64',
])

export function nativeTarget(platform, architecture) {
  const target = `${platform}-${architecture}`
  if (!SUPPORTED_TARGETS.has(target)) {
    throw new Error(`Unsupported desktop target: ${target}`)
  }
  return target
}

export function sidecarExecutable(root, target) {
  if (!SUPPORTED_TARGETS.has(target)) {
    throw new Error(`Unsupported desktop target: ${target}`)
  }
  const executable = target.startsWith('win32-')
    ? 'ancestryllm-sidecar.exe'
    : 'ancestryllm-sidecar'
  return join(root, target, 'ancestryllm-sidecar', executable)
}

function exactKeys(value, expected) {
  return Object.keys(value).sort().join(',') === [...expected].sort().join(',')
}

function safeRelativePath(path) {
  return typeof path === 'string'
    && path.length > 0
    && !path.includes('\\')
    && !isAbsolute(path)
    && path.split('/').every((part) => part.length > 0 && part !== '.' && part !== '..')
}

function within(root, path) {
  const fromRoot = relative(root, path)
  return fromRoot !== '..' && !fromRoot.startsWith(`..${sep}`) && !isAbsolute(fromRoot)
}

function parseManifest(bytes, target, appBuild) {
  let manifest
  try {
    manifest = JSON.parse(bytes.toString('utf8'))
  } catch {
    throw new Error('Sidecar manifest is not valid JSON')
  }
  if (
    !manifest
    || typeof manifest !== 'object'
    || Array.isArray(manifest)
    || !exactKeys(manifest, ['app_build', 'files', 'schema', 'target'])
    || manifest.schema !== MANIFEST_SCHEMA
    || manifest.app_build !== appBuild
    || manifest.target !== target
    || !Array.isArray(manifest.files)
    || manifest.files.length === 0
  ) throw new Error('Sidecar manifest metadata is invalid')

  let previous = ''
  const paths = new Set()
  for (const entry of manifest.files) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry) || !safeRelativePath(entry.path)) {
      throw new Error('Sidecar manifest entry is invalid')
    }
    if (paths.has(entry.path) || (previous && entry.path <= previous)) {
      throw new Error('Sidecar manifest paths are not unique and sorted')
    }
    paths.add(entry.path)
    previous = entry.path
    if (entry.type === 'file') {
      if (
        !exactKeys(entry, ['bytes', 'path', 'sha256', 'type'])
        || !Number.isSafeInteger(entry.bytes)
        || entry.bytes < 0
        || typeof entry.sha256 !== 'string'
        || !SHA256_PATTERN.test(entry.sha256)
      ) throw new Error('Sidecar file manifest entry is invalid')
    } else if (
      entry.type !== 'symlink'
      || !exactKeys(entry, ['path', 'target', 'type'])
      || typeof entry.target !== 'string'
      || entry.target.length === 0
      || entry.target.includes('\\')
    ) {
      throw new Error('Sidecar symlink manifest entry is invalid')
    }
  }
  return manifest
}

async function payloadInventory(directory, targetRoot) {
  const paths = []
  for (const name of (await readdir(directory)).sort()) {
    const path = join(directory, name)
    const metadata = await lstat(path)
    const relativePath = relative(targetRoot, path).split(sep).join('/')
    if (relativePath === MANIFEST_NAME) continue
    if (metadata.isDirectory()) {
      paths.push(...await payloadInventory(path, targetRoot))
    } else if (metadata.isFile() || metadata.isSymbolicLink()) {
      paths.push(relativePath)
    } else {
      throw new Error(`Unsupported sidecar payload entry: ${relativePath}`)
    }
  }
  return paths
}

async function verifyManifestEntry(targetRoot, entry) {
  const path = resolve(targetRoot, entry.path)
  if (!within(targetRoot, path)) throw new Error('Sidecar manifest path escapes its target')
  const metadata = await lstat(path)
  if (entry.type === 'file') {
    if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size !== entry.bytes) {
      throw new Error(`Sidecar file metadata differs from its manifest: ${entry.path}`)
    }
    const digest = createHash('sha256').update(await readFile(path)).digest('hex')
    if (digest !== entry.sha256) {
      throw new Error(`Sidecar file digest differs from its manifest: ${entry.path}`)
    }
    return
  }
  if (!metadata.isSymbolicLink()) {
    throw new Error(`Sidecar symlink differs from its manifest: ${entry.path}`)
  }
  const actualTarget = await readlink(path)
  if (actualTarget !== entry.target || !within(targetRoot, resolve(dirname(path), actualTarget))) {
    throw new Error(`Sidecar symlink escapes or differs from its manifest: ${entry.path}`)
  }
}

export async function verifySidecar(root, target, appBuild = PACKAGE_BUILD) {
  if (!SUPPORTED_TARGETS.has(target)) {
    throw new Error(`Unsupported desktop target: ${target}`)
  }
  const targetRoot = resolve(root, target)
  const manifestPath = join(targetRoot, MANIFEST_NAME)
  const manifestMetadata = await lstat(manifestPath)
  if (
    !manifestMetadata.isFile()
    || manifestMetadata.isSymbolicLink()
    || manifestMetadata.size > MAX_MANIFEST_BYTES
  ) throw new Error('Sidecar manifest must be a bounded regular file')
  const manifest = parseManifest(await readFile(manifestPath), target, appBuild)
  const inventory = (await payloadInventory(targetRoot, targetRoot)).sort()
  const expected = manifest.files.map((entry) => entry.path)
  if (inventory.length !== expected.length || inventory.some((path, index) => path !== expected[index])) {
    throw new Error('Sidecar payload inventory differs from its manifest')
  }
  for (const entry of manifest.files) {
    await verifyManifestEntry(targetRoot, entry)
  }

  const executable = sidecarExecutable(root, target)
  const metadata = await lstat(executable)
  if (!metadata.isFile()) throw new Error(`Sidecar executable is not a file: ${executable}`)
  await access(executable, target.startsWith('win32-') ? constants.R_OK : constants.R_OK | constants.X_OK)
  return executable
}

async function findNamedFiles(root, basename) {
  const matches = []
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) matches.push(...await findNamedFiles(path, basename))
    if (entry.isFile() && entry.name === basename) matches.push(path)
  }
  return matches
}

export async function verifyPackagedSidecar(releaseRoot, target, appBuild = PACKAGE_BUILD) {
  const suffix = ['sidecar', target, MANIFEST_NAME]
  const matches = (await findNamedFiles(releaseRoot, MANIFEST_NAME))
    .filter((path) => {
      const parts = relative(releaseRoot, path).split(sep)
      const resourcesIndex = parts.findIndex((part) => part.toLowerCase() === 'resources')
      return resourcesIndex >= 0
        && parts.slice(resourcesIndex + 1).join(sep) === suffix.join(sep)
    })
  if (matches.length !== 1) {
    throw new Error(`Expected exactly one packaged ${target} sidecar manifest, found ${matches.length}`)
  }
  const manifestPath = matches[0]
  const sidecarRoot = dirname(dirname(manifestPath))
  return verifySidecar(sidecarRoot, target, appBuild)
}

async function main() {
  const expectedTarget = process.argv[2]
  const actualTarget = nativeTarget(process.platform, process.arch)
  if (expectedTarget && expectedTarget !== actualTarget) {
    throw new Error(`Native host target ${actualTarget} does not match ${expectedTarget}`)
  }
  const root = resolve(fileURLToPath(new URL('../build/sidecar', import.meta.url)))
  const executable = await verifySidecar(root, actualTarget)
  const releaseRoot = process.argv[3]
  const packaged = releaseRoot
    ? await verifyPackagedSidecar(resolve(releaseRoot), actualTarget)
    : undefined
  process.stdout.write(`${packaged ?? executable}\n`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main()
}
