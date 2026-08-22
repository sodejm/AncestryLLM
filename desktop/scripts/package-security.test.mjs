/** Verifies package fuse inspection, integrity evidence, and report serialization. */
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
const pnpmWorkspace = await readFile(new URL('../pnpm-workspace.yaml', import.meta.url), 'utf8')
const electronPatch = await readFile(
  new URL('../patches/electron@39.8.10.patch', import.meta.url),
  'utf8',
)
const productionMain = await readFile(new URL('../src/main/index.ts', import.meta.url), 'utf8')
const productionRuntimeBridge = await readFile(
  new URL('../src/main/runtime-bridge.ts', import.meta.url),
  'utf8',
)
const productionNativeVerification = await readFile(
  new URL('../src/main/native-verification.ts', import.meta.url),
  'utf8',
)
const packagedNativeVerification = await readFile(
  new URL('../e2e/native-verification.packaged-verification.ts', import.meta.url),
  'utf8',
)
const packagedNativeVerificationBuilder = await readFile(
  new URL('../electron-builder.native-verification.yml', import.meta.url),
  'utf8',
)

const minimumPatchedElectronVersion = [39, 8, 10]

function parseExactVersion(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version)
  assert.ok(match, `Electron must use an exact semantic version, received ${version}`)
  return match.slice(1).map(Number)
}

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

test('ordinary pre-v1 packages cannot auto-discover a signing identity', () => {
  assert.deepEqual(packageJson.build.mac, {
    identity: null,
    forceCodeSigning: false,
    hardenedRuntime: false,
    notarize: false,
  })
  assert.deepEqual(packageJson.build.win, {
    signAndEditExecutable: true,
    signExecutable: false,
    forceCodeSigning: false,
  })
})

test('packaging pins Electron at the minimum audited security remediation', () => {
  const installed = parseExactVersion(packageJson.devDependencies.electron)
  const comparison = installed.findIndex((part, index) => part !== minimumPatchedElectronVersion[index])
  assert.ok(
    comparison === -1 || installed[comparison] > minimumPatchedElectronVersion[comparison],
    `Electron ${packageJson.devDependencies.electron} is below the audited remediation baseline 39.8.10`,
  )
})

test('dependency overrides preserve the audited transitive remediation floors', () => {
  assert.match(
    pnpmWorkspace,
    /^ {2}extract-zip: npm:@electron-internal\/extract-zip@1\.0\.5$/m,
  )
  assert.match(pnpmWorkspace, /^ {2}js-yaml@4\.3\.0: 4\.3\.1$/m)
  assert.match(pnpmWorkspace, /^ {2}nanoid@<3\.3\.18: 3\.3\.18$/m)
  assert.match(
    pnpmWorkspace,
    /^patchedDependencies:\n {2}electron@39\.8\.10: patches\/electron@39\.8\.10\.patch$/m,
  )
  assert.match(electronPatch, /\+const \{ extract \} = require\('extract-zip'\);/)
  assert.doesNotMatch(electronPatch, /\+const extract = require\('extract-zip'\);/)
})

test('production main entry contains no fixture bridge or test hook', () => {
  assert.doesNotMatch(productionMain, /createMockAncestryBridge/)
  assert.doesNotMatch(productionMain, /ANCESTRYLLM_DESKTOP_FIXTURE/)
  assert.doesNotMatch(productionMain, /ANCESTRYLLM_DESKTOP_SECURITY_E2E/)
  assert.doesNotMatch(productionMain, /__ancestryllmSecurityStateForTests/)
})

test('production packages cannot select verifier-only native storage roots', () => {
  assert.doesNotMatch(productionMain, /ancestryllm-linux-keyring-verification-root/)
  assert.doesNotMatch(productionNativeVerification, /LINUX_KEYRING_VERIFICATION_SWITCH/)
  assert.doesNotMatch(
    productionNativeVerification,
    /ancestryllm-linux-keyring-verification-root/,
  )
  assert.match(productionNativeVerification, /return undefined/)
  assert.match(packagedNativeVerification, /LINUX_KEYRING_VERIFICATION_SWITCH/)
  assert.equal(
    packageJson.scripts['build:packaged-native-verification'],
    'pnpm typecheck && electron-vite build && node scripts/verify-build.mjs --packaged-native-verification',
  )
  assert.match(
    packagedNativeVerificationBuilder,
    /^extends: \.\/electron-builder\.verification\.yml$/m,
  )
  assert.match(
    packagedNativeVerificationBuilder,
    /^ {2}output: release-native-verification$/m,
  )
})

test('production shutdown owns the supervisor before asynchronous sidecar startup', () => {
  const signalRegistration = productionMain.indexOf("process.on('SIGTERM'")
  const runtimeStartup = productionMain.indexOf('await startRuntimeBridge(')
  assert.notEqual(signalRegistration, -1, 'production main must handle SIGTERM')
  assert.notEqual(runtimeStartup, -1, 'production main must start the runtime bridge')
  assert.ok(
    signalRegistration < runtimeStartup,
    'SIGTERM handling must be installed before asynchronous runtime startup',
  )
  assert.match(
    productionMain,
    /startRuntimeBridge\(\(supervisor, prepareJobs\) => \{[\s\S]*?sidecarSupervisor = supervisor[\s\S]*?prepareJobShutdown = prepareJobs[\s\S]*?\}, \{\s*linuxKeyringVerificationRoot: requestedLinuxKeyringVerificationRoot\(app\.commandLine\),\s*diagnosticRunId,\s*recordDiagnostic: recordDesktopDiagnostic,\s*\}\)/,
  )
  assert.match(
    productionMain,
    /\(\) => supervisor\.isExplicitSafeEmpty\(\)/,
    'shutdown may skip HTTP preflight only through the supervisor-owned safe-empty proof',
  )
  assert.match(
    productionMain,
    /\(\) => supervisor\.isExplicitSafeEmpty\(\),\s*shutdownProgress,/,
    'shutdown retries must retain the completed job-preparation phase',
  )

  const ownershipCallback = productionRuntimeBridge.indexOf('onSupervisorOwned?.(')
  const supervisorStartup = productionRuntimeBridge.indexOf('await supervisor.start()')
  assert.notEqual(ownershipCallback, -1, 'runtime startup must expose supervisor ownership')
  assert.notEqual(supervisorStartup, -1, 'runtime startup must start the sidecar supervisor')
  assert.ok(
    ownershipCallback < supervisorStartup,
    'Electron main must own the supervisor before startup can spawn a sidecar',
  )
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
  const packageRoot = join(directory, 'unpublished verifier package')
  assert.deepEqual(
    parseArguments(['--root', packageRoot, '--output', outputPath]),
    { rootPath: packageRoot, outputPath },
  )
  assert.throws(
    () => parseArguments(['--root', packageRoot, '--root', packageRoot]),
    /may be supplied only once/,
  )

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
