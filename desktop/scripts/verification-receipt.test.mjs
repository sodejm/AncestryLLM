import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { access, mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { promisify } from 'node:util'
import {
  TARGET_RECEIPT_GATES,
  parseReceiptArguments,
  runVerificationCommand,
  validateVerificationReceipt,
} from './verification-receipt.mjs'

const execFileAsync = promisify(execFile)

async function cleanRepositoryFixture(prefix = 'ancestryllm-receipt-repository-') {
  const root = await mkdtemp(join(tmpdir(), prefix))
  const repositoryRoot = join(root, 'repository')
  await mkdir(repositoryRoot)
  await execFileAsync('git', ['init', '--quiet'], { cwd: repositoryRoot })
  await execFileAsync('git', ['config', 'user.email', 'security-tests@example.invalid'], { cwd: repositoryRoot })
  await execFileAsync('git', ['config', 'user.name', 'Security Tests'], { cwd: repositoryRoot })
  await writeFile(join(repositoryRoot, 'tracked.txt'), 'original\n')
  await execFileAsync('git', ['add', 'tracked.txt'], { cwd: repositoryRoot })
  await execFileAsync('git', ['commit', '--quiet', '-m', 'fixture'], { cwd: repositoryRoot })
  const { stdout } = await execFileAsync('git', ['rev-parse', 'HEAD'], { cwd: repositoryRoot })
  return { root, repositoryRoot, gitHead: stdout.trim() }
}

test('target receipts require each packaged sidecar fault scenario explicitly', () => {
  assert.deepEqual(TARGET_RECEIPT_GATES, [
    'packageRuntimePassed',
    'sidecarSmokePassed',
    'fusesInspectedPassed',
    'rendererZeroEgressCanaryPassed',
    'normalLaunchDebugSurfaceAbsentPassed',
    'packagedSidecarWithholdRetryPassed',
    'packagedSidecarRestartExhaustionQuitPassed',
    'packagedSidecarVersionMismatchPassed',
  ])
})

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

test('receipt wrapper executes a command and writes exact-head output and artifact digests once', async () => {
  const { root, repositoryRoot, gitHead } = await cleanRepositoryFixture()
  const outputPath = join(root, 'audit.json')
  const artifactPath = join(root, 'sbom.json')
  const artifact = Buffer.from('{"bomFormat":"CycloneDX"}\n')
  await writeFile(artifactPath, artifact)

  const receipt = await runVerificationCommand({
    gitHead,
    outputPath,
    gates: ['auditPassed', 'sbomGeneratedPassed'],
    artifacts: { sbom: artifactPath },
    command: [process.execPath, '-e', "process.stdout.write('verified\\n'); process.stderr.write('warning\\n')"],
    repositoryRoot,
    forwardOutput: false,
  })

  assert.equal(receipt.result.exitCode, 0)
  assert.equal(receipt.result.signal, null)
  assert.deepEqual(receipt.result.stdout, { sha256: sha256('verified\n'), bytes: 9 })
  assert.deepEqual(receipt.result.stderr, { sha256: sha256('warning\n'), bytes: 8 })
  assert.deepEqual(receipt.artifacts.sbom, { sha256: sha256(artifact), bytes: artifact.byteLength })
  assert.equal(receipt.headBefore, gitHead)
  assert.equal(receipt.headAfter, gitHead)
  assert.deepEqual(receipt.workspace.allowedOutputs, [])
  assert.equal(receipt.workspace.algorithm, 'git-workspace-v1')
  assert.equal(receipt.workspace.status, 'unchanged')
  assert.deepEqual(receipt.workspace.before, receipt.workspace.after)
  validateVerificationReceipt(JSON.parse(await (await import('node:fs/promises')).readFile(outputPath, 'utf8')), gitHead)

  await assert.rejects(runVerificationCommand({
    gitHead,
    outputPath,
    gates: ['auditPassed'],
    command: [process.execPath, '-e', 'process.exit(0)'],
    repositoryRoot,
    forwardOutput: false,
  }), /already exists/)
})

test('receipt wrapper leaves no passing receipt for a failed command', async () => {
  const { root, repositoryRoot, gitHead } = await cleanRepositoryFixture('ancestryllm-failed-receipt-')
  const outputPath = join(root, 'failed.json')

  await assert.rejects(runVerificationCommand({
    gitHead,
    outputPath,
    gates: ['secretsPassed'],
    command: [process.execPath, '-e', 'process.exit(7)'],
    repositoryRoot,
    forwardOutput: false,
  }), /exited with code 7/)
  await assert.rejects(access(outputPath), (error) => error.code === 'ENOENT')
})

test('receipt wrapper rejects tracked and staged mutations without writing a receipt', async () => {
  for (const staged of [false, true]) {
    const { root, repositoryRoot, gitHead } = await cleanRepositoryFixture(`ancestryllm-${staged ? 'staged' : 'tracked'}-mutation-`)
    const outputPath = join(root, 'mutation.json')
    const stage = staged
      ? "require('node:child_process').execFileSync('git', ['add', 'tracked.txt'])"
      : ''
    await assert.rejects(runVerificationCommand({
      gitHead,
      outputPath,
      gates: ['secretsPassed'],
      command: [process.execPath, '-e', `require('node:fs').writeFileSync('tracked.txt', 'mutated\\n'); ${stage}`],
      repositoryRoot,
      forwardOutput: false,
    }), /verification workspace changed/)
    await assert.rejects(access(outputPath), (error) => error.code === 'ENOENT')
  }
})

test('receipt wrapper rejects undeclared untracked output and accepts only an explicit output path', async () => {
  const rejected = await cleanRepositoryFixture('ancestryllm-undeclared-output-')
  const rejectedReceipt = join(rejected.root, 'rejected.json')
  await assert.rejects(runVerificationCommand({
    gitHead: rejected.gitHead,
    outputPath: rejectedReceipt,
    gates: ['buildInspectionPassed'],
    command: [process.execPath, '-e', "require('node:fs').writeFileSync('unexpected.txt', 'unexpected\\n')"],
    repositoryRoot: rejected.repositoryRoot,
    forwardOutput: false,
  }), /verification workspace changed/)
  await assert.rejects(access(rejectedReceipt), (error) => error.code === 'ENOENT')

  const accepted = await cleanRepositoryFixture('ancestryllm-declared-output-')
  const acceptedReceipt = join(accepted.root, 'accepted.json')
  const receipt = await runVerificationCommand({
    gitHead: accepted.gitHead,
    outputPath: acceptedReceipt,
    gates: ['buildInspectionPassed'],
    allowedOutputs: ['declared/output.txt'],
    command: [process.execPath, '-e', "require('node:fs').mkdirSync('declared', { recursive: true }); require('node:fs').writeFileSync('declared/output.txt', 'declared\\n')"],
    repositoryRoot: accepted.repositoryRoot,
    forwardOutput: false,
  })
  assert.deepEqual(receipt.workspace.allowedOutputs, ['declared/output.txt'])
  assert.equal(await readFile(join(accepted.repositoryRoot, 'declared/output.txt'), 'utf8'), 'declared\n')
})

test('receipt validation rejects claimed success, head drift, and malformed output digests', () => {
  const gitHead = '0123456789abcdef0123456789abcdef01234567'
  const base = {
    schemaVersion: 2,
    kind: 'verification-receipt',
    status: 'passed',
    gitHead,
    headBefore: gitHead,
    headAfter: gitHead,
    gates: ['auditPassed'],
    command: { executable: 'pnpm', args: ['audit'], shell: false },
    result: {
      exitCode: 0,
      signal: null,
      stdout: { sha256: 'a'.repeat(64), bytes: 1 },
      stderr: { sha256: 'b'.repeat(64), bytes: 0 },
    },
    artifacts: {},
    workspace: {
      algorithm: 'git-workspace-v1',
      allowedOutputs: [],
      before: { sha256: 'c'.repeat(64), bytes: 128 },
      after: { sha256: 'c'.repeat(64), bytes: 128 },
      status: 'unchanged',
    },
  }
  assert.equal(validateVerificationReceipt(base, gitHead), base)
  assert.throws(() => validateVerificationReceipt({ ...base, result: { ...base.result, exitCode: 1 } }, gitHead), /did not exit successfully/)
  assert.throws(() => validateVerificationReceipt({ ...base, headAfter: 'f'.repeat(40) }, gitHead), /headAfter differs/)
  assert.throws(() => validateVerificationReceipt({
    ...base,
    result: { ...base.result, stdout: { sha256: 'not-a-digest', bytes: 1 } },
  }, gitHead), /stdout.sha256/)
  const legacy = { ...base }
  delete legacy.workspace
  assert.throws(() => validateVerificationReceipt({ ...legacy, schemaVersion: 1 }, gitHead), /exact schema|unsupported verification receipt schema/)
  assert.throws(() => validateVerificationReceipt({
    ...base,
    workspace: { ...base.workspace, after: { sha256: 'd'.repeat(64), bytes: 128 } },
  }, gitHead), /workspace changed/)
})

test('receipt CLI requires named gates and name=path artifacts before the executed command', () => {
  assert.deepEqual(parseReceiptArguments([
    '--git-head', 'a'.repeat(40),
    '--output', 'receipt.json',
    '--gate', 'auditPassed',
    '--artifact', 'sbom=sbom.json',
    '--allow-output', 'desktop/verification',
    '--', 'pnpm', 'audit',
  ]), {
    gitHead: 'a'.repeat(40),
    outputPath: 'receipt.json',
    gates: ['auditPassed'],
    artifacts: { sbom: 'sbom.json' },
    allowedOutputs: ['desktop/verification'],
    command: ['pnpm', 'audit'],
  })
  assert.throws(() => parseReceiptArguments(['--git-head', 'a'.repeat(40), 'pnpm', 'audit']), /separated/)
})
