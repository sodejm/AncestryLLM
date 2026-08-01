import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { access, mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const SHA = /^[0-9a-f]{40}$/
const SHA256 = /^[0-9a-f]{64}$/
const ARTIFACT_NAME = /^[A-Za-z][A-Za-z0-9]*$/

export const RECEIPT_SCHEMA_VERSION = 1
export const TARGET_RECEIPT_GATES = Object.freeze([
  'packageRuntimePassed',
  'sidecarSmokePassed',
  'fusesInspectedPassed',
  'rendererZeroEgressCanaryPassed',
  'normalLaunchDebugSurfaceAbsentPassed',
  'packagedSidecarWithholdRetryPassed',
  'packagedSidecarRestartExhaustionQuitPassed',
  'packagedSidecarVersionMismatchPassed',
])
export const SECURITY_RECEIPT_GATES = Object.freeze([
  'auditPassed',
  'secretsPassed',
  'buildInspectionPassed',
  'apiContractPassed',
  'authBeforeParsingPassed',
  'domainRoutesAbsentPassed',
  'ipcSenderValidationPassed',
  'providerNoneNetworkFreePassed',
  'redactionPassed',
  'sbomGeneratedPassed',
])

const allowedGates = new Set([...TARGET_RECEIPT_GATES, ...SECURITY_RECEIPT_GATES])

function exactHead(value, label = 'gitHead') {
  assert.match(value, SHA, `${label} must be a lowercase full Git commit SHA`)
  return value
}

function digest(buffer) {
  return Object.freeze({
    sha256: createHash('sha256').update(buffer).digest('hex'),
    bytes: buffer.byteLength,
  })
}

function validateDigest(value, label, { nonempty = false } = {}) {
  assert.deepEqual(
    Object.keys(value ?? {}).sort(),
    ['bytes', 'sha256'],
    `${label} must use the exact digest schema`,
  )
  assert.match(value.sha256, SHA256, `${label}.sha256 must be a lowercase SHA-256 digest`)
  assert.equal(
    Number.isSafeInteger(value.bytes) && value.bytes >= (nonempty ? 1 : 0),
    true,
    `${label}.bytes must be a ${nonempty ? 'positive' : 'non-negative'} safe integer`,
  )
  return value
}

function validateCommand(value) {
  assert.deepEqual(
    Object.keys(value ?? {}).sort(),
    ['args', 'executable', 'shell'],
    'receipt command must use the exact schema',
  )
  assert.equal(typeof value.executable === 'string' && value.executable.length > 0, true, 'receipt command executable is missing')
  assert.equal(Array.isArray(value.args), true, 'receipt command args must be an array')
  assert.equal(value.args.every((item) => typeof item === 'string'), true, 'receipt command args must be strings')
  assert.equal(typeof value.shell, 'boolean', 'receipt command shell must be boolean')
  return value
}

export function validateVerificationReceipt(value, requestedHead) {
  assert.deepEqual(
    Object.keys(value ?? {}).sort(),
    [
      'artifacts',
      'command',
      'gates',
      'gitHead',
      'headAfter',
      'headBefore',
      'kind',
      'result',
      'schemaVersion',
      'status',
    ].sort(),
    'verification receipt must use the exact schema',
  )
  assert.equal(value.schemaVersion, RECEIPT_SCHEMA_VERSION, 'unsupported verification receipt schema')
  assert.equal(value.kind, 'verification-receipt', 'unexpected receipt kind')
  assert.equal(value.status, 'passed', 'verification receipt did not pass')

  const gitHead = exactHead(value.gitHead)
  if (requestedHead !== undefined) assert.equal(gitHead, exactHead(requestedHead, 'requestedHead'), 'receipt is not from the requested exact head')
  assert.equal(exactHead(value.headBefore, 'headBefore'), gitHead, 'receipt headBefore differs from gitHead')
  assert.equal(exactHead(value.headAfter, 'headAfter'), gitHead, 'receipt headAfter differs from gitHead')

  assert.equal(Array.isArray(value.gates) && value.gates.length > 0, true, 'receipt must claim at least one gate')
  assert.equal(value.gates.every((gate) => typeof gate === 'string' && allowedGates.has(gate)), true, 'receipt contains an unsupported gate')
  assert.equal(new Set(value.gates).size, value.gates.length, 'receipt contains duplicate gates')
  assert.deepEqual(value.gates, [...value.gates].sort(), 'receipt gates must be sorted')

  validateCommand(value.command)
  assert.deepEqual(
    Object.keys(value.result ?? {}).sort(),
    ['exitCode', 'signal', 'stderr', 'stdout'],
    'receipt result must use the exact schema',
  )
  assert.equal(value.result.exitCode, 0, 'receipt command did not exit successfully')
  assert.equal(value.result.signal, null, 'receipt command terminated by signal')
  validateDigest(value.result.stdout, 'receipt result stdout')
  validateDigest(value.result.stderr, 'receipt result stderr')

  assert.equal(value.artifacts !== null && typeof value.artifacts === 'object' && !Array.isArray(value.artifacts), true, 'receipt artifacts must be an object')
  for (const [name, artifact] of Object.entries(value.artifacts)) {
    assert.match(name, ARTIFACT_NAME, 'receipt artifact has an invalid name')
    validateDigest(artifact, `receipt artifact ${name}`)
  }
  return value
}

async function gitHead(repositoryRoot) {
  const chunks = []
  await new Promise((resolvePromise, rejectPromise) => {
    const child = spawn('git', ['rev-parse', 'HEAD'], {
      cwd: repositoryRoot,
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stderr = ''
    child.stdout.on('data', (chunk) => chunks.push(chunk))
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.once('error', rejectPromise)
    child.once('close', (code, signal) => {
      if (code === 0 && signal === null) resolvePromise()
      else rejectPromise(new Error(`git rev-parse HEAD failed (${signal ?? code}): ${stderr.trim()}`))
    })
  })
  return Buffer.concat(chunks).toString('utf8').trim()
}

async function ensureOutputAbsent(outputPath) {
  try {
    await access(outputPath)
  } catch (error) {
    if (error?.code === 'ENOENT') return
    throw error
  }
  throw new Error(`Verification receipt already exists: ${outputPath}`)
}

function executeCommand(executable, args, repositoryRoot, { forwardOutput }) {
  return new Promise((resolvePromise, rejectPromise) => {
    const stdoutHash = createHash('sha256')
    const stderrHash = createHash('sha256')
    let stdoutBytes = 0
    let stderrBytes = 0
    const useShell = process.platform === 'win32'
    const child = spawn(executable, args, {
      cwd: repositoryRoot,
      env: process.env,
      shell: useShell,
      stdio: ['inherit', 'pipe', 'pipe'],
    })
    child.stdout.on('data', (chunk) => {
      stdoutHash.update(chunk)
      stdoutBytes += chunk.byteLength
      if (forwardOutput) process.stdout.write(chunk)
    })
    child.stderr.on('data', (chunk) => {
      stderrHash.update(chunk)
      stderrBytes += chunk.byteLength
      if (forwardOutput) process.stderr.write(chunk)
    })
    child.once('error', rejectPromise)
    child.once('close', (exitCode, signal) => resolvePromise({
      command: Object.freeze({ executable, args: Object.freeze([...args]), shell: useShell }),
      result: Object.freeze({
        exitCode,
        signal,
        stdout: Object.freeze({ sha256: stdoutHash.digest('hex'), bytes: stdoutBytes }),
        stderr: Object.freeze({ sha256: stderrHash.digest('hex'), bytes: stderrBytes }),
      }),
    }))
  })
}

export async function runVerificationCommand({
  gitHead: requestedHead,
  outputPath,
  gates,
  artifacts = {},
  command,
  repositoryRoot = fileURLToPath(new URL('../../', import.meta.url)),
  forwardOutput = true,
}) {
  const expectedHead = exactHead(requestedHead, 'requestedHead')
  assert.equal(typeof outputPath === 'string' && outputPath.length > 0, true, 'outputPath is required')
  assert.equal(Array.isArray(gates) && gates.length > 0, true, 'at least one receipt gate is required')
  const sortedGates = [...gates].sort()
  assert.equal(sortedGates.every((gate) => allowedGates.has(gate)), true, 'unsupported receipt gate')
  assert.equal(new Set(sortedGates).size, sortedGates.length, 'duplicate receipt gate')
  assert.equal(Array.isArray(command) && command.length > 0, true, 'a command is required after --')
  assert.equal(command.every((item) => typeof item === 'string'), true, 'command arguments must be strings')
  for (const [name, artifactPath] of Object.entries(artifacts)) {
    assert.match(name, ARTIFACT_NAME, 'receipt artifact has an invalid name')
    assert.equal(typeof artifactPath === 'string' && artifactPath.length > 0, true, `receipt artifact ${name} has no path`)
  }

  await ensureOutputAbsent(outputPath)
  const headBefore = exactHead(await gitHead(repositoryRoot), 'headBefore')
  assert.equal(headBefore, expectedHead, 'verification command is not starting at the requested exact head')
  const { command: executedCommand, result } = await executeCommand(command[0], command.slice(1), repositoryRoot, { forwardOutput })
  assert.equal(result.exitCode, 0, `verification command exited with code ${result.exitCode}`)
  assert.equal(result.signal, null, `verification command terminated by signal ${result.signal}`)
  const headAfter = exactHead(await gitHead(repositoryRoot), 'headAfter')
  assert.equal(headAfter, expectedHead, 'verification command changed or left the requested exact head')

  const artifactDigests = {}
  for (const name of Object.keys(artifacts).sort()) {
    const artifactPath = isAbsolute(artifacts[name]) ? artifacts[name] : resolve(repositoryRoot, artifacts[name])
    artifactDigests[name] = digest(await readFile(artifactPath))
  }

  const receipt = Object.freeze({
    schemaVersion: RECEIPT_SCHEMA_VERSION,
    kind: 'verification-receipt',
    status: 'passed',
    gitHead: expectedHead,
    headBefore,
    headAfter,
    gates: Object.freeze(sortedGates),
    command: executedCommand,
    result,
    artifacts: Object.freeze(artifactDigests),
  })
  validateVerificationReceipt(receipt, expectedHead)
  await mkdir(dirname(outputPath), { recursive: true })
  await writeFile(outputPath, `${JSON.stringify(receipt, null, 2)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
    mode: 0o600,
  })
  return receipt
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

export async function loadVerificationReceipts(root, requestedHead) {
  const records = []
  for (const path of await jsonFiles(root)) {
    let bytes
    let value
    try {
      bytes = await readFile(path)
      value = JSON.parse(bytes.toString('utf8'))
    } catch {
      continue
    }
    if (value?.kind !== 'verification-receipt') continue
    records.push(Object.freeze({
      path,
      receipt: validateVerificationReceipt(value, requestedHead),
      file: digest(bytes),
    }))
  }
  assert.equal(records.length > 0, true, `No verification receipts found under ${root}`)
  return Object.freeze(records)
}

export function parseReceiptArguments(argv) {
  const separator = argv.indexOf('--')
  assert.notEqual(separator, -1, 'Receipt command must be separated from options by --')
  const optionArgs = argv.slice(0, separator)
  const command = argv.slice(separator + 1)
  assert.equal(command.length > 0, true, 'Missing command after --')
  assert.equal(optionArgs.length % 2, 0, 'Receipt options must be --name value pairs')

  const parsed = { gates: [], artifacts: {}, command }
  for (let index = 0; index < optionArgs.length; index += 2) {
    const name = optionArgs[index]
    const value = optionArgs[index + 1]
    assert.ok(name?.startsWith('--') && value !== undefined, `Invalid receipt option near ${name ?? '<end>'}`)
    if (name === '--gate') {
      parsed.gates.push(value)
    } else if (name === '--artifact') {
      const equals = value.indexOf('=')
      assert.ok(equals > 0 && equals < value.length - 1, '--artifact must use name=path')
      const artifactName = value.slice(0, equals)
      assert.match(artifactName, ARTIFACT_NAME, 'receipt artifact has an invalid name')
      assert.equal(parsed.artifacts[artifactName], undefined, `Duplicate receipt artifact: ${artifactName}`)
      parsed.artifacts[artifactName] = value.slice(equals + 1)
    } else if (name === '--git-head') {
      assert.equal(parsed.gitHead, undefined, 'Duplicate --git-head')
      parsed.gitHead = value
    } else if (name === '--output') {
      assert.equal(parsed.outputPath, undefined, 'Duplicate --output')
      parsed.outputPath = value
    } else {
      throw new Error(`Unknown receipt option: ${name}`)
    }
  }
  assert.ok(parsed.gitHead, 'Missing --git-head')
  assert.ok(parsed.outputPath, 'Missing --output')
  assert.equal(parsed.gates.length > 0, true, 'Missing --gate')
  return parsed
}

async function main(argv) {
  await runVerificationCommand(parseReceiptArguments(argv))
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main(process.argv.slice(2))
}
