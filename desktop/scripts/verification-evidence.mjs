/** Aggregates platform verification receipts into deterministic release evidence. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { promisify } from 'node:util'
import { fileURLToPath, pathToFileURL } from 'node:url'
import {
  SECURITY_RECEIPT_GATES,
  TARGET_RECEIPT_GATES,
  loadVerificationReceipts,
  validateVerificationReceipt,
} from './verification-receipt.mjs'

const execFileAsync = promisify(execFile)
const SHA = /^[0-9a-f]{40}$/
const SHA256 = /^[0-9a-f]{64}$/
const EVIDENCE_SCHEMA_VERSION = 2
const FUSE_INSPECTION_KIND = 'ancestryllm-desktop-package-security-inspection'
const FAULT_EVIDENCE_KIND = 'ancestryllm-packaged-fault-evidence'
const FILE_GRANT_EVIDENCE_KIND = 'ancestryllm-packaged-file-grant-evidence'
const METRIC_NAMES = Object.freeze([
  'coldLaunchMs',
  'warmLaunchMs',
  'readyMs',
  'rssBytes',
  'rendererOutboundRequests',
])

/**
 * Identifies the immutable performance-threshold contract used by release evidence.
 * @type {string}
 */
export const PERFORMANCE_POLICY_VERSION = 'desktop-unpacked-v1'

function ceilings(coldLaunchMs, warmLaunchMs, readyMs, rssBytes) {
  return Object.freeze({
    coldLaunchMs,
    warmLaunchMs,
    readyMs,
    rssBytes,
    rendererOutboundRequests: 0,
  })
}

const macAndLinuxCeilings = ceilings(30_000, 20_000, 45_000, 1_610_612_736)
const windowsCeilings = ceilings(45_000, 30_000, 60_000, 2_147_483_648)

/**
 * Defines the exact native runner, architecture, operating-system, and performance rows accepted as release evidence.
 * @type {Readonly<Record<string, Readonly<Record<string, unknown>>>>}
 */
export const TARGET_ROWS = Object.freeze({
  'macos-15': Object.freeze({ sidecarTarget: 'darwin-arm64', platform: 'darwin', expectedOs: 'macOS 15', actualOs: 'macOS 15', arch: 'arm64', hostArch: 'arm64', platformValidated: true, ceilings: macAndLinuxCeilings }),
  'macos-15-intel': Object.freeze({ sidecarTarget: 'darwin-x64', platform: 'darwin', expectedOs: 'macOS 15', actualOs: 'macOS 15', arch: 'x64', hostArch: 'x64', platformValidated: true, ceilings: macAndLinuxCeilings }),
  'macos-26': Object.freeze({ sidecarTarget: 'darwin-arm64', platform: 'darwin', expectedOs: 'macOS 26', actualOs: 'macOS 26', arch: 'arm64', hostArch: 'arm64', platformValidated: true, ceilings: macAndLinuxCeilings }),
  'macos-26-intel': Object.freeze({ sidecarTarget: 'darwin-x64', platform: 'darwin', expectedOs: 'macOS 26', actualOs: 'macOS 26', arch: 'x64', hostArch: 'x64', platformValidated: true, ceilings: macAndLinuxCeilings }),
  'windows-11-arm': Object.freeze({ sidecarTarget: 'win32-arm64', platform: 'win32', expectedOs: 'Windows 11', actualOs: 'Windows 11', arch: 'arm64', hostArch: 'arm64', platformValidated: true, ceilings: windowsCeilings }),
  'ubuntu-24.04': Object.freeze({ sidecarTarget: 'linux-x64', platform: 'linux', expectedOs: 'Ubuntu 24.04', actualOs: 'Ubuntu 24.04', arch: 'x64', hostArch: 'x64', platformValidated: true, ceilings: macAndLinuxCeilings }),
})

function exactHead(value, label = 'gitHead') {
  assert.match(value, SHA, `${label} must be a lowercase full Git commit SHA`)
  return value
}

function artifactDigest(bytes) {
  assert.equal(bytes instanceof Uint8Array, true, 'artifact bytes must be a Uint8Array')
  return Object.freeze({
    sha256: createHash('sha256').update(bytes).digest('hex'),
    bytes: bytes.byteLength,
  })
}

function validateDigest(value, label, { nonempty = true } = {}) {
  assert.deepEqual(Object.keys(value ?? {}).sort(), ['bytes', 'sha256'], `${label} must use the exact digest schema`)
  assert.match(value.sha256, SHA256, `${label}.sha256 must be a lowercase SHA-256 digest`)
  assert.equal(
    Number.isSafeInteger(value.bytes) && value.bytes >= (nonempty ? 1 : 0),
    true,
    `${label}.bytes must be a ${nonempty ? 'positive' : 'non-negative'} safe integer`,
  )
  return value
}

function receiptSummary(record, gitHead) {
  const receipt = validateVerificationReceipt(record.receipt, gitHead)
  validateDigest(record.file, 'receipt file')
  return Object.freeze({
    ...receipt,
    receiptFile: Object.freeze({ ...record.file }),
  })
}

function deriveReceiptGates(records, requiredGates, gitHead) {
  assert.equal(Array.isArray(records) && records.length > 0, true, 'verification receipt records are required')
  const expected = new Set(requiredGates)
  for (const record of records) {
    validateVerificationReceipt(record.receipt, gitHead)
    for (const gate of record.receipt.gates) {
      assert.equal(expected.has(gate), true, `receipt claims out-of-scope gate ${gate}`)
    }
  }

  const gates = {}
  const receipts = {}
  for (const gate of requiredGates) {
    const matches = records.filter((record) => record.receipt.gates.includes(gate))
    assert.equal(matches.length, 1, `expected exactly one successful exact-head receipt for ${gate}`)
    gates[gate] = true
    receipts[gate] = receiptSummary(matches[0], gitHead)
  }
  return Object.freeze({ gates: Object.freeze(gates), receipts: Object.freeze(receipts) })
}

function assertArtifactBound(receipt, artifactName, observed, label) {
  const expected = receipt.artifacts?.[artifactName]
  validateDigest(expected, `${label} receipt artifact ${artifactName}`)
  assert.deepEqual(expected, observed, `${label} is not the artifact produced by its successful receipt command`)
}

function validatedMetrics(metrics) {
  assert.equal(metrics !== null && typeof metrics === 'object' && !Array.isArray(metrics), true, 'metrics must be an object')
  assert.deepEqual(Object.keys(metrics).sort(), [...METRIC_NAMES].sort(), 'metrics must use the exact schema')
  for (const name of METRIC_NAMES) {
    assert.equal(Number.isFinite(metrics[name]) && metrics[name] >= 0, true, `${name} must be a finite non-negative number`)
  }
  assert.equal(Number.isSafeInteger(metrics.rssBytes), true, 'rssBytes must be a safe integer')
  assert.equal(Number.isSafeInteger(metrics.rendererOutboundRequests), true, 'rendererOutboundRequests must be a safe integer')
  return Object.freeze(Object.fromEntries(METRIC_NAMES.map((name) => [name, metrics[name]])))
}

function performanceEvidence(runner, metrics) {
  const observed = validatedMetrics(metrics)
  const policy = TARGET_ROWS[runner]
  assert.ok(policy, `Unsupported runner: ${runner}`)
  const checks = {}
  for (const name of METRIC_NAMES) {
    const passed = observed[name] <= policy.ceilings[name]
    checks[name] = Object.freeze({
      observed: observed[name],
      ceiling: policy.ceilings[name],
      passed,
    })
    assert.equal(
      passed,
      true,
      `${runner} ${name} exceeded ${PERFORMANCE_POLICY_VERSION} ceiling (${observed[name]} > ${policy.ceilings[name]})`,
    )
  }
  return Object.freeze({
    policyVersion: PERFORMANCE_POLICY_VERSION,
    runner,
    platform: policy.sidecarTarget,
    observed,
    ceilings: policy.ceilings,
    checks: Object.freeze(checks),
    passed: true,
  })
}

function validatedFuseInspection(value, target) {
  assert.deepEqual(
    Object.keys(value ?? {}).sort(),
    ['asar', 'fuses', 'kind', 'package', 'platform', 'schemaVersion'],
    'fuse inspection must use the exact schema',
  )
  assert.equal(value.schemaVersion, 1, 'unsupported fuse inspection schema')
  assert.equal(value.kind, FUSE_INSPECTION_KIND, 'unexpected fuse inspection kind')
  assert.equal(value.platform, target.platform, 'fuse inspection platform does not match the target')
  assert.equal(value.package !== null && typeof value.package === 'object', true, 'fuse inspection package metadata is missing')
  for (const field of ['executable', 'application', 'resources']) {
    assert.equal(typeof value.package[field] === 'string' && value.package[field].length > 0, true, `fuse inspection package.${field} is missing`)
  }
  assert.equal(value.fuses?.status, 'verified', 'packaged Electron fuses were not verified')
  assert.equal(Number.isSafeInteger(value.fuses?.count) && value.fuses.count > 0, true, 'fuse inspection count is invalid')
  assert.equal(Array.isArray(value.fuses?.items), true, 'fuse inspection items are missing')
  assert.equal(value.fuses.items.length, value.fuses.count, 'fuse inspection count does not match its items')
  for (const item of value.fuses.items) {
    assert.equal(typeof item.name === 'string' && item.name.length > 0, true, 'fuse inspection item name is missing')
    assert.equal(item.status, 'verified', `fuse ${item.name} was not verified`)
    assert.equal(item.actual, item.expected, `fuse ${item.name} does not match its expected state`)
  }
  assert.equal(value.asar?.presence?.status, 'verified', 'packaged app.asar presence was not verified')
  assert.equal(typeof value.asar?.integrity?.scope === 'string' && value.asar.integrity.scope.length > 0, true, 'ASAR integrity scope is missing')

  let integrity
  if (target.platform === 'darwin') {
    assert.equal(value.asar.integrity.status, 'verified', 'macOS ASAR integrity metadata was not verified')
    assert.equal(value.asar.integrity.algorithm, 'SHA256', 'macOS ASAR integrity metadata must use SHA256')
    assert.match(value.asar.integrity.hash, SHA256, 'macOS ASAR integrity metadata has no valid hash')
    integrity = Object.freeze({
      status: value.asar.integrity.status,
      scope: value.asar.integrity.scope,
      algorithm: value.asar.integrity.algorithm,
      hash: value.asar.integrity.hash,
    })
  } else {
    assert.equal(value.asar.integrity.status, 'not-applicable', 'non-macOS ASAR integrity scope must be explicitly not applicable')
    assert.equal(typeof value.asar.integrity.reason === 'string' && value.asar.integrity.reason.length > 0, true, 'non-macOS ASAR integrity scope requires a reason')
    integrity = Object.freeze({
      status: value.asar.integrity.status,
      scope: value.asar.integrity.scope,
      reason: value.asar.integrity.reason,
    })
  }

  return Object.freeze({
    schemaVersion: value.schemaVersion,
    kind: value.kind,
    platform: value.platform,
    fuses: Object.freeze({
      status: value.fuses.status,
      count: value.fuses.count,
      items: Object.freeze(value.fuses.items.map((item) => Object.freeze({
        name: item.name,
        expected: item.expected,
        actual: item.actual,
        status: item.status,
      }))),
    }),
    asar: Object.freeze({
      presence: Object.freeze({ status: value.asar.presence.status }),
      integrity,
    }),
  })
}

const FAULT_SCENARIOS = Object.freeze({
  packagedSidecarWithholdRetryPassed: Object.freeze({
    name: 'sidecar-withhold-retry',
    artifact: 'withholdEvidence',
    observations: Object.freeze({
      failure: 'startup_failed',
      automaticRestartsRemaining: 2,
      manualRetriesRemainingBefore: 1,
      recoveredState: 'ready',
      processExitedAfterWindowClose: true,
    }),
  }),
  packagedSidecarRestartExhaustionQuitPassed: Object.freeze({
    name: 'sidecar-restart-exhaustion-quit',
    artifact: 'restartEvidence',
    observations: Object.freeze({
      automaticRestartCount: 2,
      exhaustedFailure: 'crash_loop',
      manualRetriesRemainingBefore: 1,
      manualRetryState: 'ready',
      activeSidecarExitedOnQuit: true,
      processExitedAfterWindowClose: true,
    }),
  }),
  packagedSidecarIntegritySubstitutionPassed: Object.freeze({
    name: 'sidecar-integrity-substitution',
    artifact: 'integrityEvidence',
    observations: Object.freeze({
      failure: 'startup_failed',
      automaticRestartsRemaining: 2,
      manualRetriesRemainingBefore: 1,
      manualRetryFailure: 'startup_failed',
      manualRetriesRemainingAfter: 0,
      verificationProcessTerminated: true,
    }),
  }),
})

const FILE_GRANT_OBSERVATIONS = Object.freeze({
  openGrantOpaque: true,
  openMetadataValidated: true,
  saveGrantOpaque: true,
  replacementConfirmed: true,
  revocationPassed: true,
  selectedPathsAbsent: true,
})

function validatedFileGrantEvidence(value) {
  assert.deepEqual(
    Object.keys(value ?? {}).sort(),
    ['kind', 'observations', 'schemaVersion', 'status', 'verificationOnlyDialogAdapter'].sort(),
    'packaged file-grant evidence must use the exact schema',
  )
  assert.equal(value.schemaVersion, 1, 'packaged file-grant evidence has an unsupported schema')
  assert.equal(value.kind, FILE_GRANT_EVIDENCE_KIND, 'packaged file-grant evidence has the wrong kind')
  assert.equal(value.status, 'passed', 'packaged file-grant evidence did not pass')
  assert.equal(value.verificationOnlyDialogAdapter, true, 'packaged file-grant evidence did not use the verification-only dialog adapter')
  assert.deepEqual(
    Object.keys(value.observations ?? {}).sort(),
    Object.keys(FILE_GRANT_OBSERVATIONS).sort(),
    'packaged file-grant observations must use the exact schema',
  )
  assert.deepEqual(value.observations, FILE_GRANT_OBSERVATIONS, 'packaged file-grant observations are incomplete')
  return Object.freeze({
    schemaVersion: value.schemaVersion,
    kind: value.kind,
    status: value.status,
    verificationOnlyDialogAdapter: value.verificationOnlyDialogAdapter,
    observations: Object.freeze({ ...value.observations }),
  })
}

function validatedFaultEvidence(value, expected) {
  assert.deepEqual(
    Object.keys(value ?? {}).sort(),
    [
      'kind',
      'observations',
      'packageCopy',
      'productionFaultHookUsed',
      'scenario',
      'schemaVersion',
      'status',
    ],
    `${expected.name} fault evidence must use the exact schema`,
  )
  assert.equal(value.schemaVersion, 1, `${expected.name} fault evidence has an unsupported schema`)
  assert.equal(value.kind, FAULT_EVIDENCE_KIND, `${expected.name} fault evidence has the wrong kind`)
  assert.equal(value.scenario, expected.name, `${expected.name} fault evidence has the wrong scenario`)
  assert.equal(value.status, 'passed', `${expected.name} fault evidence did not pass`)
  assert.equal(value.packageCopy, true, `${expected.name} did not mutate a verification-only package copy`)
  assert.equal(value.productionFaultHookUsed, false, `${expected.name} used a production fault hook`)
  assert.deepEqual(value.observations, expected.observations, `${expected.name} observations are incomplete`)
  return Object.freeze({
    schemaVersion: value.schemaVersion,
    kind: value.kind,
    scenario: value.scenario,
    status: value.status,
    packageCopy: value.packageCopy,
    productionFaultHookUsed: value.productionFaultHookUsed,
    observations: Object.freeze({ ...value.observations }),
  })
}

function validateTargetRow(input) {
  const expected = TARGET_ROWS[input.runner]
  assert.ok(expected, `Unsupported runner: ${input.runner}`)
  for (const field of ['sidecarTarget', 'expectedOs', 'actualOs', 'arch', 'hostArch']) {
    assert.equal(input[field], expected[field], `${field} does not match the supported target row`)
  }
  assert.equal(input.packageBoundary, 'unpacked-native', 'packageBoundary must be unpacked-native')
  assert.equal(input.platformValidated, undefined, 'platformValidated is derived from the executed target and must not be supplied')
  return expected
}

/**
 * Derives one native-target evidence record from exact-head receipts and downloaded artifacts.
 * @param {Record<string, any>} input - Runner identity, receipts, metrics, fuse inspection, file-grant, and fault evidence.
 * @returns {Readonly<Record<string, unknown>>} Schema-v2 target evidence with independently derived gate results and artifact digests.
 */
export function createTargetEvidence(input) {
  const gitHead = exactHead(input.gitHead)
  const expected = validateTargetRow(input)
  const derived = deriveReceiptGates(input.receiptRecords, TARGET_RECEIPT_GATES, gitHead)
  const metricsArtifact = artifactDigest(input.metricsBytes)
  const fuseInspectionArtifact = artifactDigest(input.fuseInspectionBytes)
  const fileGrantEvidenceArtifact = artifactDigest(input.fileGrantEvidenceBytes)
  const fileGrantMediation = validatedFileGrantEvidence(input.fileGrantMediation)
  const faultArtifacts = Object.fromEntries(Object.entries(FAULT_SCENARIOS).map(([gate, scenario]) => {
    const bytes = input[`${scenario.artifact}Bytes`]
    const document = input[scenario.artifact]
    const artifact = artifactDigest(bytes)
    validatedFaultEvidence(document, scenario)
    assertArtifactBound(derived.receipts[gate], 'faultEvidence', artifact, scenario.name)
    return [scenario.artifact, artifact]
  }))
  validateDigest(
    derived.receipts.packagedSidecarIntegritySubstitutionPassed.artifacts?.substitutedSidecar,
    'sidecar-integrity-substitution receipt artifact substitutedSidecar',
  )
  assertArtifactBound(derived.receipts.packageRuntimePassed, 'metrics', metricsArtifact, 'packaged runtime metrics')
  assertArtifactBound(derived.receipts.fusesInspectedPassed, 'fuseInspection', fuseInspectionArtifact, 'fuse inspection')
  assertArtifactBound(
    derived.receipts.packagedFileGrantSmokePassed,
    'fileGrantEvidence',
    fileGrantEvidenceArtifact,
    'packaged file-grant mediation',
  )
  const performance = performanceEvidence(input.runner, input.metrics)
  const inspection = validatedFuseInspection(input.fuseInspection, expected)

  return Object.freeze({
    schemaVersion: EVIDENCE_SCHEMA_VERSION,
    kind: 'target',
    gitHead,
    runner: input.runner,
    sidecarTarget: input.sidecarTarget,
    expectedOs: input.expectedOs,
    actualOs: input.actualOs,
    arch: input.arch,
    hostArch: input.hostArch,
    packageBoundary: input.packageBoundary,
    platformValidated: expected.platformValidated,
    artifactKind: 'unpublished-unpacked-native',
    signingVerified: false,
    packageRuntime: derived.gates.packageRuntimePassed,
    sidecarSmoke: derived.gates.sidecarSmokePassed,
    fusesInspected: derived.gates.fusesInspectedPassed,
    rendererZeroEgressCanary: derived.gates.rendererZeroEgressCanaryPassed,
    normalLaunchDebugSurfaceAbsent: derived.gates.normalLaunchDebugSurfaceAbsentPassed,
    packagedFileGrantSmoke: derived.gates.packagedFileGrantSmokePassed,
    performancePassed: performance.passed,
    gates: derived.gates,
    receipts: derived.receipts,
    artifacts: Object.freeze({
      metrics: metricsArtifact,
      fuseInspection: fuseInspectionArtifact,
      fileGrantEvidence: fileGrantEvidenceArtifact,
      ...faultArtifacts,
    }),
    fileGrantMediation,
    faultScenarios: Object.freeze(Object.fromEntries(Object.entries(FAULT_SCENARIOS).map(([, scenario]) => [
      scenario.name,
      validatedFaultEvidence(input[scenario.artifact], scenario),
    ]))),
    performance,
    inspection,
  })
}

/**
 * Derives the security evidence record from exact-head security receipts and the bound SBOM.
 * @param {Record<string, any>} input - Exact Git head, receipt records, and SBOM bytes.
 * @returns {Readonly<Record<string, unknown>>} Schema-v2 security evidence with derived gates and SBOM digest.
 */
export function createSecurityEvidence(input) {
  const gitHead = exactHead(input.gitHead)
  const derived = deriveReceiptGates(input.receiptRecords, SECURITY_RECEIPT_GATES, gitHead)
  const sbom = artifactDigest(input.sbomBytes)
  assertArtifactBound(derived.receipts.sbomGeneratedPassed, 'sbom', sbom, 'SBOM')
  return Object.freeze({
    schemaVersion: EVIDENCE_SCHEMA_VERSION,
    kind: 'security',
    gitHead,
    gates: derived.gates,
    receipts: derived.receipts,
    sbom,
  })
}

async function jsonFiles(root) {
  const output = []
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) output.push(...await jsonFiles(path))
    else if (entry.isFile() && entry.name.endsWith('.json')) output.push(path)
  }
  return output
}

function validateReceiptSummary(summary, gate, gitHead, receiptRecords) {
  assert.equal(summary !== null && typeof summary === 'object' && !Array.isArray(summary), true, `receipt summary for ${gate} is missing`)
  const { receiptFile, ...receipt } = summary
  validateDigest(receiptFile, `${gate} receipt file`)
  validateVerificationReceipt(receipt, gitHead)
  assert.equal(receipt.gates.includes(gate), true, `${gate} receipt does not claim its evidence gate`)
  const matches = receiptRecords.filter((record) => (
    record.file.sha256 === receiptFile.sha256
    && record.file.bytes === receiptFile.bytes
    && JSON.stringify(record.receipt) === JSON.stringify(receipt)
  ))
  assert.equal(matches.length > 0, true, `${gate} evidence is not backed by a downloaded exact receipt`)
  return receipt
}

function validateDerivedReceipts(value, requiredGates, gitHead, receiptRecords) {
  assert.deepEqual(value.gates, Object.fromEntries(requiredGates.map((gate) => [gate, true])), 'evidence gate set is incomplete')
  assert.deepEqual(Object.keys(value.receipts ?? {}), [...requiredGates], 'evidence receipt set is incomplete')
  const receipts = {}
  for (const gate of requiredGates) receipts[gate] = validateReceiptSummary(value.receipts[gate], gate, gitHead, receiptRecords)
  return receipts
}

function findArtifactFile(files, expected, label) {
  validateDigest(expected, label)
  const matches = files.filter((file) => file.digest.sha256 === expected.sha256 && file.digest.bytes === expected.bytes)
  assert.equal(matches.length > 0, true, `${label} was not downloaded with the evidence`)
  return matches[0]
}

function validateTargetEvidence(value, gitHead, receiptRecords, files) {
  assert.equal(value.schemaVersion, EVIDENCE_SCHEMA_VERSION, 'unsupported target evidence schema')
  assert.equal(value.kind, 'target')
  assert.equal(value.gitHead, gitHead, `${value.runner ?? 'target'} evidence is not from the exact head`)
  const expected = TARGET_ROWS[value.runner]
  assert.ok(expected, `Unsupported runner: ${value.runner}`)
  for (const field of ['sidecarTarget', 'expectedOs', 'actualOs', 'arch', 'hostArch']) {
    assert.equal(value[field], expected[field], `${value.runner} ${field} does not match the supported target row`)
  }
  assert.equal(value.packageBoundary, 'unpacked-native')
  assert.equal(value.platformValidated, expected.platformValidated, `${value.runner} platformValidated is not derived from the executed target`)
  assert.equal(value.artifactKind, 'unpublished-unpacked-native')
  assert.equal(value.signingVerified, false)
  const receipts = validateDerivedReceipts(value, TARGET_RECEIPT_GATES, gitHead, receiptRecords)
  assert.equal(value.packageRuntime, value.gates.packageRuntimePassed)
  assert.equal(value.sidecarSmoke, value.gates.sidecarSmokePassed)
  assert.equal(value.fusesInspected, value.gates.fusesInspectedPassed)
  assert.equal(value.rendererZeroEgressCanary, value.gates.rendererZeroEgressCanaryPassed)
  assert.equal(value.normalLaunchDebugSurfaceAbsent, value.gates.normalLaunchDebugSurfaceAbsentPassed)
  assert.equal(value.packagedFileGrantSmoke, value.gates.packagedFileGrantSmokePassed)
  assert.equal(value.performancePassed, true)

  validateDigest(value.artifacts?.metrics, `${value.runner} metrics artifact`)
  validateDigest(value.artifacts?.fuseInspection, `${value.runner} fuse inspection artifact`)
  validateDigest(value.artifacts?.fileGrantEvidence, `${value.runner} packaged file-grant evidence artifact`)
  assertArtifactBound(receipts.packageRuntimePassed, 'metrics', value.artifacts.metrics, `${value.runner} packaged runtime metrics`)
  assertArtifactBound(receipts.fusesInspectedPassed, 'fuseInspection', value.artifacts.fuseInspection, `${value.runner} fuse inspection`)
  assertArtifactBound(
    receipts.packagedFileGrantSmokePassed,
    'fileGrantEvidence',
    value.artifacts.fileGrantEvidence,
    `${value.runner} packaged file-grant mediation`,
  )

  const fileGrantEvidenceFile = findArtifactFile(
    files,
    value.artifacts.fileGrantEvidence,
    `${value.runner} packaged file-grant evidence artifact`,
  )
  assert.deepEqual(
    value.fileGrantMediation,
    validatedFileGrantEvidence(fileGrantEvidenceFile.value),
    `${value.runner} packaged file-grant evidence differs from its downloaded artifact`,
  )

  for (const [gate, scenario] of Object.entries(FAULT_SCENARIOS)) {
    const artifact = value.artifacts?.[scenario.artifact]
    validateDigest(artifact, `${value.runner} ${scenario.name} fault evidence artifact`)
    assertArtifactBound(receipts[gate], 'faultEvidence', artifact, `${value.runner} ${scenario.name}`)
    const artifactFile = findArtifactFile(files, artifact, `${value.runner} ${scenario.name} fault evidence artifact`)
    assert.deepEqual(
      value.faultScenarios?.[scenario.name],
      validatedFaultEvidence(artifactFile.value, scenario),
      `${value.runner} ${scenario.name} evidence differs from its downloaded artifact`,
    )
  }
  validateDigest(
    receipts.packagedSidecarIntegritySubstitutionPassed.artifacts?.substitutedSidecar,
    `${value.runner} sidecar-integrity-substitution receipt artifact substitutedSidecar`,
  )

  const metricsFile = findArtifactFile(files, value.artifacts.metrics, `${value.runner} metrics artifact`)
  assert.deepEqual(metricsFile.value, value.performance?.observed, `${value.runner} observed metrics differ from the downloaded metrics artifact`)
  assert.deepEqual(value.performance, performanceEvidence(value.runner, metricsFile.value), `${value.runner} performance evidence differs from policy`)

  const inspectionFile = findArtifactFile(files, value.artifacts.fuseInspection, `${value.runner} fuse inspection artifact`)
  assert.deepEqual(value.inspection, validatedFuseInspection(inspectionFile.value, expected), `${value.runner} inspection evidence differs from its downloaded artifact`)
  return value
}

function validateSecurityEvidence(value, gitHead, receiptRecords, files) {
  assert.equal(value.schemaVersion, EVIDENCE_SCHEMA_VERSION, 'unsupported security evidence schema')
  assert.equal(value.kind, 'security')
  assert.equal(value.gitHead, gitHead, 'security evidence is not from the exact head')
  const receipts = validateDerivedReceipts(value, SECURITY_RECEIPT_GATES, gitHead, receiptRecords)
  validateDigest(value.sbom, 'security SBOM')
  assertArtifactBound(receipts.sbomGeneratedPassed, 'sbom', value.sbom, 'SBOM')
  const sbomFile = findArtifactFile(files, value.sbom, 'security SBOM')
  assert.equal(sbomFile.value?.bomFormat, 'CycloneDX', 'downloaded SBOM is not a CycloneDX document')
  return value
}

/**
 * Validates and aggregates exactly six target rows plus one security row from downloaded evidence.
 * @param {string} root - Directory containing receipts, evidence JSON, and referenced artifacts.
 * @param {string} requestedHead - Full Git commit that every record must match.
 * @returns {Promise<Readonly<Record<string, unknown>>>} Deterministically ordered schema-v2 release evidence.
 */
export async function aggregateEvidence(root, requestedHead) {
  const gitHead = exactHead(requestedHead)
  const evidence = []
  const receiptRecords = []
  const files = []
  for (const path of await jsonFiles(root)) {
    let bytes
    let value
    try {
      bytes = await readFile(path)
      value = JSON.parse(bytes.toString('utf8'))
    } catch {
      continue
    }
    const digest = artifactDigest(bytes)
    files.push(Object.freeze({ path, value, digest }))
    if (value?.kind === 'verification-receipt') {
      receiptRecords.push(Object.freeze({ path, receipt: validateVerificationReceipt(value, gitHead), file: digest }))
    } else if (value?.schemaVersion === EVIDENCE_SCHEMA_VERSION && (value.kind === 'target' || value.kind === 'security')) {
      evidence.push(value)
    }
  }

  const targets = evidence
    .filter((value) => value.kind === 'target')
    .map((value) => validateTargetEvidence(value, gitHead, receiptRecords, files))
  const security = evidence.filter((value) => value.kind === 'security')
  assert.equal(targets.length, Object.keys(TARGET_ROWS).length, 'expected exactly six target evidence rows')
  assert.equal(security.length, 1, 'expected exactly one security evidence row')
  assert.equal(new Set(targets.map((target) => target.runner)).size, targets.length, 'duplicate target evidence row')
  assert.deepEqual(targets.map((target) => target.runner).sort(), Object.keys(TARGET_ROWS).sort(), 'target evidence matrix is incomplete')
  const checkedSecurity = validateSecurityEvidence(security[0], gitHead, receiptRecords, files)

  return Object.freeze({
    schemaVersion: EVIDENCE_SCHEMA_VERSION,
    kind: 'aggregate',
    gitHead,
    status: 'passed',
    platformValidated: true,
    targets: Object.freeze(targets.sort((left, right) => left.runner.localeCompare(right.runner))),
    security: checkedSecurity,
    publicationRequirements: Object.freeze({
      desktopInstaller: true,
    }),
  })
}

function options(args, allowed) {
  assert.equal(args.length % 2, 0, 'evidence options must be --name value pairs')
  const parsed = {}
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index]
    const value = args[index + 1]
    assert.ok(name?.startsWith('--') && value !== undefined, `Invalid option list near ${name ?? '<end>'}`)
    const key = name.slice(2)
    assert.equal(allowed.has(key), true, `Unknown evidence option: ${name}`)
    assert.equal(parsed[key], undefined, `Duplicate evidence option: ${name}`)
    parsed[key] = value
  }
  return parsed
}

function required(values, name) {
  assert.ok(values[name], `Missing --${name}`)
  return values[name]
}

async function writeEvidence(path, value) {
  await mkdir(dirname(path), { recursive: true })
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
    mode: 0o600,
  })
}

async function currentHead(root) {
  const { stdout } = await execFileAsync('git', ['rev-parse', 'HEAD'], { cwd: root })
  return stdout.trim()
}

async function assertCurrentHead(root, requestedHead) {
  assert.equal(await currentHead(root), requestedHead, 'evidence command is not running at the requested exact head')
}

/**
 * Executes the target, security, or aggregate evidence CLI with exact-head race checks.
 * @param {string[]} values - Subcommand and strict `--name value` arguments supplied by the caller.
 * @returns {Promise<void>} Completion after exclusively writing the requested evidence file.
 */
export async function runCli([command, ...args]) {
  const repositoryRoot = fileURLToPath(new URL('../../', import.meta.url))
  const common = new Set(['git-head', 'output'])
  let allowed
  if (command === 'target') {
    allowed = new Set([...common, 'runner', 'sidecar-target', 'expected-os', 'actual-os', 'arch', 'host-arch', 'package-boundary', 'metrics', 'fuse-inspection', 'file-grant-evidence', 'withhold-evidence', 'restart-evidence', 'integrity-evidence', 'receipts'])
  } else if (command === 'security') {
    allowed = new Set([...common, 'sbom', 'receipts'])
  } else if (command === 'aggregate') {
    allowed = new Set([...common, 'root'])
  } else {
    throw new Error(`Unknown verification evidence command: ${command ?? '<missing>'}`)
  }
  const values = options(args, allowed)
  const requestedHead = exactHead(required(values, 'git-head'))
  const output = required(values, 'output')
  await assertCurrentHead(repositoryRoot, requestedHead)

  let evidence
  if (command === 'target') {
    const metricsBytes = await readFile(required(values, 'metrics'))
    const fuseInspectionBytes = await readFile(required(values, 'fuse-inspection'))
    const fileGrantEvidenceBytes = await readFile(required(values, 'file-grant-evidence'))
    const withholdEvidenceBytes = await readFile(required(values, 'withhold-evidence'))
    const restartEvidenceBytes = await readFile(required(values, 'restart-evidence'))
    const integrityEvidenceBytes = await readFile(required(values, 'integrity-evidence'))
    evidence = createTargetEvidence({
      gitHead: requestedHead,
      runner: required(values, 'runner'),
      sidecarTarget: required(values, 'sidecar-target'),
      expectedOs: required(values, 'expected-os'),
      actualOs: required(values, 'actual-os'),
      arch: required(values, 'arch'),
      hostArch: required(values, 'host-arch'),
      packageBoundary: required(values, 'package-boundary'),
      metrics: JSON.parse(metricsBytes.toString('utf8')),
      metricsBytes,
      fuseInspection: JSON.parse(fuseInspectionBytes.toString('utf8')),
      fuseInspectionBytes,
      fileGrantMediation: JSON.parse(fileGrantEvidenceBytes.toString('utf8')),
      fileGrantEvidenceBytes,
      withholdEvidence: JSON.parse(withholdEvidenceBytes.toString('utf8')),
      withholdEvidenceBytes,
      restartEvidence: JSON.parse(restartEvidenceBytes.toString('utf8')),
      restartEvidenceBytes,
      integrityEvidence: JSON.parse(integrityEvidenceBytes.toString('utf8')),
      integrityEvidenceBytes,
      receiptRecords: await loadVerificationReceipts(required(values, 'receipts'), requestedHead),
    })
  } else if (command === 'security') {
    evidence = createSecurityEvidence({
      gitHead: requestedHead,
      sbomBytes: await readFile(required(values, 'sbom')),
      receiptRecords: await loadVerificationReceipts(required(values, 'receipts'), requestedHead),
    })
  } else {
    evidence = await aggregateEvidence(required(values, 'root'), requestedHead)
  }

  await assertCurrentHead(repositoryRoot, requestedHead)
  await writeEvidence(output, evidence)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await runCli(process.argv.slice(2))
}
