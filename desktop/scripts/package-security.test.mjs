import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const packageJson = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))

test('packaging locks the production ASAR and Electron fuse policy', () => {
  assert.equal(packageJson.build.asar, true)
  assert.deepEqual(packageJson.build.electronFuses, {
    runAsNode: false,
    enableCookieEncryption: true,
    enableNodeOptionsEnvironmentVariable: false,
    enableNodeCliInspectArguments: false,
    enableEmbeddedAsarIntegrityValidation: true,
    onlyLoadAppFromAsar: true,
    loadBrowserProcessSpecificV8Snapshot: false,
    grantFileProtocolExtraPrivileges: false,
  })
  assert.equal(packageJson.scripts['test:security'], 'pnpm package && vitest run src/main/security-policy.test.ts src/main/session-policy.test.ts src/main/external-links.test.ts && node scripts/inspect-package-fuses.mjs')
  assert.equal(packageJson.devDependencies['@electron/fuses'], '2.1.2')
})
