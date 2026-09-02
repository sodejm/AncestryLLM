/** Rejects prohibited files, secrets, and development-only behavior in desktop builds. */
import { readdir, readFile } from 'node:fs/promises'
import { extname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const prohibitedNames = [/\.map$/i, /^latest.*\.ya?ml$/i, /credential/i, /^\.env/i]
const prohibitedContent = [
  /Component gallery/i,
  /sourceMappingURL=/i,
  /\bfetch\s*\(\s*['"]https?:\/\//i,
  /\bnew\s+(?:EventSource|WebSocket|XMLHttpRequest)\s*\(/i,
  /\bnavigator\.sendBeacon\s*\(/i,
  /(?:src|href)\s*=\s*['"]https?:\/\//i,
  /@import\s+(?:url\()?\s*['"]?https?:\/\//i,
  /BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY/i,
]
const prohibitedProductionFixtureContent = [
  /createMockAncestryBridge/,
  /ANCESTRYLLM_DESKTOP_FIXTURE/,
  /ANCESTRYLLM_DESKTOP_SECURITY_E2E/,
  /__ancestryllmSecurityStateForTests/,
]
const prohibitedPackagedFileGrantContent = [
  /ANCESTRYLLM_PACKAGED_FILE_GRANT_VERIFICATION/,
  /ANCESTRYLLM_FILE_GRANT_OPEN_PATH/,
  /ANCESTRYLLM_FILE_GRANT_SAVE_PATH/,
]
const prohibitedPackagedNativeVerificationContent = [
  /ancestryllm-linux-keyring-verification-root/,
  /ancestryllm-macos-ephemeral-verification/,
]
async function files(root) { return (await readdir(root, { withFileTypes: true })).flatMap((entry) => entry.isDirectory() ? [] : [join(root, entry.name)]) }
async function walk(root) {
  const entries = await readdir(root, { withFileTypes: true }); const output = []
  for (const entry of entries) {
    if (entry.isDirectory()) output.push(...await walk(join(root, entry.name)))
    else output.push(join(root, entry.name))
  }
  return output
}
/**
 * Scans a packaged desktop tree and rejects prohibited names, content, secrets, and debug-only capabilities.
 * @param {string} root - Packaged artifact tree to inspect without mutation.
 * @param {{allowFixtures?: boolean, allowPackagedNativeVerification?: boolean, allowPackagedFileGrants?: boolean}} [options] - Narrow exceptions used only by dedicated verification packages.
 * @returns {Promise<number>} Number of non-empty packaged files inspected.
 */
export async function inspectBuild(
  root,
  {
    allowFixtures = false,
    allowPackagedNativeVerification = false,
    allowPackagedFileGrants = false,
  } = {},
) {
  const all = await walk(root)
  for (const file of all) {
    const name = file.split('/').at(-1)
    if (prohibitedNames.some((pattern) => pattern.test(name))) throw new Error(`Prohibited build artifact: ${name}`)
    if (['.js','.mjs','.cjs','.html','.css','.txt'].includes(extname(file))) {
      const content = await readFile(file, 'utf8')
      const match = prohibitedContent.find((pattern) => pattern.test(content))
      if (match) throw new Error(`Prohibited build content in ${name}: ${match.source}`)
      const fixtureMatch = allowFixtures
        ? undefined
        : prohibitedProductionFixtureContent.find((pattern) => pattern.test(content))
      if (fixtureMatch) throw new Error(`Prohibited production fixture content in ${name}: ${fixtureMatch.source}`)
      const packagedNativeVerificationMatch = allowPackagedNativeVerification
        ? undefined
        : prohibitedPackagedNativeVerificationContent.find((pattern) => pattern.test(content))
      if (packagedNativeVerificationMatch) {
        throw new Error(`Prohibited packaged native-verification content in ${name}: ${packagedNativeVerificationMatch.source}`)
      }
      const packagedFileGrantMatch = allowPackagedFileGrants
        ? undefined
        : prohibitedPackagedFileGrantContent.find((pattern) => pattern.test(content))
      if (packagedFileGrantMatch) {
        throw new Error(`Prohibited packaged file-grant verification content in ${name}: ${packagedFileGrantMatch.source}`)
      }
    }
  }
  if ((await files(root)).length === 0 && all.length === 0) throw new Error('Build output is empty')
  return all.length
}
/**
 * Resolves the desktop build-output directory relative to a module URL.
 * @param {string | URL} moduleUrl - Module location used as the trusted path anchor.
 * @param {{windows?: boolean}} [options] - Optional Node URL conversion behavior for cross-platform tests.
 * @returns {string} Absolute path to the adjacent desktop output directory.
 */
export function resolveBuildOutputPath(moduleUrl, options) {
  return fileURLToPath(new URL('../out', moduleUrl), options)
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const count = await inspectBuild(
    resolveBuildOutputPath(import.meta.url),
    {
      allowFixtures: process.argv.includes('--fixture'),
      allowPackagedNativeVerification: process.argv.includes('--packaged-native-verification'),
      allowPackagedFileGrants: process.argv.includes('--packaged-file-grants'),
    },
  )
  console.log(`Verified ${count} build artifacts: no development-only gallery copy, source maps, embedded remote network endpoints, credentials, or updater metadata.`)
}
