import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { access, mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { promisify } from 'node:util'
import {
  parseReceiptArguments,
  runVerificationCommand,
  validateVerificationReceipt,
} from './verification-receipt.mjs'

const execFileAsync = promisify(execFile)
const repositoryRoot = new URL('../../', import.meta.url).pathname

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

test('receipt wrapper executes a command and writes exact-head output and artifact digests once', async () => {
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-receipt-'))
  const outputPath = join(root, 'audit.json')
  const artifactPath = join(root, 'sbom.json')
  const artifact = Buffer.from('{"bomFormat":"CycloneDX"}\n')
  await writeFile(artifactPath, artifact)
  const { stdout } = await execFileAsync('git', ['rev-parse', 'HEAD'], { cwd: repositoryRoot })
  const gitHead = stdout.trim()

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
  const root = await mkdtemp(join(tmpdir(), 'ancestryllm-failed-receipt-'))
  const outputPath = join(root, 'failed.json')
  const { stdout } = await execFileAsync('git', ['rev-parse', 'HEAD'], { cwd: repositoryRoot })

  await assert.rejects(runVerificationCommand({
    gitHead: stdout.trim(),
    outputPath,
    gates: ['secretsPassed'],
    command: [process.execPath, '-e', 'process.exit(7)'],
    repositoryRoot,
    forwardOutput: false,
  }), /exited with code 7/)
  await assert.rejects(access(outputPath), (error) => error.code === 'ENOENT')
})

test('receipt validation rejects claimed success, head drift, and malformed output digests', () => {
  const gitHead = '0123456789abcdef0123456789abcdef01234567'
  const base = {
    schemaVersion: 1,
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
  }
  assert.equal(validateVerificationReceipt(base, gitHead), base)
  assert.throws(() => validateVerificationReceipt({ ...base, result: { ...base.result, exitCode: 1 } }, gitHead), /did not exit successfully/)
  assert.throws(() => validateVerificationReceipt({ ...base, headAfter: 'f'.repeat(40) }, gitHead), /headAfter differs/)
  assert.throws(() => validateVerificationReceipt({
    ...base,
    result: { ...base.result, stdout: { sha256: 'not-a-digest', bytes: 1 } },
  }, gitHead), /stdout.sha256/)
})

test('receipt CLI requires named gates and name=path artifacts before the executed command', () => {
  assert.deepEqual(parseReceiptArguments([
    '--git-head', 'a'.repeat(40),
    '--output', 'receipt.json',
    '--gate', 'auditPassed',
    '--artifact', 'sbom=sbom.json',
    '--', 'pnpm', 'audit',
  ]), {
    gitHead: 'a'.repeat(40),
    outputPath: 'receipt.json',
    gates: ['auditPassed'],
    artifacts: { sbom: 'sbom.json' },
    command: ['pnpm', 'audit'],
  })
  assert.throws(() => parseReceiptArguments(['--git-head', 'a'.repeat(40), 'pnpm', 'audit']), /separated/)
})
