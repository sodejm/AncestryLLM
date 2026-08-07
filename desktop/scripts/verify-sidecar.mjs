/** Verifies supported sidecar bundle targets and packaged sidecar placement inside Electron resources. */
import { constants } from 'node:fs'
import { access, readdir, stat } from 'node:fs/promises'
import { join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const SUPPORTED_TARGETS = new Set([
  'darwin-arm64',
  'darwin-x64',
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
  const executable = target === 'win32-x64'
    ? 'ancestryllm-sidecar.exe'
    : 'ancestryllm-sidecar'
  return join(root, target, 'ancestryllm-sidecar', executable)
}

export async function verifySidecar(root, target) {
  const executable = sidecarExecutable(root, target)
  const metadata = await stat(executable)
  if (!metadata.isFile()) throw new Error(`Sidecar executable is not a file: ${executable}`)
  await access(executable, target === 'win32-x64' ? constants.R_OK : constants.R_OK | constants.X_OK)
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

export async function verifyPackagedSidecar(releaseRoot, target) {
  const executable = target === 'win32-x64'
    ? 'ancestryllm-sidecar.exe'
    : 'ancestryllm-sidecar'
  const suffix = [
    'sidecar',
    target,
    'ancestryllm-sidecar',
    executable,
  ]
  const matches = (await findNamedFiles(releaseRoot, executable))
    .filter((path) => {
      const parts = relative(releaseRoot, path).split(sep)
      const resourcesIndex = parts.findIndex((part) => part.toLowerCase() === 'resources')
      return resourcesIndex >= 0
        && parts.slice(resourcesIndex + 1).join(sep) === suffix.join(sep)
    })
  if (matches.length !== 1) {
    throw new Error(`Expected exactly one packaged ${target} sidecar, found ${matches.length}`)
  }
  return matches[0]
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
