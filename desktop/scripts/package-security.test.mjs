import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import {
  asarIntegrityReport,
  formatInspectionSummary,
  parseArguments,
  writeInspectionReport,
} from './inspect-package-fuses.mjs'

const packageJson = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))
const productionMain = await readFile(new URL('../src/main/index.ts', import.meta.url), 'utf8')

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

test('production main entry contains no fixture bridge or test hook', () => {
  assert.doesNotMatch(productionMain, /createMockAncestryBridge/)
  assert.doesNotMatch(productionMain, /ANCESTRYLLM_DESKTOP_FIXTURE/)
  assert.doesNotMatch(productionMain, /ANCESTRYLLM_DESKTOP_SECURITY_E2E/)
  assert.doesNotMatch(productionMain, /__ancestryllmSecurityStateForTests/)
})

test('macOS ASAR integrity metadata is verified against the packaged header hash', () => {
  const hash = 'a'.repeat(64)
  const plist = {
    ElectronAsarIntegrity: {
      'Resources/app.asar': { algorithm: 'SHA256', hash },
    },
  }

  assert.deepEqual(asarIntegrityReport('darwin', plist, hash), {
    status: 'verified',
    scope: 'ElectronAsarIntegrity Info.plist metadata for Resources/app.asar',
    algorithm: 'SHA256',
    hash,
  })
  assert.throws(
    () => asarIntegrityReport('darwin', plist, 'b'.repeat(64)),
    /does not match the ASAR header/,
  )
})

test('Windows and Linux report the macOS Info.plist integrity scope as not applicable', () => {
  for (const platform of ['win32', 'linux']) {
    const report = asarIntegrityReport(platform)
    assert.equal(report.status, 'not-applicable')
    assert.match(report.scope, /Info\.plist/)
    assert.match(report.reason, /only in macOS application bundles/)
    assert.match(report.reason, /app\.asar presence and the embedded-integrity fuse were verified separately/)
  }
})

test('inspection output is optional, cross-platform, and write-once', async (t) => {
  assert.deepEqual(parseArguments([]), {})
  const directory = await mkdtemp(join(tmpdir(), 'ancestryllm package inspection '))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const outputPath = join(directory, 'package inspection.json')
  assert.deepEqual(parseArguments(['--output', outputPath]), { outputPath })

  const report = {
    schemaVersion: 1,
    kind: 'ancestryllm-desktop-package-security-inspection',
    platform: 'linux',
    package: { executable: '/package/ancestryllm', resources: '/package/resources' },
    fuses: { status: 'verified', count: 8, items: [] },
    asar: {
      path: '/package/resources/app.asar',
      presence: { status: 'verified' },
      integrity: asarIntegrityReport('linux'),
    },
  }
  await writeInspectionReport(outputPath, report)
  assert.deepEqual(JSON.parse(await readFile(outputPath, 'utf8')), report)
  await assert.rejects(
    writeInspectionReport(outputPath, report),
    (error) => error?.code === 'EEXIST',
  )
})

test('inspection summary states the platform-specific integrity scope truthfully', () => {
  const report = {
    platform: 'win32',
    fuses: { count: 8 },
    asar: { integrity: asarIntegrityReport('win32') },
  }
  assert.equal(
    formatInspectionSummary(report),
    'Verified app.asar presence and 8 packaged Electron fuse states; ElectronAsarIntegrity Info.plist metadata verification is not applicable on win32.',
  )
})
