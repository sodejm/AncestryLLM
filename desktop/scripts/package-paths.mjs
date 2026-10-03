/** Discovers packaged application executables and resource roots on supported platforms. */
import assert from 'node:assert/strict'
import { readdir, stat } from 'node:fs/promises'
import { dirname, join } from 'node:path'

async function isFile(path) {
  try { return (await stat(path)).isFile() } catch { return false }
}

async function findPackagedResources(root) {
  for (const entry of await readdir(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const path = join(root, entry.name)
    if (
      (entry.name === 'Resources' || entry.name === 'resources')
      && await isFile(join(path, 'app.asar'))
    ) return path
    const nested = await findPackagedResources(path)
    if (nested) return nested
  }
  return null
}

function executableFor(resources, platform) {
  if (platform === 'darwin') {
    const appMarker = `${join('.app', 'Contents', 'Resources')}`
    assert.ok(resources.endsWith(appMarker), 'Packaged macOS Resources path is outside an application bundle')
    return join(resources.slice(0, -'Resources'.length), 'MacOS', 'ancestryllm')
  }
  if (platform === 'win32') return join(dirname(resources), 'ancestryllm.exe')
  if (platform === 'linux') return join(dirname(resources), 'ancestryllm')
  throw new Error(`Unsupported package platform: ${platform}`)
}

/**
 * Discovers the packaged resources directory and derives its platform-native executable.
 * @param {string} root - Release tree to search recursively for a packaged app.asar.
 * @param {string} platform - Node platform identifier that selects the native bundle layout.
 * @returns {Promise<{resources: string, executable: string}>} Existing resource and executable paths.
 */
export async function discoverPackage(root, platform = process.platform) {
  const resources = await findPackagedResources(root)
  assert.ok(resources, 'Packaged resources directory was not found')
  const executable = executableFor(resources, platform)
  assert.equal(
    await isFile(executable),
    true,
    `Expected packaged application executable at ${executable}`,
  )
  return { resources, executable }
}
