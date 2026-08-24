/** Verifies desktop evidence aggregation, coverage gates, and performance policy. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import {
  PERFORMANCE_POLICY_VERSION,
  TARGET_ROWS,
  aggregateEvidence,
  createSecurityEvidence,
  createTargetEvidence,
  runCli,
} from './verification-evidence.mjs'
import {
  SECURITY_RECEIPT_GATES,
  TARGET_RECEIPT_GATES,
  validateVerificationReceipt,
} from './verification-receipt.mjs'

const gitHead = '0123456789abcdef0123456789abcdef01234567'
const rows = [
  ['macos-15', 'darwin-arm64', 'macOS 15', 'macOS 15', 'arm64', 'arm64'],
  ['macos-15-intel', 'darwin-x64', 'macOS 15', 'macOS 15', 'x64', 'x64'],
  ['macos-26', 'darwin-arm64', 'macOS 26', 'macOS 26', 'arm64', 'arm64'],
  ['macos-26-intel', 'darwin-x64', 'macOS 26', 'macOS 26', 'x64', 'x64'],
  ['windows-11-arm', 'win32-arm64', 'Windows 11', 'Windows 11', 'arm64', 'arm64'],
  ['ubuntu-24.04', 'linux-x64', 'Ubuntu 24.04', 'Ubuntu 24.04', 'x64', 'x64'],
]

const metrics = {
  coldLaunchMs: 1200,
  warmLaunchMs: 900,
  readyMs: 500,
  rssBytes: 150_000_000,
  rendererOutboundRequests: 0,
}

function encoded(value) {
  return Buffer.from(`${JSON.stringify(value)}\n`)
}

function digest(bytes) {
  return { sha256: createHash('sha256').update(bytes).digest('hex'), bytes: bytes.byteLength }
}

function receiptRecord(gates, artifacts = {}, command = ['node', '--test']) {
  const receipt = {
    schemaVersion: 2,
    kind: 'verification-receipt',
    status: 'passed',
    gitHead,
    headBefore: gitHead,
    headAfter: gitHead,
    gates: [...gates].sort(),
    command: { executable: command[0], args: command.slice(1), shell: false },
    result: {
      exitCode: 0,
      signal: null,
      stdout: { sha256: createHash('sha256').update('').digest('hex'), bytes: 0 },
      stderr: { sha256: createHash('sha256').update('').digest('hex'), bytes: 0 },
    },
    artifacts,
    workspace: {
      algorithm: 'git-workspace-v1',
      allowedOutputs: [],
      before: { sha256: 'e'.repeat(64), bytes: 128 },
      after: { sha256: 'e'.repeat(64), bytes: 128 },
      status: 'unchanged',
    },
  }
  validateVerificationReceipt(receipt, gitHead)
  const raw = encoded(receipt)
  return { receipt, file: digest(raw), raw }
}

function fuseInspection(platform) {
  const integrity = platform === 'darwin'
    ? {
        status: 'verified',
        scope: 'ElectronAsarIntegrity Info.plist metadata for Resources/app.asar',
        algorithm: 'SHA256',
        hash: 'c'.repeat(64),
      }
    : {
        status: 'not-applicable',
        scope: 'ElectronAsarIntegrity Info.plist metadata for Resources/app.asar',
        reason: 'Info.plist metadata exists only in macOS application bundles.',
      }
  return {
    schemaVersion: 1,
    kind: 'ancestryllm-desktop-package-security-inspection',
    platform,
    package: { executable: '/package/app', application: '/package', resources: '/package/resources' },
    fuses: {
      status: 'verified',
      count: 1,
      items: [{ name: 'RunAsNode', expected: 'disabled', actual: 'disabled', status: 'verified' }],
    },
    asar: { path: '/package/resources/app.asar', presence: { status: 'verified' }, integrity },
  }
}

function faultEvidence(scenario, observations) {
  return {
    schemaVersion: 1,
    kind: 'ancestryllm-packaged-fault-evidence',
    scenario,
    status: 'passed',
    packageCopy: true,
    productionFaultHookUsed: false,
    observations,
  }
}

function fileGrantEvidence() {
  return {
    schemaVersion: 1,
    kind: 'ancestryllm-packaged-file-grant-evidence',
    status: 'passed',
    verificationOnlyDialogAdapter: true,
    observations: {
      openGrantOpaque: true,
      openMetadataValidated: true,
      saveGrantOpaque: true,
      replacementConfirmed: true,
      revocationPassed: true,
      selectedPathsAbsent: true,
    },
  }
}

function targetFixture(row, observed = metrics) {
  const [runner, sidecarTarget, expectedOs, actualOs, arch, hostArch] = row
  const metricsBytes = encoded(observed)
  const inspection = fuseInspection(TARGET_ROWS[runner].platform)
  const fuseInspectionBytes = encoded(inspection)
  const withholdEvidence = faultEvidence('sidecar-withhold-retry', {
    failure: 'startup_failed',
    automaticRestartsRemaining: 2,
    manualRetriesRemainingBefore: 1,
    recoveredState: 'ready',
    processExitedAfterWindowClose: true,
  })
  const restartEvidence = faultEvidence('sidecar-restart-exhaustion-quit', {
    automaticRestartCount: 2,
    exhaustedFailure: 'crash_loop',
    manualRetriesRemainingBefore: 1,
    manualRetryState: 'ready',
    activeSidecarExitedOnQuit: true,
    processExitedAfterWindowClose: true,
  })
  const integrityEvidence = faultEvidence('sidecar-integrity-substitution', {
    failure: 'startup_failed',
    automaticRestartsRemaining: 2,
    manualRetriesRemainingBefore: 1,
    manualRetryFailure: 'startup_failed',
    manualRetriesRemainingAfter: 0,
    verificationProcessTerminated: true,
  })
  const withholdEvidenceBytes = encoded(withholdEvidence)
  const restartEvidenceBytes = encoded(restartEvidence)
  const integrityEvidenceBytes = encoded(integrityEvidence)
  const fileGrantMediation = fileGrantEvidence()
  const fileGrantEvidenceBytes = encoded(fileGrantMediation)
  const runtimeReceipt = receiptRecord(
    ['packageRuntimePassed', 'rendererZeroEgressCanaryPassed'],
    { metrics: digest(metricsBytes) },
    [
      'node',
      'desktop/scripts/run-wdio.mjs',
      'packaged',
      '--grep',
      'exercises first run, persistence, corrupt preferences, security, and resource evidence',
    ],
  )
  const normalLaunchReceipt = receiptRecord(
    ['normalLaunchDebugSurfaceAbsentPassed'],
    {},
    [
      'node',
      'desktop/scripts/run-wdio.mjs',
      'packaged',
      '--grep',
      'launches production normally without a debugging transport',
    ],
  )
  const processTreeGuardReceipt = receiptRecord(
    ['sidecarProcessTreeGuardPassed'],
    {},
    ['python', '-m', 'pytest', 'tests/api/test_sidecar_bootstrap.py'],
  )
  const sidecarReceipt = receiptRecord(['sidecarSmokePassed'], {}, ['node', 'scripts/smoke-sidecar.mjs'])
  const fuseReceipt = receiptRecord(
    ['fusesInspectedPassed'],
    { fuseInspection: digest(fuseInspectionBytes) },
    ['node', 'scripts/inspect-package-fuses.mjs'],
  )
  const fileGrantReceipt = receiptRecord(
    ['packagedFileGrantSmokePassed'],
    { fileGrantEvidence: digest(fileGrantEvidenceBytes) },
    [
      'node',
      'desktop/scripts/run-wdio.mjs',
      'packaged',
      '--grep',
      'mediates opaque packaged open and save file grants',
    ],
  )
  const withholdReceipt = receiptRecord(
    ['packagedSidecarWithholdRetryPassed'],
    { faultEvidence: digest(withholdEvidenceBytes) },
    [
      'node',
      'desktop/scripts/run-wdio.mjs',
      'packaged',
      '--grep',
      'withholds and restores the packaged sidecar through Diagnostics retry',
    ],
  )
  const restartReceipt = receiptRecord(
    ['packagedSidecarRestartExhaustionQuitPassed'],
    { faultEvidence: digest(restartEvidenceBytes) },
    [
      'node',
      'desktop/scripts/run-wdio.mjs',
      'packaged',
      '--grep',
      'exhausts packaged sidecar restarts and exits cleanly',
    ],
  )
  const integrityReceipt = receiptRecord(
    ['packagedSidecarIntegritySubstitutionPassed'],
    {
      faultEvidence: digest(integrityEvidenceBytes),
      substitutedSidecar: { sha256: 'd'.repeat(64), bytes: 123 },
    },
    [
      'node',
      'desktop/scripts/run-wdio.mjs',
      'packaged',
      '--grep',
      'rejects a substituted packaged sidecar before launch',
    ],
  )
  const receiptRecords = [
    runtimeReceipt,
    normalLaunchReceipt,
    processTreeGuardReceipt,
    sidecarReceipt,
    fuseReceipt,
    fileGrantReceipt,
    withholdReceipt,
    restartReceipt,
    integrityReceipt,
  ]
  const evidence = createTargetEvidence({
    gitHead,
    runner,
    sidecarTarget,
    expectedOs,
    actualOs,
    arch,
    hostArch,
    packageBoundary: 'unpacked-native',
    metrics: observed,
    metricsBytes,
    fuseInspection: inspection,
    fuseInspectionBytes,
    fileGrantMediation,
    fileGrantEvidenceBytes,
    withholdEvidence,
    withholdEvidenceBytes,
    restartEvidence,
    restartEvidenceBytes,
    integrityEvidence,
    integrityEvidenceBytes,
    receiptRecords,
  })
  return {
    evidence,
    metricsBytes,
    fuseInspectionBytes,
    fileGrantEvidenceBytes,
    withholdEvidenceBytes,
    restartEvidenceBytes,
    integrityEvidenceBytes,
    receiptRecords,
  }
}

function securityFixture() {
  const sbomBytes = encoded({ bomFormat: 'CycloneDX', specVersion: '1.6' })
  const receipt = receiptRecord(
    SECURITY_RECEIPT_GATES,
    { sbom: digest(sbomBytes) },
    ['pnpm', 'run', 'security-verification'],
  )
  return {
    evidence: createSecurityEvidence({ gitHead, sbomBytes, receiptRecords: [receipt] }),
    sbomBytes,
    receiptRecords: [receipt],
  }
}

test('target evidence derives gates for the native Windows 11 ARM64 boundary only from exact receipts and its row', () => {
  const { evidence } = targetFixture(rows.find(([runner]) => runner === 'windows-11-arm'))
  assert.equal(evidence.platformValidated, true)
  assert.equal(evidence.artifactKind, 'unpublished-unpacked-native')
  assert.equal(evidence.packageRuntime, true)
  assert.equal(evidence.rendererZeroEgressCanary, true)
  assert.equal(evidence.normalLaunchDebugSurfaceAbsent, true)
  assert.equal(evidence.packagedFileGrantSmoke, true)
  assert.deepEqual(evidence.fileGrantMediation, fileGrantEvidence())
  assert.equal(evidence.signingVerified, false)
  assert.equal(evidence.hostArch, 'arm64')
  assert.equal(evidence.arch, 'arm64')
  assert.equal(
    evidence.faultScenarios['sidecar-withhold-retry'].observations.processExitedAfterWindowClose,
    true,
  )
  assert.equal(
    evidence.faultScenarios['sidecar-restart-exhaustion-quit'].observations.processExitedAfterWindowClose,
    true,
  )
  assert.equal(
    Object.hasOwn(
      evidence.faultScenarios['sidecar-withhold-retry'].observations,
      'cleanExit',
    ),
    false,
  )
  assert.equal(evidence.performance.policyVersion, PERFORMANCE_POLICY_VERSION)
  assert.deepEqual(evidence.gates, Object.fromEntries(TARGET_RECEIPT_GATES.map((gate) => [gate, true])))

  const fixture = targetFixture(rows[0])
  assert.throws(() => createTargetEvidence({
    gitHead,
    runner: rows[0][0],
    sidecarTarget: rows[0][1],
    expectedOs: rows[0][2],
    actualOs: rows[0][3],
    arch: rows[0][4],
    hostArch: rows[0][5],
    packageBoundary: 'unpacked-native',
    platformValidated: false,
    metrics,
    metricsBytes: fixture.metricsBytes,
    fuseInspection: JSON.parse(fixture.fuseInspectionBytes),
    fuseInspectionBytes: fixture.fuseInspectionBytes,
    fileGrantMediation: fileGrantEvidence(),
    fileGrantEvidenceBytes: fixture.fileGrantEvidenceBytes,
    withholdEvidence: JSON.parse(fixture.withholdEvidenceBytes),
    withholdEvidenceBytes: fixture.withholdEvidenceBytes,
    restartEvidence: JSON.parse(fixture.restartEvidenceBytes),
    restartEvidenceBytes: fixture.restartEvidenceBytes,
    integrityEvidence: JSON.parse(fixture.integrityEvidenceBytes),
    integrityEvidenceBytes: fixture.integrityEvidenceBytes,
    receiptRecords: fixture.receiptRecords,
  }), /platformValidated is derived/)
})

test('target evidence records every observed value, ceiling, and check and rejects exceeded or missing metrics', () => {
  const { evidence } = targetFixture(rows[0])
  assert.equal(evidence.performance.passed, true)
  for (const [name, observed] of Object.entries(metrics)) {
    assert.deepEqual(evidence.performance.checks[name], {
      observed,
      ceiling: TARGET_ROWS['macos-15'].ceilings[name],
      passed: true,
    })
  }

  assert.throws(() => targetFixture(rows[0], {
    ...metrics,
    coldLaunchMs: TARGET_ROWS['macos-15'].ceilings.coldLaunchMs + 1,
  }), /coldLaunchMs exceeded/)
  const missing = { ...metrics }
  delete missing.readyMs
  assert.throws(() => targetFixture(rows[0], missing), /exact schema/)
})

test('target evidence rejects a digest-unbound artifact and the wrong platform ASAR scope', () => {
  const fixture = targetFixture(rows[0])
  assert.throws(() => createTargetEvidence({
    gitHead,
    runner: rows[0][0],
    sidecarTarget: rows[0][1],
    expectedOs: rows[0][2],
    actualOs: rows[0][3],
    arch: rows[0][4],
    hostArch: rows[0][5],
    packageBoundary: 'unpacked-native',
    metrics,
    metricsBytes: Buffer.from('{}'),
    fuseInspection: JSON.parse(fixture.fuseInspectionBytes),
    fuseInspectionBytes: fixture.fuseInspectionBytes,
    fileGrantMediation: fileGrantEvidence(),
    fileGrantEvidenceBytes: fixture.fileGrantEvidenceBytes,
    withholdEvidence: JSON.parse(fixture.withholdEvidenceBytes),
    withholdEvidenceBytes: fixture.withholdEvidenceBytes,
    restartEvidence: JSON.parse(fixture.restartEvidenceBytes),
    restartEvidenceBytes: fixture.restartEvidenceBytes,
    integrityEvidence: JSON.parse(fixture.integrityEvidenceBytes),
    integrityEvidenceBytes: fixture.integrityEvidenceBytes,
    receiptRecords: fixture.receiptRecords,
  }), /not the artifact produced/)

  const linuxRow = rows.find(([runner]) => runner === 'ubuntu-24.04')
  const linuxFixture = targetFixture(linuxRow)
  const wrong = JSON.parse(linuxFixture.fuseInspectionBytes)
  wrong.asar.integrity.status = 'verified'
  const wrongBytes = encoded(wrong)
  const records = linuxFixture.receiptRecords.map((record) => (
    record.receipt.gates.includes('fusesInspectedPassed')
      ? receiptRecord(['fusesInspectedPassed'], { fuseInspection: digest(wrongBytes) })
      : record
  ))
  assert.throws(() => createTargetEvidence({
    gitHead,
    runner: linuxRow[0],
    sidecarTarget: linuxRow[1],
    expectedOs: linuxRow[2],
    actualOs: linuxRow[3],
    arch: linuxRow[4],
    hostArch: linuxRow[5],
    packageBoundary: 'unpacked-native',
    metrics,
    metricsBytes: linuxFixture.metricsBytes,
    fuseInspection: wrong,
    fuseInspectionBytes: wrongBytes,
    fileGrantMediation: fileGrantEvidence(),
    fileGrantEvidenceBytes: linuxFixture.fileGrantEvidenceBytes,
    withholdEvidence: JSON.parse(linuxFixture.withholdEvidenceBytes),
    withholdEvidenceBytes: linuxFixture.withholdEvidenceBytes,
    restartEvidence: JSON.parse(linuxFixture.restartEvidenceBytes),
    restartEvidenceBytes: linuxFixture.restartEvidenceBytes,
    integrityEvidence: JSON.parse(linuxFixture.integrityEvidenceBytes),
    integrityEvidenceBytes: linuxFixture.integrityEvidenceBytes,
    receiptRecords: records,
  }), /not-applicable/)
})

test('security evidence derives all gates from successful receipts and binds the SBOM digest', () => {
  const { evidence, sbomBytes, receiptRecords } = securityFixture()
  assert.match(evidence.sbom.sha256, /^[0-9a-f]{64}$/)
  assert.equal(evidence.sbom.bytes, sbomBytes.byteLength)
  assert.deepEqual(evidence.gates, Object.fromEntries(SECURITY_RECEIPT_GATES.map((gate) => [gate, true])))

  assert.throws(() => createSecurityEvidence({
    gitHead,
    sbomBytes: Buffer.from('{"bomFormat":"CycloneDX","tampered":true}'),
    receiptRecords,
  }), /not the artifact produced/)
  assert.throws(() => createSecurityEvidence({
    gitHead,
    sbomBytes,
    receiptRecords: receiptRecords.map((record) => ({
      ...record,
      receipt: { ...record.receipt, gates: record.receipt.gates.filter((gate) => gate !== 'auditPassed') },
    })),
  }), /auditPassed/)
})

test('aggregate requires six exact-head rows, security, raw receipts, and raw bound artifacts', async () => {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-evidence-'))
  const targetsRoot = join(root, 'targets')
  await mkdir(targetsRoot)
  for (const row of rows) {
    const runnerRoot = join(targetsRoot, row[0])
    await mkdir(runnerRoot)
    const fixture = targetFixture(row)
    await writeFile(join(runnerRoot, 'evidence.json'), encoded(fixture.evidence))
    await writeFile(join(runnerRoot, 'metrics.json'), fixture.metricsBytes)
    await writeFile(join(runnerRoot, 'fuse-inspection.json'), fixture.fuseInspectionBytes)
    await writeFile(join(runnerRoot, 'file-grant-mediation.json'), fixture.fileGrantEvidenceBytes)
    await writeFile(join(runnerRoot, 'sidecar-withhold-retry.json'), fixture.withholdEvidenceBytes)
    await writeFile(join(runnerRoot, 'sidecar-restart-exhaustion-quit.json'), fixture.restartEvidenceBytes)
    await writeFile(join(runnerRoot, 'sidecar-integrity-substitution.json'), fixture.integrityEvidenceBytes)
    for (const [index, receipt] of fixture.receiptRecords.entries()) {
      await writeFile(join(runnerRoot, `receipt-${index}.json`), receipt.raw)
    }
  }
  const securityRoot = join(root, 'security')
  await mkdir(securityRoot)
  const security = securityFixture()
  await writeFile(join(securityRoot, 'evidence.json'), encoded(security.evidence))
  await writeFile(join(securityRoot, 'sbom.json'), security.sbomBytes)
  await writeFile(join(securityRoot, 'receipt.json'), security.receiptRecords[0].raw)

  const aggregate = await aggregateEvidence(root, gitHead)
  assert.equal(aggregate.targets.length, 6)
  assert.equal(aggregate.platformValidated, true)
  assert.equal(aggregate.status, 'passed')
  assert.deepEqual(aggregate.publicationRequirements, { desktopInstaller: true })

  await writeFile(join(targetsRoot, 'windows-11-arm', 'evidence.json'), encoded({
    ...aggregate.targets.find((target) => target.runner === 'windows-11-arm'),
    platformValidated: false,
  }))
  await assert.rejects(aggregateEvidence(root, gitHead), /platformValidated/)
})

test('target evidence CLI rejects literal platform-validation booleans', async () => {
  await assert.rejects(runCli([
    'target',
    '--platform-validated', 'true',
  ]), /Unknown evidence option: --platform-validated/)
})
