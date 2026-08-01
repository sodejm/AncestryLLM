import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { access, lstat, mkdir, readFile, readdir, readlink, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const SHA = /^[0-9a-f]{40}$/
const SHA256 = /^[0-9a-f]{64}$/
const ARTIFACT_NAME = /^[A-Za-z][A-Za-z0-9]*$/

export const RECEIPT_SCHEMA_VERSION = 2
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

function validateWorkspace(value) {
  assert.deepEqual(
    Object.keys(value ?? {}).sort(),
    ['after', 'algorithm', 'allowedOutputs', 'before', 'status'],
    'receipt workspace must use the exact schema',
  )
  assert.equal(value.algorithm, 'git-workspace-v1', 'unsupported receipt workspace algorithm')
  assert.equal(value.status, 'unchanged', 'verification workspace changed')
  assert.equal(Array.isArray(value.allowedOutputs), true, 'receipt workspace allowedOutputs must be an array')
  assert.equal(
    value.allowedOutputs.every((item) => typeof item === 'string' && item.length > 0),
    true,
    'receipt workspace allowedOutputs must contain non-empty paths',
  )
  assert.deepEqual(value.allowedOutputs, [...new Set(value.allowedOutputs)].sort(), 'receipt workspace allowedOutputs must be unique and sorted')
  validateDigest(value.before, 'receipt workspace before', { nonempty: true })
  validateDigest(value.after, 'receipt workspace after', { nonempty: true })
  assert.deepEqual(value.after, value.before, 'verification workspace changed')
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
      'workspace',
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
  validateWorkspace(value.workspace)
  return value
}

async function captureCommand(executable, args, repositoryRoot) {
  const chunks = []
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(executable, args, {
      cwd: repositoryRoot,
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stderr = ''
    child.stdout.on('data', (chunk) => chunks.push(chunk))
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.once('error', rejectPromise)
    child.once('close', (code, signal) => {
      if (code === 0 && signal === null) resolvePromise(Buffer.concat(chunks))
      else rejectPromise(new Error(`${executable} ${args.join(' ')} failed (${signal ?? code}): ${stderr.trim()}`))
    })
  })
}

async function gitOutput(repositoryRoot, args) {
  return captureCommand('git', args, repositoryRoot)
}

async function gitHead(repositoryRoot) {
  return (await gitOutput(repositoryRoot, ['rev-parse', 'HEAD'])).toString('utf8').trim()
}

function nullSeparatedPaths(bytes) {
  const value = bytes.toString('utf8')
  if (value.length === 0) return []
  assert.equal(value.endsWith('\0'), true, 'git path output was not NUL terminated')
  return value.slice(0, -1).split('\0')
}

function repositoryPath(repositoryRoot, path, label, { outside = 'reject' } = {}) {
  const absolutePath = isAbsolute(path) ? resolve(path) : resolve(repositoryRoot, path)
  const relativePath = relative(repositoryRoot, absolutePath)
  const isOutside = relativePath === '..' || relativePath.startsWith(`..${sep}`) || isAbsolute(relativePath)
  if (isOutside && outside === 'ignore') return undefined
  assert.equal(isOutside, false, `${label} must stay inside the repository`)
  assert.notEqual(relativePath, '', `${label} must not allow the repository root`)
  return relativePath.split(sep).join('/')
}

function pathIsAllowed(path, allowedOutputs) {
  return allowedOutputs.some((allowed) => path === allowed || path.startsWith(`${allowed}/`))
}

function fileKind(stat) {
  if (stat.isFile()) return 'file'
  if (stat.isSymbolicLink()) return 'symlink'
  if (stat.isDirectory()) return 'directory'
  return 'other'
}

async function untrackedEntry(repositoryRoot, path) {
  const absolutePath = resolve(repositoryRoot, ...path.split('/'))
  const stat = await lstat(absolutePath)
  const kind = fileKind(stat)
  let bytes
  if (kind === 'file') bytes = await readFile(absolutePath)
  else if (kind === 'symlink') bytes = Buffer.from(await readlink(absolutePath), 'utf8')
  else bytes = Buffer.from(`${kind}:${stat.size}`, 'utf8')
  return Object.freeze({ path, kind, mode: stat.mode & 0o777, digest: digest(bytes) })
}

async function workspaceSnapshot(repositoryRoot, expectedHead, allowedOutputs) {
  const [head, indexDiff, worktreeDiff, untrackedBytes] = await Promise.all([
    gitHead(repositoryRoot),
    gitOutput(repositoryRoot, ['diff', '--cached', '--no-ext-diff', '--binary', '--full-index', '--no-renames', 'HEAD', '--']),
    gitOutput(repositoryRoot, ['diff', '--no-ext-diff', '--binary', '--full-index', '--no-renames', '--']),
    gitOutput(repositoryRoot, ['ls-files', '--others', '--exclude-standard', '-z']),
  ])
  assert.equal(exactHead(head, 'workspace gitHead'), expectedHead, 'verification workspace left the requested exact head')
  const untrackedPaths = nullSeparatedPaths(untrackedBytes)
    .filter((path) => !pathIsAllowed(path, allowedOutputs))
    .sort()
  const untracked = []
  for (const path of untrackedPaths) untracked.push(await untrackedEntry(repositoryRoot, path))
  const manifest = Object.freeze({
    gitHead: head,
    indexDiff: digest(indexDiff),
    worktreeDiff: digest(worktreeDiff),
    untracked: Object.freeze(untracked),
  })
  const bytes = Buffer.from(JSON.stringify(manifest), 'utf8')
  return Object.freeze({
    digest: digest(bytes),
    dirty: indexDiff.byteLength > 0 || worktreeDiff.byteLength > 0 || untracked.length > 0,
  })
}

async function normalizedAllowedOutputs(repositoryRoot, allowedOutputs, artifacts, outputPath) {
  assert.equal(Array.isArray(allowedOutputs), true, 'allowedOutputs must be an array')
  assert.equal(allowedOutputs.every((path) => typeof path === 'string' && path.length > 0), true, 'allowedOutputs must contain non-empty paths')
  const normalized = allowedOutputs.map((path) => repositoryPath(repositoryRoot, path, 'allowed output'))
  for (const [name, path] of Object.entries(artifacts)) {
    const artifact = repositoryPath(repositoryRoot, path, `receipt artifact ${name}`, { outside: 'ignore' })
    if (artifact !== undefined) normalized.push(artifact)
  }
  const receipt = repositoryPath(repositoryRoot, outputPath, 'receipt output', { outside: 'ignore' })
  if (receipt !== undefined) normalized.push(receipt)
  const unique = [...new Set(normalized)].sort()
  assert.equal(unique.length, normalized.length, 'duplicate allowed output path')

  const trackedPaths = nullSeparatedPaths(await gitOutput(repositoryRoot, ['ls-files', '-z']))
  for (const output of unique) {
    assert.equal(
      trackedPaths.some((path) => path === output || path.startsWith(`${output}/`)),
      false,
      `allowed output overlaps tracked repository content: ${output}`,
    )
  }
  return Object.freeze(unique)
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
  allowedOutputs = [],
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
  const normalizedOutputs = await normalizedAllowedOutputs(repositoryRoot, allowedOutputs, artifacts, outputPath)
  const workspaceBefore = await workspaceSnapshot(repositoryRoot, expectedHead, normalizedOutputs)
  assert.equal(workspaceBefore.dirty, false, 'verification workspace must be clean before the command')
  const { command: executedCommand, result } = await executeCommand(command[0], command.slice(1), repositoryRoot, { forwardOutput })
  assert.equal(result.exitCode, 0, `verification command exited with code ${result.exitCode}`)
  assert.equal(result.signal, null, `verification command terminated by signal ${result.signal}`)
  const headAfter = exactHead(await gitHead(repositoryRoot), 'headAfter')
  assert.equal(headAfter, expectedHead, 'verification command changed or left the requested exact head')
  const workspaceAfter = await workspaceSnapshot(repositoryRoot, expectedHead, normalizedOutputs)
  assert.equal(workspaceAfter.dirty, false, 'verification workspace changed during the command')
  assert.deepEqual(workspaceAfter.digest, workspaceBefore.digest, 'verification workspace changed during the command')

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
    workspace: Object.freeze({
      algorithm: 'git-workspace-v1',
      allowedOutputs: normalizedOutputs,
      before: workspaceBefore.digest,
      after: workspaceAfter.digest,
      status: 'unchanged',
    }),
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

  const parsed = { gates: [], artifacts: {}, allowedOutputs: [], command }
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
    } else if (name === '--allow-output') {
      parsed.allowedOutputs.push(value)
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
