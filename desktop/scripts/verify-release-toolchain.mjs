/** Verifies that declared and running desktop release tools match the quality policy. */
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)
const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const repositoryRoot = resolve(scriptDirectory, '..', '..')
const desktopRoot = join(repositoryRoot, 'desktop')

async function readJson(path) {
  return JSON.parse(await readFile(path, 'utf8'))
}

async function output(executable, args) {
  const result = await execFileAsync(executable, args, {
    cwd: repositoryRoot,
    encoding: 'utf8',
  })
  return result.stdout.trim()
}

function exactVersion(value, prefix = '') {
  const match = value.match(new RegExp(`(?:^|\\s)${prefix.replace('.', '\\.')}([0-9]+\\.[0-9]+\\.[0-9]+)(?:$|\\s)`))
  assert.ok(match, `could not determine a semantic version from: ${value}`)
  return match[1]
}

/**
 * Validates the release toolchain against policy and package declarations.
 * @returns {Promise<Readonly<Record<string, string>>>} Exact validated versions.
 */
export async function verifyReleaseToolchain() {
  const [policy, desktopPackage] = await Promise.all([
    readJson(join(repositoryRoot, 'config', 'release-quality-policy-v1.json')),
    readJson(join(desktopRoot, 'package.json')),
  ])
  const expected = policy.qa.toolVersions
  assert.deepEqual(Object.keys(expected).sort(), ['node', 'pnpm', 'python', 'vitest', 'webdriverio'])
  assert.equal(desktopPackage.engines.node, expected.node, 'desktop Node declaration differs from release policy')
  assert.equal(desktopPackage.engines.pnpm, expected.pnpm, 'desktop pnpm declaration differs from release policy')
  assert.equal(desktopPackage.packageManager, `pnpm@${expected.pnpm}`, 'desktop package manager differs from release policy')
  assert.equal(desktopPackage.devDependencies.vitest, expected.vitest, 'Vitest declaration differs from release policy')
  assert.equal(desktopPackage.devDependencies.webdriverio, expected.webdriverio, 'WebdriverIO declaration differs from release policy')

  const observed = Object.freeze({
    python: (await output('python', ['--version'])).replace(/^Python\s+/, '').split('.').slice(0, 2).join('.'),
    node: (await output('node', ['--version'])).replace(/^v/, ''),
    pnpm: await output('pnpm', ['--version']),
    vitest: exactVersion(await output('pnpm', ['--dir', 'desktop', 'exec', 'vitest', '--version']), 'vitest/'),
    webdriverio: exactVersion(await output('pnpm', ['--dir', 'desktop', 'exec', 'wdio', '--version'])),
  })
  assert.deepEqual(observed, expected, 'running release toolchain differs from policy')
  return observed
}

async function main() {
  const versions = await verifyReleaseToolchain()
  process.stdout.write(`${JSON.stringify(versions)}\n`)
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main()
}
