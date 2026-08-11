// Runs the exact Docker and Compose command allowlist under bounded host-only process controls.

import {
  spawn,
  type ChildProcessWithoutNullStreams,
  type SpawnOptionsWithoutStdio,
} from 'node:child_process'
import { isAbsolute } from 'node:path'
import { terminateNativeSidecarProcess } from './sidecar-process'
import type {
  HostComposePlan,
  HostContainerControlPort,
  HostContainerOperation,
  HostContainerPolicy,
  HostOwnedResource,
  HostRuntimeObservation,
} from './container-supervisor'

const INSPECTION_TIMEOUT_MS = 30_000
const LIFECYCLE_TIMEOUT_MS = 5 * 60 * 1000
const MAX_INSPECTION_OUTPUT_BYTES = 64 * 1024
const MAX_COMPOSE_INPUT_BYTES = 512 * 1024
const MAX_LIFECYCLE_OUTPUT_BYTES = 256 * 1024
const ENVIRONMENT_NAME = /^[A-Z_][A-Z0-9_]{0,63}$/
const MAX_ENVIRONMENT_ENTRIES = 16
const OBSERVATION_FIELD_SEPARATOR = '|ancestryllm-field|'

export type HostContainerProcessErrorCode =
  | 'PROCESS_REQUEST_INVALID'
  | 'PROCESS_INPUT_LIMIT'
  | 'PROCESS_OUTPUT_LIMIT'
  | 'PROCESS_TIMEOUT'
  | 'PROCESS_EXIT'
  | 'PROCESS_RESPONSE_INVALID'

const PROCESS_ERROR_MESSAGES: Readonly<Record<HostContainerProcessErrorCode, string>> = {
  PROCESS_REQUEST_INVALID: 'The bounded host process request is invalid.',
  PROCESS_INPUT_LIMIT: 'The bounded host process input exceeded its limit.',
  PROCESS_OUTPUT_LIMIT: 'The bounded host process output exceeded its limit.',
  PROCESS_TIMEOUT: 'The bounded host process exceeded its time limit.',
  PROCESS_EXIT: 'The bounded host process failed.',
  PROCESS_RESPONSE_INVALID: 'The bounded host process returned an invalid response.',
}

export class HostContainerProcessError extends Error {
  constructor(readonly code: HostContainerProcessErrorCode) {
    super(PROCESS_ERROR_MESSAGES[code])
    this.name = 'HostContainerProcessError'
  }
}

export interface HostProcessRequest {
  readonly executablePath: string
  readonly arguments: readonly string[]
  readonly workingDirectory: string
  readonly environment: NodeJS.ProcessEnv
  readonly standardInput: string | undefined
  readonly timeoutMs: number
  readonly maxInputBytes: number
  readonly maxOutputBytes: number
}

export interface HostProcessResult {
  readonly stdout: string
}

export type RunHostProcess = (request: HostProcessRequest) => Promise<HostProcessResult>

function processFail(code: HostContainerProcessErrorCode): never {
  throw new HostContainerProcessError(code)
}

function isPositiveInteger(value: number): boolean {
  return Number.isSafeInteger(value) && value > 0
}

function validEnvironment(environment: NodeJS.ProcessEnv): boolean {
  try {
    if (environment === null || typeof environment !== 'object') return false
    const prototype = Object.getPrototypeOf(environment)
    if (prototype !== Object.prototype && prototype !== null) return false
    const keys = Reflect.ownKeys(environment)
    if (keys.length > MAX_ENVIRONMENT_ENTRIES) return false
    return keys.every((key) => {
      if (typeof key !== 'string' || !ENVIRONMENT_NAME.test(key)) return false
      const descriptor = Object.getOwnPropertyDescriptor(environment, key)
      if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) return false
      const value = descriptor.value as unknown
      return value === undefined || (
        typeof value === 'string'
        && value.length <= 4096
        && !value.includes('\0')
        && !value.includes('\n')
        && !value.includes('\r')
      )
    })
  } catch {
    return false
  }
}

function validateRequest(request: HostProcessRequest): void {
  if (
    typeof request.executablePath !== 'string'
    || !isAbsolute(request.executablePath)
    || typeof request.workingDirectory !== 'string'
    || !isAbsolute(request.workingDirectory)
    || !Array.isArray(request.arguments)
    || request.arguments.length > 128
    || request.arguments.some((argument) => (
      typeof argument !== 'string'
      || argument.includes('\0')
      || argument.includes('\n')
      || argument.includes('\r')
    ))
    || (request.standardInput !== undefined && typeof request.standardInput !== 'string')
    || !validEnvironment(request.environment)
    || !isPositiveInteger(request.timeoutMs)
    || !isPositiveInteger(request.maxInputBytes)
    || !isPositiveInteger(request.maxOutputBytes)
  ) processFail('PROCESS_REQUEST_INVALID')
}

export function minimalDockerEnvironment(
  platform: NodeJS.Platform,
  source: NodeJS.ProcessEnv,
): NodeJS.ProcessEnv {
  const permitted = platform === 'win32'
    ? ['SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP']
    : ['LANG', 'LC_ALL', 'TMPDIR']
  const environment: NodeJS.ProcessEnv = {}
  for (const name of permitted) {
    const descriptor = Object.getOwnPropertyDescriptor(source, name)
    const value = descriptor && 'value' in descriptor ? descriptor.value as unknown : undefined
    if (
      descriptor?.enumerable === true
      && typeof value === 'string'
      && value.length <= 4096
      && !value.includes('\0')
      && !value.includes('\n')
      && !value.includes('\r')
    ) {
      environment[name] = value
    }
  }
  return environment
}

export function dockerProcessSpawnOptions(
  workingDirectory: string,
  environment: NodeJS.ProcessEnv,
  platform: NodeJS.Platform = process.platform,
): SpawnOptionsWithoutStdio & { stdio: ['pipe', 'pipe', 'pipe'] } {
  return {
    cwd: workingDirectory,
    env: environment,
    shell: false,
    detached: platform !== 'win32',
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'],
  }
}

export function runBoundedHostProcess(request: HostProcessRequest): Promise<HostProcessResult> {
  validateRequest(request)
  const input = request.standardInput ?? ''
  if (Buffer.byteLength(input, 'utf8') > request.maxInputBytes) {
    return Promise.reject(new HostContainerProcessError('PROCESS_INPUT_LIMIT'))
  }

  return new Promise((resolve, reject) => {
    let child: ChildProcessWithoutNullStreams
    try {
      child = spawn(
        request.executablePath,
        [...request.arguments],
        dockerProcessSpawnOptions(request.workingDirectory, request.environment),
      )
    } catch {
      reject(new HostContainerProcessError('PROCESS_EXIT'))
      return
    }

    let settled = false
    let outputBytes = 0
    const stdout: Buffer[] = []

    const timer = setTimeout(() => {
      fail('PROCESS_TIMEOUT', true)
    }, request.timeoutMs)

    const finish = (callback: () => void): void => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      callback()
    }

    const fail = (code: HostContainerProcessErrorCode, terminate = false): void => {
      finish(() => {
        if (terminate) {
          void terminateNativeSidecarProcess(child).catch(() => undefined)
        }
        reject(new HostContainerProcessError(code))
      })
    }

    const account = (chunk: Buffer, retain: boolean): void => {
      if (settled) return
      outputBytes += chunk.length
      if (outputBytes > request.maxOutputBytes) {
        fail('PROCESS_OUTPUT_LIMIT', true)
        return
      }
      if (retain) stdout.push(chunk)
    }

    child.stdout.on('data', (chunk: Buffer) => account(chunk, true))
    child.stderr.on('data', (chunk: Buffer) => account(chunk, false))
    child.once('error', () => fail('PROCESS_EXIT'))
    child.once('exit', (code, signal) => {
      if (code !== 0 || signal !== null) {
        fail('PROCESS_EXIT')
        return
      }
      finish(() => resolve({ stdout: Buffer.concat(stdout).toString('utf8') }))
    })
    child.stdin.once('error', () => fail('PROCESS_EXIT', true))
    child.stdin.end(input)
  })
}

function responseFail(): never {
  return processFail('PROCESS_RESPONSE_INVALID')
}

function exactFields(value: string, count: number): string[] {
  if (
    Buffer.byteLength(value, 'utf8') > MAX_INSPECTION_OUTPUT_BYTES
    || value.includes('\0')
    || !value.endsWith('\n')
  ) {
    return responseFail()
  }
  const record = value.slice(0, -1)
  if (record.includes('\n') || record.includes('\r')) return responseFail()
  const fields = record.split(OBSERVATION_FIELD_SEPARATOR)
  if (fields.length !== count || fields.some((field) => field.length === 0)) {
    return responseFail()
  }
  return fields
}

function parseSecurityOptions(value: string): readonly string[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    return responseFail()
  }
  if (!Array.isArray(parsed) || parsed.length === 0 || parsed.length > 32) return responseFail()
  const options = parsed.map((option) => {
    if (typeof option !== 'string') return responseFail()
    if (option === 'seccomp' || option.startsWith('name=seccomp,')) return 'seccomp'
    if (option === 'cgroupns' || option === 'name=cgroupns') return 'cgroupns'
    if (!/^[A-Za-z0-9][A-Za-z0-9._=,:/-]{0,255}$/.test(option)) return responseFail()
    return option
  })
  if (new Set(options).size !== options.length) return responseFail()
  return [...options].sort()
}

function parseDockerArchitecture(value: string): 'arm64' {
  if (value === 'arm64' || value === 'aarch64') return 'arm64'
  return responseFail()
}

function labelsFromColumns(columns: readonly string[]): Readonly<Record<string, string>> {
  if (columns.length !== 3) return responseFail()
  const [owner, profile, project] = columns
  if (!owner && !profile && !project) return {}
  if (!owner || !profile || !project) return responseFail()
  return {
    'com.ancestryllm.owner': owner,
    'com.ancestryllm.profile': profile,
    'com.ancestryllm.project': project,
  }
}

function parseInventory(
  value: string,
  kind: HostOwnedResource['kind'],
): HostOwnedResource[] {
  if (value.length === 0) return []
  if (value.includes('\0') || !value.endsWith('\n')) return responseFail()
  const rows = value.slice(0, -1).split('\n')
  if (rows.length > 256) return responseFail()
  return rows.map((row) => {
    if (row.includes('\r')) return responseFail()
    const columns = row.split('\t')
    const nameIndex = kind === 'volume' ? 0 : 1
    const labelsIndex = kind === 'volume' ? 1 : 2
    if (columns.length !== labelsIndex + 3) return responseFail()
    if (kind !== 'volume') {
      const id = columns[0] ?? ''
      if (!/^(?:[a-f0-9]{12,64}|sha256:[a-f0-9]{64})$/.test(id)) return responseFail()
    }
    const name = columns[nameIndex]
    if (!name || !/^[a-z0-9][a-z0-9.-]{0,126}$/.test(name)) return responseFail()
    return {
      kind,
      name,
      labels: labelsFromColumns(columns.slice(labelsIndex)),
    }
  })
}

function mergeInventory(
  ...groups: readonly (readonly HostOwnedResource[])[]
): readonly HostOwnedResource[] {
  const merged: HostOwnedResource[] = []
  for (const resource of groups.flat()) {
    const existing = merged.find((candidate) => (
      candidate.kind === resource.kind && candidate.name === resource.name
    ))
    if (!existing) {
      merged.push(resource)
      continue
    }
    const labelsMatch = Object.entries(existing.labels).every(([name, value]) => (
      resource.labels[name] === value
    )) && Object.keys(existing.labels).length === Object.keys(resource.labels).length
    // Preserve conflicting duplicates so the supervisor emits its stable
    // RESOURCE_CONFLICT error instead of letting this adapter hide ambiguity.
    if (!labelsMatch) merged.push(resource)
  }
  return merged
}

function sortedValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortedValue)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, child]) => [key, sortedValue(child)]),
    )
  }
  return value
}

export function serializeHostComposePlan(plan: HostComposePlan): string {
  const labels = { ...plan.labels }
  const services = Object.fromEntries(Object.entries(plan.services).map(([name, service]) => [
    name,
    {
      cap_drop: [...service.capDrop],
      container_name: service.containerName,
      cpus: service.cpus,
      image: service.image,
      init: service.init,
      labels,
      logging: {
        driver: service.logging.driver,
        options: {
          'max-file': String(service.logging.maxFiles),
          'max-size': service.logging.maxSize,
        },
      },
      mem_limit: service.memory,
      networks: [...service.networks],
      pids_limit: service.pidsLimit,
      ports: service.ports.map((port) => ({
        host_ip: port.hostIp,
        protocol: port.protocol,
        published: port.published,
        target: port.target,
      })),
      read_only: service.readOnly,
      security_opt: [...service.securityOptions],
      user: service.user,
      volumes: service.mounts.map((mount) => ({
        read_only: mount.readOnly,
        source: mount.source,
        target: mount.target,
        type: mount.kind,
      })),
    },
  ]))
  const networks = Object.fromEntries(Object.entries(plan.networks).map(([name, network]) => [
    name,
    { internal: network.internal, labels, name },
  ]))
  const volumes = Object.fromEntries(Object.keys(plan.volumes).map((name) => [
    name,
    { labels, name },
  ]))
  return `${JSON.stringify(sortedValue({ networks, services, volumes }))}\n`
}

interface DockerCliHostControlOptions {
  readonly run?: RunHostProcess
  readonly sourceEnvironment?: NodeJS.ProcessEnv
  readonly platform?: NodeJS.Platform
}

export class DockerCliHostControl implements HostContainerControlPort {
  private readonly run: RunHostProcess
  private readonly sourceEnvironment: NodeJS.ProcessEnv
  private readonly platform: NodeJS.Platform

  constructor(options: DockerCliHostControlOptions = {}) {
    this.run = options.run ?? runBoundedHostProcess
    this.sourceEnvironment = options.sourceEnvironment ?? process.env
    this.platform = options.platform ?? process.platform
  }

  async observe(policy: HostContainerPolicy): Promise<HostRuntimeObservation> {
    const context = exactFields((await this.executeDocker(policy, [
      'context', 'inspect', policy.dockerContext, '--format',
      `{{.Name}}${OBSERVATION_FIELD_SEPARATOR}{{(index .Endpoints "docker").Host}}`,
    ])).stdout, 2)
    const version = exactFields((await this.executeDocker(policy, [
      '--context', policy.dockerContext,
      'version', '--format',
      `{{.Server.Version}}${OBSERVATION_FIELD_SEPARATOR}{{.Server.APIVersion}}`,
    ])).stdout, 2)
    const info = exactFields((await this.executeDocker(policy, [
      '--context', policy.dockerContext,
      'info', '--format',
      [
        '{{.ID}}',
        '{{.OSType}}',
        '{{.Architecture}}',
        '{{json .SecurityOptions}}',
      ].join(OBSERVATION_FIELD_SEPARATOR),
    ])).stdout, 4)
    return {
      runtimeProfile: policy.runtimeProfile,
      dockerContext: context[0]!,
      endpoint: context[1]!,
      engineId: info[0]!,
      serverVersion: version[0]!,
      apiVersion: version[1]!,
      operatingSystem: info[1]!,
      architecture: parseDockerArchitecture(info[2]!),
      securityOptions: parseSecurityOptions(info[3]!),
    }
  }

  async inventory(policy: HostContainerPolicy): Promise<readonly HostOwnedResource[]> {
    const nameFilter = ['--filter', `name=${policy.compose.projectName}`]
    const labelFilters = Object.entries(policy.compose.labels).flatMap(([name, value]) => [
      '--filter', `label=${name}=${value}`,
    ])
    const labelColumns = [
      '{{.Label "com.ancestryllm.owner"}}',
      '{{.Label "com.ancestryllm.profile"}}',
      '{{.Label "com.ancestryllm.project"}}',
    ].join('\t')
    const containerFormat = `{{.ID}}\t{{.Names}}\t${labelColumns}`
    const networkFormat = `{{.ID}}\t{{.Name}}\t${labelColumns}`
    const volumeFormat = `{{.Name}}\t${labelColumns}`
    const containersByName = await this.executeDocker(policy, [
      '--context', policy.dockerContext,
      'ps', '--all', ...nameFilter, '--format', containerFormat,
    ])
    const containersByLabel = await this.executeDocker(policy, [
      '--context', policy.dockerContext,
      'ps', '--all', ...labelFilters, '--format', containerFormat,
    ])
    const networksByName = await this.executeDocker(policy, [
      '--context', policy.dockerContext,
      'network', 'ls', ...nameFilter, '--format', networkFormat,
    ])
    const networksByLabel = await this.executeDocker(policy, [
      '--context', policy.dockerContext,
      'network', 'ls', ...labelFilters, '--format', networkFormat,
    ])
    const volumesByName = await this.executeDocker(policy, [
      '--context', policy.dockerContext,
      'volume', 'ls', ...nameFilter, '--format', volumeFormat,
    ])
    const volumesByLabel = await this.executeDocker(policy, [
      '--context', policy.dockerContext,
      'volume', 'ls', ...labelFilters, '--format', volumeFormat,
    ])
    return mergeInventory(
      parseInventory(containersByName.stdout, 'container'),
      parseInventory(containersByLabel.stdout, 'container'),
      parseInventory(networksByName.stdout, 'network'),
      parseInventory(networksByLabel.stdout, 'network'),
      parseInventory(volumesByName.stdout, 'volume'),
      parseInventory(volumesByLabel.stdout, 'volume'),
    )
  }

  async apply(
    policy: HostContainerPolicy,
    plan: HostComposePlan,
    operation: HostContainerOperation,
  ): Promise<void> {
    const lifecycleArguments: Readonly<Record<HostContainerOperation, readonly string[]>> = {
      start: ['up', '--detach', '--wait', '--remove-orphans'],
      stop: ['stop', '--timeout', '20'],
      repair: ['up', '--detach', '--wait', '--remove-orphans', '--force-recreate'],
      'uninstall-preserve': ['down', '--remove-orphans', '--timeout', '20'],
      'uninstall-delete': ['down', '--remove-orphans', '--volumes', '--timeout', '20'],
    }
    const input = serializeHostComposePlan(plan)
    await this.executeCompose(policy, [
      '--ansi', 'never',
      '--project-name', policy.compose.projectName,
      '--file', '-',
      ...lifecycleArguments[operation],
    ], input, LIFECYCLE_TIMEOUT_MS, MAX_LIFECYCLE_OUTPUT_BYTES)
  }

  private executeDocker(
    policy: HostContainerPolicy,
    arguments_: readonly string[],
    standardInput?: string,
    timeoutMs = INSPECTION_TIMEOUT_MS,
    maxOutputBytes = MAX_INSPECTION_OUTPUT_BYTES,
  ): Promise<HostProcessResult> {
    return this.run({
      executablePath: policy.dockerExecutable,
      arguments: ['--config', policy.dockerConfigDirectory, ...arguments_],
      workingDirectory: policy.workingDirectory,
      environment: minimalDockerEnvironment(this.platform, this.sourceEnvironment),
      standardInput,
      timeoutMs,
      maxInputBytes: MAX_COMPOSE_INPUT_BYTES,
      maxOutputBytes,
    })
  }

  private executeCompose(
    policy: HostContainerPolicy,
    arguments_: readonly string[],
    standardInput: string,
    timeoutMs: number,
    maxOutputBytes: number,
  ): Promise<HostProcessResult> {
    return this.run({
      executablePath: policy.dockerComposeExecutable,
      arguments: [...arguments_],
      workingDirectory: policy.workingDirectory,
      environment: {
        ...minimalDockerEnvironment(this.platform, this.sourceEnvironment),
        DOCKER_CONFIG: policy.dockerConfigDirectory,
        DOCKER_CONTEXT: policy.dockerContext,
      },
      standardInput,
      timeoutMs,
      maxInputBytes: MAX_COMPOSE_INPUT_BYTES,
      maxOutputBytes,
    })
  }
}
