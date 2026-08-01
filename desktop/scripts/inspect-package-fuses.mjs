import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { readdir, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
import { FuseState, FuseV1Options, getCurrentFuseWire } from '@electron/fuses'

const execFileAsync = promisify(execFile)
const releaseRoot = fileURLToPath(new URL('../release/', import.meta.url))

async function findPackagedResources(root) {
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (!entry.isDirectory()) continue
    if ((entry.name === 'Resources' || entry.name === 'resources') && await isFile(join(path, 'app.asar'))) return path
    const nested = await findPackagedResources(path)
    if (nested) return nested
  }
  return null
}

async function isFile(path) {
  try { return (await stat(path)).isFile() } catch { return false }
}

async function findExecutable(resources) {
  if (process.platform === 'darwin') return resources.slice(0, resources.indexOf('.app/') + 4)
  const packageRoot = join(resources, '..')
  const entries = await readdir(packageRoot, { withFileTypes: true })
  const candidates = entries.filter((entry) => entry.isFile() && (
    process.platform === 'win32' ? entry.name === 'AncestryLLM.exe' : entry.name === 'ancestryllm-desktop'
  ))
  assert.equal(candidates.length, 1, 'Expected exactly one packaged application executable')
  return join(packageRoot, candidates[0].name)
}

const resources = await findPackagedResources(releaseRoot)
assert.ok(resources, 'Packaged resources directory was not found')
assert.equal(await isFile(join(resources, 'app.asar')), true, 'Production code must be packaged in app.asar')

const executable = await findExecutable(resources)
const fuses = await getCurrentFuseWire(executable)
const expected = new Map([
  [FuseV1Options.RunAsNode, FuseState.DISABLE],
  [FuseV1Options.EnableCookieEncryption, FuseState.ENABLE],
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable, FuseState.DISABLE],
  [FuseV1Options.EnableNodeCliInspectArguments, FuseState.DISABLE],
  [FuseV1Options.EnableEmbeddedAsarIntegrityValidation, FuseState.ENABLE],
  [FuseV1Options.OnlyLoadAppFromAsar, FuseState.ENABLE],
  [FuseV1Options.LoadBrowserProcessSpecificV8Snapshot, FuseState.DISABLE],
  [FuseV1Options.GrantFileProtocolExtraPrivileges, FuseState.DISABLE],
])
for (const [option, state] of expected) assert.equal(fuses[option], state, `Unexpected packaged fuse state for option ${option}`)

if (process.platform === 'darwin') {
  const infoPlist = join(executable, 'Contents', 'Info.plist')
  const { stdout } = await execFileAsync('/usr/bin/plutil', ['-convert', 'json', '-o', '-', infoPlist])
  const integrity = JSON.parse(stdout).ElectronAsarIntegrity
  assert.ok(integrity?.['Resources/app.asar']?.hash, 'Packaged app.asar integrity metadata was not embedded')
  assert.equal(integrity['Resources/app.asar'].algorithm, 'SHA256')
}

console.log(`Verified app.asar, ${expected.size} packaged Electron fuses, and supported integrity metadata.`)
