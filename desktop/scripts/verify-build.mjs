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
async function files(root) { return (await readdir(root, { withFileTypes: true })).flatMap((entry) => entry.isDirectory() ? [] : [join(root, entry.name)]) }
async function walk(root) {
  const entries = await readdir(root, { withFileTypes: true }); const output = []
  for (const entry of entries) {
    if (entry.isDirectory()) output.push(...await walk(join(root, entry.name)))
    else output.push(join(root, entry.name))
  }
  return output
}
export async function inspectBuild(
  root,
  { allowFixtures = false, allowPackagedFileGrants = false } = {},
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
export function resolveBuildOutputPath(moduleUrl, options) {
  return fileURLToPath(new URL('../out', moduleUrl), options)
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const count = await inspectBuild(
    resolveBuildOutputPath(import.meta.url),
    {
      allowFixtures: process.argv.includes('--fixture'),
      allowPackagedFileGrants: process.argv.includes('--packaged-file-grants'),
    },
  )
  console.log(`Verified ${count} build artifacts: no development-only gallery copy, source maps, embedded remote network endpoints, credentials, or updater metadata.`)
}
