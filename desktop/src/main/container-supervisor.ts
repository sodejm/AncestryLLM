/** Owns the host-only Docker control-plane policy, validation, and lifecycle reconciliation. */

import { randomBytes } from 'node:crypto'
import { lstat, realpath } from 'node:fs/promises'
import { isAbsolute, normalize, relative } from 'node:path'
import { validateStructuredClone } from './structured-clone-policy'

const POLICY_LIMITS = Object.freeze({
  maxBytes: 256 * 1024,
  maxDepth: 12,
  maxItems: 2048,
  maxStringCharacters: 4096,
})
const AUTHORIZATION_TTL_MS = 5 * 60 * 1000
const IDENTIFIER = /^[a-z0-9][a-z0-9.-]{0,126}$/
const ENGINE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const VERSION = /^[0-9]+(?:\.[0-9]+){1,2}$/
const USER = /^[1-9][0-9]*:[1-9][0-9]*$/
const IMAGE = /^[a-z0-9][a-z0-9._/-]*(?::[a-z0-9._-]+)?@sha256:[a-f0-9]{64}$/
const SAFE_OPTION = /^[A-Za-z0-9][A-Za-z0-9._=,:/-]{0,255}$/
const CPU_LIMIT = /^(?:0\.[1-9]|[1-7](?:\.[0-9])?|8(?:\.0)?)$/

/**
 * Enumerates fail-closed policy, endpoint, engine, resource, authorization, and control failures.
 */
export type HostContainerControlErrorCode =
  | 'INVALID_POLICY'
  | 'INVALID_PLAN'
  | 'ENDPOINT_UNTRUSTED'
  | 'ENDPOINT_CHANGED'
  | 'ENGINE_UNTRUSTED'
  | 'RESOURCE_CONFLICT'
  | 'AUTHORIZATION_REQUIRED'
  | 'CONTROL_FAILED'

const CONTROL_ERROR_MESSAGES: Readonly<Record<HostContainerControlErrorCode, string>> = {
  INVALID_POLICY: 'The host container policy is invalid.',
  INVALID_PLAN: 'The generated container plan is invalid.',
  ENDPOINT_UNTRUSTED: 'The selected container endpoint is not trusted.',
  ENDPOINT_CHANGED: 'The selected container endpoint changed during verification.',
  ENGINE_UNTRUSTED: 'The selected container engine identity is not trusted.',
  RESOURCE_CONFLICT: 'Container resources conflict with the app-owned namespace.',
  AUTHORIZATION_REQUIRED: 'Exact authorization is required for this host operation.',
  CONTROL_FAILED: 'The bounded host container operation failed.',
}

/**
 * Reports a stable coded failure from bounded Docker Compose process execution and immutable family-tree mounts without leaking sensitive host details.
 */
export class HostContainerControlError extends Error {
  constructor(readonly code: HostContainerControlErrorCode) {
    super(CONTROL_ERROR_MESSAGES[code])
    this.name = 'HostContainerControlError'
  }
}

/**
 * Carries the ownership labels required on every app-managed Docker resource.
 */
export interface HostContainerLabels {
  readonly 'com.ancestryllm.owner': string
  readonly 'com.ancestryllm.profile': string
  readonly 'com.ancestryllm.project': string
}

/**
 * Describes a named-volume mount; family-tree bind mounts are intentionally unrepresentable.
 */
export interface HostContainerMount {
  readonly kind: 'volume'
  readonly source: string
  readonly target: string
  readonly readOnly: boolean
}

/**
 * Restricts published service ports to loopback TCP.
 */
export interface HostContainerPort {
  readonly hostIp: '127.0.0.1'
  readonly published: number
  readonly target: number
  readonly protocol: 'tcp'
}

/**
 * Caps Docker's local log storage for an app-managed service.
 */
export interface HostContainerLogging {
  readonly driver: 'local'
  readonly maxSize: string
  readonly maxFiles: number
}

/**
 * Describes a hardened service with a read-only root, no capabilities, and bounded resources.
 */
export interface HostContainerService {
  readonly containerName: string
  readonly image: string
  readonly user: string
  readonly readOnly: true
  readonly capDrop: readonly ['ALL']
  readonly securityOptions: readonly ['no-new-privileges:true']
  readonly init: true
  readonly cpus: string
  readonly memory: string
  readonly pidsLimit: number
  readonly logging: HostContainerLogging
  readonly mounts: readonly HostContainerMount[]
  readonly networks: readonly string[]
  readonly ports: readonly HostContainerPort[]
}

/**
 * Requires the app-owned Compose network to remain internal.
 */
export interface HostContainerNetwork {
  readonly internal: true
}

/**
 * Marks app-owned data volumes for preservation unless deletion is explicitly authorized.
 */
export interface HostContainerVolume {
  readonly preserveOnUninstall: true
}

/**
 * Captures the exact app-owned Compose model before serialization.
 */
export interface HostComposeModel {
  readonly projectName: string
  readonly labels: HostContainerLabels
  readonly services: Readonly<Record<string, HostContainerService>>
  readonly networks: Readonly<Record<string, HostContainerNetwork>>
  readonly volumes: Readonly<Record<string, HostContainerVolume>>
}

/**
 * Binds trusted executables, socket identity, engine identity, and Compose model to one ARM64 policy.
 */
export interface HostContainerPolicy {
  readonly schemaVersion: 1
  readonly platform: 'darwin'
  readonly architecture: 'arm64'
  readonly dockerExecutable: string
  readonly dockerComposeExecutable: string
  readonly dockerConfigDirectory: string
  readonly workingDirectory: string
  readonly runtimeProfile: string
  readonly runtimeProfileRoot: string
  readonly dockerContext: string
  readonly endpoint: {
    readonly scheme: 'unix'
    readonly path: string
    readonly canonicalPath: string
    readonly ownerUid: number
    readonly mode: number
  }
  readonly engine: {
    readonly id: string
    readonly serverVersion: string
    readonly apiVersion: string
    readonly operatingSystem: 'linux'
    readonly architecture: 'arm64'
    readonly securityOptions: readonly string[]
  }
  readonly compose: HostComposeModel
}

/**
 * Carries the policy-derived Compose plan authorized for one local runtime profile.
 */
export interface HostComposePlan extends HostComposeModel {
  readonly schemaVersion: 1
  readonly runtimeProfile: string
}

/**
 * Records socket identity used to detect ownership or inode changes between verification and execution.
 */
export interface HostEndpointObservation {
  readonly scheme: 'unix'
  readonly path: string
  readonly canonicalPath: string
  readonly ownerUid: number
  readonly mode: number
  readonly device: number
  readonly inode: number
  readonly kind: 'socket'
}

/**
 * Reports the selected Docker context and engine identity for comparison with policy.
 */
export interface HostRuntimeObservation {
  readonly runtimeProfile: string
  readonly dockerContext: string
  readonly endpoint: string
  readonly engineId: string
  readonly serverVersion: string
  readonly apiVersion: string
  readonly operatingSystem: string
  readonly architecture: string
  readonly securityOptions: readonly string[]
}

/**
 * Represents inventory metadata used to reject unlabeled or conflicting Docker resources.
 */
export interface HostOwnedResource {
  readonly kind: 'container' | 'network' | 'volume'
  readonly name: string
  readonly labels: Readonly<Record<string, string>>
}

/**
 * Captures an engine-inspected mount for comparison with the named-volume-only plan.
 */
export interface HostRealizedContainerMount {
  readonly kind: string
  readonly source: string
  readonly target: string
  readonly readOnly: boolean
}

/**
 * Captures an engine-inspected port binding for comparison with the loopback-only plan.
 */
export interface HostRealizedContainerPort {
  readonly hostIp: string
  readonly published: number
  readonly target: number
  readonly protocol: string
}

/**
 * Captures the engine-inspected logging driver and retention options.
 */
export interface HostRealizedContainerLogging {
  readonly driver: string
  readonly options: Readonly<Record<string, string>>
}

/**
 * Records the complete engine-inspected service state checked against the hardened plan.
 */
export interface HostRealizedContainer {
  readonly containerName: string
  readonly image: string
  readonly user: string
  readonly readOnly: boolean
  readonly capDrop: readonly string[]
  readonly capAdd: readonly string[]
  readonly securityOptions: readonly string[]
  readonly init: boolean
  readonly privileged: boolean
  readonly deviceCount: number
  readonly deviceRequestCount: number
  readonly deviceCgroupRuleCount: number
  readonly nanoCpus: number
  readonly memoryBytes: number
  readonly pidsLimit: number
  readonly logging: HostRealizedContainerLogging
  readonly mounts: readonly HostRealizedContainerMount[]
  readonly networks: readonly string[]
  readonly ports: readonly HostRealizedContainerPort[]
}

/**
 * Records whether an engine-inspected app network remains internal.
 */
export interface HostRealizedNetwork {
  readonly name: string
  readonly internal: boolean
}

/**
 * Groups the inspected container and network facts used for post-operation verification.
 */
export interface HostRealizedState {
  readonly containers: readonly HostRealizedContainer[]
  readonly networks: readonly HostRealizedNetwork[]
}

/**
 * Lists lifecycle mutations available through the host-control authorization boundary.
 */
export type HostContainerOperation =
  | 'start'
  | 'stop'
  | 'repair'
  | 'uninstall-preserve'
  | 'uninstall-delete'

/**
 * Abstracts bounded Docker observation, inspection, and application so policy is verified before mutation.
 */
export interface HostContainerControlPort {
  observe: (policy: HostContainerPolicy) => Promise<HostRuntimeObservation>
  inventory: (policy: HostContainerPolicy) => Promise<readonly HostOwnedResource[]>
  inspectResources: (
    policy: HostContainerPolicy,
    resources: readonly HostOwnedResource[],
  ) => Promise<HostRealizedState>
  apply: (
    policy: HostContainerPolicy,
    plan: HostComposePlan,
    operation: HostContainerOperation,
  ) => Promise<void>
}

/**
 * Binds a short-lived opaque consent token to one destructive host operation.
 */
export interface HostOperationAuthorization {
  readonly token: string
  readonly operation: Exclude<HostContainerOperation, 'stop'>
}

/**
 * Returns a sanitized verification result without socket paths or engine identifiers.
 */
export interface HostContainerDiagnostics {
  readonly status: 'verified'
  readonly operation: HostContainerOperation | 'inspect'
  readonly resourceCount: number
  readonly serverVersion: string
  readonly apiVersion: string
}

function fail(code: HostContainerControlErrorCode): never {
  throw new HostContainerControlError(code)
}

/** Applies the bounded structured-clone policy before inspecting renderer-derived values. */
function validateClone(value: unknown): void {
  validateStructuredClone(value, POLICY_LIMITS)
}

function exactRecord(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (
    value === null
    || typeof value !== 'object'
    || Array.isArray(value)
    || Object.getPrototypeOf(value) !== Object.prototype
  ) throw new Error('record')
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error('keys')
  }
  return value as Record<string, unknown>
}

function requiredString(value: unknown, pattern?: RegExp): string {
  if (typeof value !== 'string' || value.length === 0 || value.length > 4096) {
    throw new Error('string')
  }
  if (pattern && !pattern.test(value)) throw new Error('string')
  return value
}

function requiredBoolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('boolean')
  return value
}

function requiredInteger(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    throw new Error('integer')
  }
  return value as number
}

function requiredMegabytes(value: unknown, minimum: number, maximum: number): string {
  const quantity = requiredString(value)
  const match = /^([1-9][0-9]{0,3})m$/.exec(quantity)
  if (match === null) throw new Error('megabytes')
  const megabytes = Number(match[1])
  if (megabytes < minimum || megabytes > maximum) throw new Error('megabytes')
  return quantity
}

function requiredArray(value: unknown, maximum = 128): unknown[] {
  if (!Array.isArray(value) || value.length > maximum) throw new Error('array')
  return value
}

/** Accepts only normalized absolute paths without control characters for host operations. */
function safeAbsolutePath(value: unknown): string {
  const path = requiredString(value)
  if (
    !isAbsolute(path)
    || path.includes('\0')
    || path.includes('\n')
    || path.includes('\r')
    || normalize(path) !== path
  ) throw new Error('path')
  return path
}

function isWithin(root: string, path: string): boolean {
  const child = relative(root, path)
  return child.length > 0 && child !== '..' && !child.startsWith('../') && !isAbsolute(child)
}

function parseLabels(value: unknown, runtimeProfile: string, projectName: string): HostContainerLabels {
  const record = exactRecord(value, [
    'com.ancestryllm.owner',
    'com.ancestryllm.profile',
    'com.ancestryllm.project',
  ])
  const labels = {
    'com.ancestryllm.owner': requiredString(record['com.ancestryllm.owner']),
    'com.ancestryllm.profile': requiredString(record['com.ancestryllm.profile']),
    'com.ancestryllm.project': requiredString(record['com.ancestryllm.project']),
  }
  if (
    labels['com.ancestryllm.owner'] !== 'ancestryllm'
    || labels['com.ancestryllm.profile'] !== runtimeProfile
    || labels['com.ancestryllm.project'] !== projectName
  ) throw new Error('labels')
  return labels
}

/** Parses the generated Compose model and enforces project-scoped names and security policy. */
function parseCompose(value: unknown, runtimeProfile: string): HostComposeModel {
  const record = exactRecord(value, ['projectName', 'labels', 'services', 'networks', 'volumes'])
  const projectName = requiredString(record.projectName, IDENTIFIER)
  if (!projectName.startsWith('ancestryllm-')) throw new Error('project')
  const labels = parseLabels(record.labels, runtimeProfile, projectName)

  const networkRecord = record.networks
  if (
    networkRecord === null
    || typeof networkRecord !== 'object'
    || Array.isArray(networkRecord)
    || Object.getPrototypeOf(networkRecord) !== Object.prototype
  ) throw new Error('networks')
  const networkEntries = Object.entries(networkRecord as Record<string, unknown>)
  if (networkEntries.length === 0 || networkEntries.length > 32) throw new Error('networks')
  const networks: Record<string, HostContainerNetwork> = {}
  for (const [name, candidate] of networkEntries) {
    if (!IDENTIFIER.test(name) || !name.startsWith(`${projectName}-`)) throw new Error('network')
    const parsed = exactRecord(candidate, ['internal'])
    if (requiredBoolean(parsed.internal) !== true) throw new Error('network')
    networks[name] = { internal: true }
  }

  const volumeRecord = record.volumes
  if (
    volumeRecord === null
    || typeof volumeRecord !== 'object'
    || Array.isArray(volumeRecord)
    || Object.getPrototypeOf(volumeRecord) !== Object.prototype
  ) throw new Error('volumes')
  const volumeEntries = Object.entries(volumeRecord as Record<string, unknown>)
  if (volumeEntries.length === 0 || volumeEntries.length > 32) throw new Error('volumes')
  const volumes: Record<string, HostContainerVolume> = {}
  for (const [name, candidate] of volumeEntries) {
    if (!IDENTIFIER.test(name) || !name.startsWith(`${projectName}-`)) throw new Error('volume')
    const parsed = exactRecord(candidate, ['preserveOnUninstall'])
    if (requiredBoolean(parsed.preserveOnUninstall) !== true) throw new Error('volume')
    volumes[name] = { preserveOnUninstall: true }
  }

  const serviceRecord = record.services
  if (
    serviceRecord === null
    || typeof serviceRecord !== 'object'
    || Array.isArray(serviceRecord)
    || Object.getPrototypeOf(serviceRecord) !== Object.prototype
  ) throw new Error('services')
  const serviceEntries = Object.entries(serviceRecord as Record<string, unknown>)
  if (serviceEntries.length === 0 || serviceEntries.length > 16) throw new Error('services')
  const services: Record<string, HostContainerService> = {}
  const containerNames = new Set<string>()
  const publishedEndpoints = new Set<string>()
  for (const [serviceName, candidate] of serviceEntries) {
    if (!IDENTIFIER.test(serviceName)) throw new Error('service')
    const service = exactRecord(candidate, [
      'containerName', 'image', 'user', 'readOnly', 'capDrop',
      'securityOptions', 'init', 'cpus', 'memory', 'pidsLimit', 'logging',
      'mounts', 'networks', 'ports',
    ])
    const containerName = requiredString(service.containerName, IDENTIFIER)
    if (!containerName.startsWith(`${projectName}-`) || containerNames.has(containerName)) {
      throw new Error('container')
    }
    containerNames.add(containerName)
    const image = requiredString(service.image, IMAGE)
    const user = requiredString(service.user, USER)
    if (requiredBoolean(service.readOnly) !== true || requiredBoolean(service.init) !== true) {
      throw new Error('service hardening')
    }
    const capDrop = requiredArray(service.capDrop)
    if (capDrop.length !== 1 || capDrop[0] !== 'ALL') throw new Error('capabilities')
    const securityOptions = requiredArray(service.securityOptions)
    if (securityOptions.length !== 1 || securityOptions[0] !== 'no-new-privileges:true') {
      throw new Error('security options')
    }
    const cpus = requiredString(service.cpus, CPU_LIMIT)
    const memory = requiredMegabytes(service.memory, 64, 8192)
    const pidsLimit = requiredInteger(service.pidsLimit, 16, 1024)
    const loggingRecord = exactRecord(service.logging, ['driver', 'maxSize', 'maxFiles'])
    if (loggingRecord.driver !== 'local') throw new Error('logging')
    const logging: HostContainerLogging = {
      driver: 'local',
      maxSize: requiredMegabytes(loggingRecord.maxSize, 1, 100),
      maxFiles: requiredInteger(loggingRecord.maxFiles, 1, 10),
    }
    const mounts = requiredArray(service.mounts, 32).map((mount): HostContainerMount => {
      const parsed = exactRecord(mount, ['kind', 'source', 'target', 'readOnly'])
      if (parsed.kind !== 'volume') throw new Error('mount')
      const source = requiredString(parsed.source, IDENTIFIER)
      if (!Object.hasOwn(volumes, source)) throw new Error('mount')
      const target = safeAbsolutePath(parsed.target)
      if (target === '/' || target.includes('docker.sock')) throw new Error('mount')
      return {
        kind: 'volume', source, target,
        readOnly: requiredBoolean(parsed.readOnly),
      }
    })
    const mountSources = new Set(mounts.map((mount) => mount.source))
    const mountTargets = new Set(mounts.map((mount) => mount.target))
    if (
      mounts.length === 0
      || mountSources.size !== mounts.length
      || mountTargets.size !== mounts.length
    ) throw new Error('mounts')
    const serviceNetworks = requiredArray(service.networks, 16).map((network) => {
      const name = requiredString(network, IDENTIFIER)
      if (!Object.hasOwn(networks, name)) throw new Error('network')
      return name
    })
    if (serviceNetworks.length === 0 || new Set(serviceNetworks).size !== serviceNetworks.length) {
      throw new Error('networks')
    }
    const ports = requiredArray(service.ports, 16).map((port): HostContainerPort => {
      const parsed = exactRecord(port, ['hostIp', 'published', 'target', 'protocol'])
      if (parsed.hostIp !== '127.0.0.1' || parsed.protocol !== 'tcp') throw new Error('port')
      return {
        hostIp: '127.0.0.1',
        published: requiredInteger(parsed.published, 1024, 65535),
        target: requiredInteger(parsed.target, 1, 65535),
        protocol: 'tcp',
      }
    })
    const publishedPorts = new Set(ports.map((port) => (
      `${port.hostIp}:${port.published}/${port.protocol}`
    )))
    const targetPorts = new Set(ports.map((port) => `${port.target}/${port.protocol}`))
    if (publishedPorts.size !== ports.length || targetPorts.size !== ports.length) {
      throw new Error('ports')
    }
    for (const endpoint of publishedPorts) {
      if (publishedEndpoints.has(endpoint)) throw new Error('ports')
      publishedEndpoints.add(endpoint)
    }
    services[serviceName] = {
      containerName,
      image,
      user,
      readOnly: true,
      capDrop: ['ALL'],
      securityOptions: ['no-new-privileges:true'],
      init: true,
      cpus,
      memory,
      pidsLimit,
      logging,
      mounts,
      networks: serviceNetworks,
      ports,
    }
  }
  return { projectName, labels, services, networks, volumes }
}

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value as Record<string, unknown>)) deepFreeze(child)
    Object.freeze(value)
  }
  return value
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value !== null && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => `${JSON.stringify(key)}:${canonical(child)}`)
      .join(',')}}`
  }
  const encoded = JSON.stringify(value)
  if (encoded === undefined) throw new Error('canonical')
  return encoded
}

/**
 * Parses host container policy deterministically under bounded Docker Compose process execution and immutable family-tree mounts.
 */
export function parseHostContainerPolicy(value: unknown): HostContainerPolicy {
  try {
    validateClone(value)
    const record = exactRecord(value, [
      'schemaVersion', 'platform', 'architecture', 'dockerExecutable', 'dockerComposeExecutable',
      'dockerConfigDirectory', 'workingDirectory', 'runtimeProfile',
      'runtimeProfileRoot', 'dockerContext', 'endpoint', 'engine', 'compose',
    ])
    if (record.schemaVersion !== 1 || record.platform !== 'darwin' || record.architecture !== 'arm64') {
      throw new Error('target')
    }
    const dockerExecutable = safeAbsolutePath(record.dockerExecutable)
    const dockerComposeExecutable = safeAbsolutePath(record.dockerComposeExecutable)
    const dockerConfigDirectory = safeAbsolutePath(record.dockerConfigDirectory)
    const workingDirectory = safeAbsolutePath(record.workingDirectory)
    const runtimeProfile = requiredString(record.runtimeProfile, IDENTIFIER)
    const runtimeProfileRoot = safeAbsolutePath(record.runtimeProfileRoot)
    const dockerContext = requiredString(record.dockerContext, IDENTIFIER)
    if (
      !runtimeProfile.startsWith('ancestryllm-')
      || dockerContext !== `colima-${runtimeProfile}`
      || !runtimeProfileRoot.endsWith(`/${runtimeProfile}`)
      || relative(runtimeProfileRoot, dockerConfigDirectory) !== 'docker-config'
      || relative(runtimeProfileRoot, workingDirectory) !== 'control'
    ) throw new Error('profile')

    const endpointRecord = exactRecord(
      record.endpoint,
      ['scheme', 'path', 'canonicalPath', 'ownerUid', 'mode'],
    )
    if (endpointRecord.scheme !== 'unix') throw new Error('endpoint')
    const endpointPath = safeAbsolutePath(endpointRecord.path)
    const canonicalPath = safeAbsolutePath(endpointRecord.canonicalPath)
    const ownerUid = requiredInteger(endpointRecord.ownerUid, 0, 2_147_483_647)
    const mode = requiredInteger(endpointRecord.mode, 0, 0o777)
    if (
      mode !== 0o600
      || endpointPath !== canonicalPath
      || !isWithin(runtimeProfileRoot, endpointPath)
    ) throw new Error('endpoint')

    const engineRecord = exactRecord(record.engine, [
      'id', 'serverVersion', 'apiVersion', 'operatingSystem', 'architecture', 'securityOptions',
    ])
    const securityOptions = requiredArray(engineRecord.securityOptions, 32).map((option) => (
      requiredString(option, SAFE_OPTION)
    ))
    if (
      engineRecord.operatingSystem !== 'linux'
      || engineRecord.architecture !== 'arm64'
      || new Set(securityOptions).size !== securityOptions.length
      || !securityOptions.includes('cgroupns')
      || !securityOptions.includes('seccomp')
    ) throw new Error('engine')
    const engine = {
      id: requiredString(engineRecord.id, ENGINE_ID),
      serverVersion: requiredString(engineRecord.serverVersion, VERSION),
      apiVersion: requiredString(engineRecord.apiVersion, VERSION),
      operatingSystem: 'linux' as const,
      architecture: 'arm64' as const,
      securityOptions: [...securityOptions].sort(),
    }
    const compose = parseCompose(record.compose, runtimeProfile)
    return deepFreeze({
      schemaVersion: 1,
      platform: 'darwin',
      architecture: 'arm64',
      dockerExecutable,
      dockerComposeExecutable,
      dockerConfigDirectory,
      workingDirectory,
      runtimeProfile,
      runtimeProfileRoot,
      dockerContext,
      endpoint: { scheme: 'unix', path: endpointPath, canonicalPath, ownerUid, mode },
      engine,
      compose,
    })
  } catch {
    return fail('INVALID_POLICY')
  }
}

/**
 * Parses host compose plan deterministically under bounded Docker Compose process execution and immutable family-tree mounts.
 */
export function parseHostComposePlan(value: unknown, policy: HostContainerPolicy): HostComposePlan {
  try {
    validateClone(value)
    const record = exactRecord(value, [
      'schemaVersion', 'runtimeProfile', 'projectName', 'labels', 'services', 'networks', 'volumes',
    ])
    if (record.schemaVersion !== 1 || record.runtimeProfile !== policy.runtimeProfile) {
      throw new Error('plan')
    }
    const compose = parseCompose({
      projectName: record.projectName,
      labels: record.labels,
      services: record.services,
      networks: record.networks,
      volumes: record.volumes,
    }, policy.runtimeProfile)
    if (canonical(compose) !== canonical(policy.compose)) throw new Error('plan')
    return deepFreeze({ schemaVersion: 1, runtimeProfile: policy.runtimeProfile, ...compose })
  } catch {
    return fail('INVALID_PLAN')
  }
}

/**
 * Inspects a canonical Unix socket without following aliases and records its identity for TOCTOU checks.
 */
export async function inspectUnixSocketEndpoint(path: string): Promise<HostEndpointObservation> {
  try {
    if (!isAbsolute(path) || normalize(path) !== path) throw new Error('path')
    const metadata = await lstat(path)
    if (metadata.isSymbolicLink() || !metadata.isSocket()) throw new Error('kind')
    const canonicalPath = await realpath(path)
    if (canonicalPath !== path) throw new Error('canonical')
    return Object.freeze({
      scheme: 'unix',
      path,
      canonicalPath,
      ownerUid: metadata.uid,
      mode: metadata.mode & 0o777,
      device: metadata.dev,
      inode: metadata.ino,
      kind: 'socket',
    })
  } catch {
    return fail('ENDPOINT_UNTRUSTED')
  }
}

function validateEndpointObservation(
  value: unknown,
  policy: HostContainerPolicy,
): HostEndpointObservation {
  try {
    validateClone(value)
    const record = exactRecord(value, [
      'scheme', 'path', 'canonicalPath', 'ownerUid', 'mode', 'device', 'inode', 'kind',
    ])
    const observation: HostEndpointObservation = {
      scheme: record.scheme === 'unix' ? 'unix' : (() => { throw new Error('scheme') })(),
      path: requiredString(record.path),
      canonicalPath: requiredString(record.canonicalPath),
      ownerUid: requiredInteger(record.ownerUid, 0, Number.MAX_SAFE_INTEGER),
      mode: requiredInteger(record.mode, 0, 0o777),
      device: requiredInteger(record.device, 0, Number.MAX_SAFE_INTEGER),
      inode: requiredInteger(record.inode, 1, Number.MAX_SAFE_INTEGER),
      kind: record.kind === 'socket' ? 'socket' : (() => { throw new Error('kind') })(),
    }
    if (
      observation.path !== policy.endpoint.path
      || observation.canonicalPath !== policy.endpoint.canonicalPath
      || observation.ownerUid !== policy.endpoint.ownerUid
      || observation.mode !== policy.endpoint.mode
    ) throw new Error('mismatch')
    return observation
  } catch {
    return fail('ENDPOINT_UNTRUSTED')
  }
}

function assertSameEndpoint(
  before: HostEndpointObservation,
  after: HostEndpointObservation,
): void {
  if (
    before.scheme !== after.scheme
    || before.path !== after.path
    || before.canonicalPath !== after.canonicalPath
    || before.ownerUid !== after.ownerUid
    || before.mode !== after.mode
    || before.device !== after.device
    || before.inode !== after.inode
    || before.kind !== after.kind
  ) fail('ENDPOINT_CHANGED')
}

/** Validates that the observed container engine still matches the trusted runtime policy. */
function validateRuntimeObservation(value: unknown, policy: HostContainerPolicy): HostRuntimeObservation {
  try {
    validateClone(value)
    const record = exactRecord(value, [
      'runtimeProfile', 'dockerContext', 'endpoint', 'engineId', 'serverVersion',
      'apiVersion', 'operatingSystem', 'architecture', 'securityOptions',
    ])
    const securityOptions = requiredArray(record.securityOptions, 32)
      .map((option) => requiredString(option, SAFE_OPTION)).sort()
    const observation: HostRuntimeObservation = {
      runtimeProfile: requiredString(record.runtimeProfile),
      dockerContext: requiredString(record.dockerContext),
      endpoint: requiredString(record.endpoint),
      engineId: requiredString(record.engineId),
      serverVersion: requiredString(record.serverVersion),
      apiVersion: requiredString(record.apiVersion),
      operatingSystem: requiredString(record.operatingSystem),
      architecture: requiredString(record.architecture),
      securityOptions,
    }
    if (
      observation.runtimeProfile !== policy.runtimeProfile
      || observation.dockerContext !== policy.dockerContext
      || observation.endpoint !== `unix://${policy.endpoint.path}`
      || observation.engineId !== policy.engine.id
      || observation.serverVersion !== policy.engine.serverVersion
      || observation.apiVersion !== policy.engine.apiVersion
      || observation.operatingSystem !== policy.engine.operatingSystem
      || observation.architecture !== policy.engine.architecture
      || canonical(observation.securityOptions) !== canonical(policy.engine.securityOptions)
    ) throw new Error('runtime')
    return observation
  } catch {
    return fail('ENGINE_UNTRUSTED')
  }
}

/** Confines the discovered resource inventory to the exact project-owned resource set. */
function validateInventory(
  value: unknown,
  policy: HostContainerPolicy,
): readonly HostOwnedResource[] {
  try {
    validateClone(value)
    const resources = requiredArray(value, 256)
    const expected = new Set<string>()
    for (const service of Object.values(policy.compose.services)) {
      expected.add(`container:${service.containerName}`)
    }
    for (const name of Object.keys(policy.compose.networks)) expected.add(`network:${name}`)
    for (const name of Object.keys(policy.compose.volumes)) expected.add(`volume:${name}`)
    const seen = new Set<string>()
    return resources.map((candidate): HostOwnedResource => {
      const record = exactRecord(candidate, ['kind', 'name', 'labels'])
      if (record.kind !== 'container' && record.kind !== 'network' && record.kind !== 'volume') {
        throw new Error('kind')
      }
      const name = requiredString(record.name, IDENTIFIER)
      const key = `${record.kind}:${name}`
      if (!expected.has(key)) throw new Error('resource')
      if (seen.has(key)) throw new Error('duplicate')
      seen.add(key)
      const labels = parseLabels(record.labels, policy.runtimeProfile, policy.compose.projectName)
      return { kind: record.kind, name, labels: { ...labels } }
    })
  } catch {
    return fail('RESOURCE_CONFLICT')
  }
}

function observationString(value: unknown): string {
  const result = requiredString(value)
  if (result.includes('\0') || result.includes('\n') || result.includes('\r')) {
    throw new Error('observation string')
  }
  return result
}

function observationStrings(value: unknown, maximum = 128): string[] {
  const strings = requiredArray(value, maximum).map(observationString)
  if (new Set(strings).size !== strings.length) throw new Error('duplicate')
  return strings.sort()
}

function observationStringRecord(value: unknown): Readonly<Record<string, string>> {
  if (
    value === null
    || typeof value !== 'object'
    || Array.isArray(value)
    || Object.getPrototypeOf(value) !== Object.prototype
  ) throw new Error('record')
  const entries = Object.entries(value as Record<string, unknown>)
  if (entries.length > 32) throw new Error('record')
  return Object.fromEntries(entries.map(([key, candidate]): [string, string] => [
    observationString(key), observationString(candidate),
  ]).sort((left, right) => left[0].localeCompare(right[0])))
}

/** Verifies realized containers and networks against the accepted Compose security contract. */
function validateRealizedResources(
  value: unknown,
  policy: HostContainerPolicy,
  resources: readonly HostOwnedResource[],
): HostRealizedState {
  try {
    validateClone(value)
    const state = exactRecord(value, ['containers', 'networks'])
    const expectedContainers = new Set(resources
      .filter((resource) => resource.kind === 'container')
      .map((resource) => resource.name))
    const services = new Map(Object.values(policy.compose.services).map((service) => [
      service.containerName, service,
    ]))
    const seen = new Set<string>()
    const containers = requiredArray(state.containers, 16)
      .map((candidate): HostRealizedContainer => {
      const record = exactRecord(candidate, [
        'containerName', 'image', 'user', 'readOnly', 'capDrop', 'capAdd',
        'securityOptions', 'init', 'privileged', 'deviceCount', 'deviceRequestCount',
        'deviceCgroupRuleCount', 'nanoCpus', 'memoryBytes', 'pidsLimit', 'logging',
        'mounts', 'networks', 'ports',
      ])
      const containerName = observationString(record.containerName)
      if (!expectedContainers.has(containerName) || seen.has(containerName)) {
        throw new Error('container')
      }
      seen.add(containerName)
      const loggingRecord = exactRecord(record.logging, ['driver', 'options'])
      const realization: HostRealizedContainer = {
        containerName,
        image: observationString(record.image),
        user: observationString(record.user),
        readOnly: requiredBoolean(record.readOnly),
        capDrop: observationStrings(record.capDrop, 64),
        capAdd: observationStrings(record.capAdd, 64),
        securityOptions: observationStrings(record.securityOptions, 64),
        init: requiredBoolean(record.init),
        privileged: requiredBoolean(record.privileged),
        deviceCount: requiredInteger(record.deviceCount, 0, 128),
        deviceRequestCount: requiredInteger(record.deviceRequestCount, 0, 128),
        deviceCgroupRuleCount: requiredInteger(record.deviceCgroupRuleCount, 0, 128),
        nanoCpus: requiredInteger(record.nanoCpus, 0, Number.MAX_SAFE_INTEGER),
        memoryBytes: requiredInteger(record.memoryBytes, 0, Number.MAX_SAFE_INTEGER),
        pidsLimit: requiredInteger(record.pidsLimit, 0, Number.MAX_SAFE_INTEGER),
        logging: {
          driver: observationString(loggingRecord.driver),
          options: observationStringRecord(loggingRecord.options),
        },
        mounts: requiredArray(record.mounts, 32).map((mount) => {
          const parsed = exactRecord(mount, ['kind', 'source', 'target', 'readOnly'])
          return {
            kind: observationString(parsed.kind),
            source: observationString(parsed.source),
            target: observationString(parsed.target),
            readOnly: requiredBoolean(parsed.readOnly),
          }
        }).sort((left, right) => canonical(left).localeCompare(canonical(right))),
        networks: observationStrings(record.networks, 32),
        ports: requiredArray(record.ports, 32).map((port) => {
          const parsed = exactRecord(port, ['hostIp', 'published', 'target', 'protocol'])
          return {
            hostIp: observationString(parsed.hostIp),
            published: requiredInteger(parsed.published, 1, 65535),
            target: requiredInteger(parsed.target, 1, 65535),
            protocol: observationString(parsed.protocol),
          }
        }).sort((left, right) => canonical(left).localeCompare(canonical(right))),
      }
      const service = services.get(containerName)
      if (!service) throw new Error('service')
      const expected: HostRealizedContainer = {
        containerName: service.containerName,
        image: service.image,
        user: service.user,
        readOnly: service.readOnly,
        capDrop: [...service.capDrop].sort(),
        capAdd: [],
        securityOptions: [...service.securityOptions].sort(),
        init: service.init,
        privileged: false,
        deviceCount: 0,
        deviceRequestCount: 0,
        deviceCgroupRuleCount: 0,
        nanoCpus: Math.round(Number(service.cpus) * 1_000_000_000),
        memoryBytes: Number.parseInt(service.memory, 10) * 1024 * 1024,
        pidsLimit: service.pidsLimit,
        logging: {
          driver: service.logging.driver,
          options: {
            'max-file': String(service.logging.maxFiles),
            'max-size': service.logging.maxSize,
          },
        },
        mounts: [...service.mounts]
          .sort((left, right) => canonical(left).localeCompare(canonical(right))),
        networks: [...service.networks].sort(),
        ports: [...service.ports]
          .sort((left, right) => canonical(left).localeCompare(canonical(right))),
      }
      if (canonical(realization) !== canonical(expected)) throw new Error('hardening')
      return realization
      })
    if (seen.size !== expectedContainers.size) throw new Error('missing container')

    const expectedNetworks = new Set(resources
      .filter((resource) => resource.kind === 'network')
      .map((resource) => resource.name))
    const seenNetworks = new Set<string>()
    const networks = requiredArray(state.networks, 32).map((candidate): HostRealizedNetwork => {
      const record = exactRecord(candidate, ['name', 'internal'])
      const name = observationString(record.name)
      if (!expectedNetworks.has(name) || seenNetworks.has(name)) throw new Error('network')
      seenNetworks.add(name)
      const network: HostRealizedNetwork = {
        name,
        internal: requiredBoolean(record.internal),
      }
      const expected = policy.compose.networks[name]
      if (expected === undefined || network.internal !== expected.internal) {
        throw new Error('network hardening')
      }
      return network
    })
    if (seenNetworks.size !== expectedNetworks.size) throw new Error('missing network')
    return { containers, networks }
  } catch {
    return fail('RESOURCE_CONFLICT')
  }
}

/** Fails closed unless post-operation resources exactly match the requested lifecycle outcome. */
function assertExpectedPostOperationResources(
  resources: readonly HostOwnedResource[],
  policy: HostContainerPolicy,
  operation: HostContainerOperation,
): void {
  const expected = operation === 'uninstall-delete'
    ? []
    : operation === 'uninstall-preserve'
      ? Object.keys(policy.compose.volumes).map((name) => `volume:${name}`)
      : [
          ...Object.values(policy.compose.services)
            .map((service) => `container:${service.containerName}`),
          ...Object.keys(policy.compose.networks).map((name) => `network:${name}`),
          ...Object.keys(policy.compose.volumes).map((name) => `volume:${name}`),
        ]
  const actual = resources.map((resource) => `${resource.kind}:${resource.name}`)
  if (canonical(actual.sort()) !== canonical(expected.sort())) fail('RESOURCE_CONFLICT')
}

type AuthorizationOperation = Exclude<HostContainerOperation, 'stop'>

/**
 * Returns the exact consent phrase required before a mutating host-container operation.
 */
export function confirmationPhrase(operation: AuthorizationOperation, projectName: string): string {
  switch (operation) {
    case 'start': return `AUTHORIZE ${projectName} HOST CONTROL`
    case 'repair': return `REPAIR ${projectName} HOST CONTROL`
    case 'uninstall-preserve': return `UNINSTALL ${projectName} AND PRESERVE DATA`
    case 'uninstall-delete': return `DELETE ${projectName} DATA`
  }
}

/**
 * Injects the reviewed policy and plan plus bounded endpoint, token, and clock seams.
 */
export interface HostContainerSupervisorOptions {
  readonly policy: unknown
  readonly plan: unknown
  readonly control: HostContainerControlPort
  readonly inspectEndpoint?: (path: string) => Promise<HostEndpointObservation>
  readonly tokenFactory?: () => string
  readonly now?: () => number
}

interface VerifiedState {
  readonly endpoint: HostEndpointObservation
  readonly runtime: HostRuntimeObservation
  readonly resources: readonly HostOwnedResource[]
}

/**
 * Owns host container supervisor state transitions while enforcing bounded Docker Compose process execution and immutable family-tree mounts.
 */
export class HostContainerSupervisor {
  private readonly policy: HostContainerPolicy
  private readonly plan: HostComposePlan
  private readonly authorizations = new Map<string, { operation: AuthorizationOperation, expiresAt: number }>()
  private busy = false

  constructor(private readonly options: HostContainerSupervisorOptions) {
    this.policy = parseHostContainerPolicy(options.policy)
    this.plan = parseHostComposePlan(options.plan, this.policy)
  }

  authorize(
    operation: AuthorizationOperation,
    phrase: string,
  ): HostOperationAuthorization {
    if (phrase !== confirmationPhrase(operation, this.policy.compose.projectName)) {
      return fail('AUTHORIZATION_REQUIRED')
    }
    const token = (this.options.tokenFactory ?? (() => randomBytes(32).toString('base64url')))()
    if (!/^[A-Za-z0-9_-]{32,128}$/.test(token) || this.authorizations.has(token)) {
      return fail('AUTHORIZATION_REQUIRED')
    }
    this.authorizations.set(token, {
      operation,
      expiresAt: (this.options.now ?? Date.now)() + AUTHORIZATION_TTL_MS,
    })
    return Object.freeze({ token, operation })
  }

  async inspect(): Promise<HostContainerDiagnostics> {
    const state = await this.preflight()
    return this.diagnostics('inspect', state)
  }

  async start(authorization: HostOperationAuthorization): Promise<HostContainerDiagnostics> {
    this.consumeAuthorization('start', authorization)
    return this.mutate('start')
  }

  async stop(): Promise<HostContainerDiagnostics> {
    return this.mutate('stop')
  }

  async repair(authorization: HostOperationAuthorization): Promise<HostContainerDiagnostics> {
    this.consumeAuthorization('repair', authorization)
    return this.mutate('repair')
  }

  async uninstall(options: {
    readonly deleteData: boolean
    readonly authorization: HostOperationAuthorization
  }): Promise<HostContainerDiagnostics> {
    const operation = options.deleteData ? 'uninstall-delete' : 'uninstall-preserve'
    this.consumeAuthorization(operation, options.authorization)
    return this.mutate(operation)
  }

  private consumeAuthorization(
    operation: AuthorizationOperation,
    authorization: HostOperationAuthorization,
  ): void {
    if (
      authorization === null
      || typeof authorization !== 'object'
      || typeof authorization.token !== 'string'
      || authorization.operation !== operation
    ) return fail('AUTHORIZATION_REQUIRED')
    const stored = this.authorizations.get(authorization.token)
    this.authorizations.delete(authorization.token)
    const now = (this.options.now ?? Date.now)()
    if (!stored || stored.operation !== operation || stored.expiresAt < now) {
      return fail('AUTHORIZATION_REQUIRED')
    }
  }

  private async mutate(operation: HostContainerOperation): Promise<HostContainerDiagnostics> {
    if (this.busy) return fail('CONTROL_FAILED')
    this.busy = true
    try {
      const before = await this.preflight()
      try {
        await this.options.control.apply(this.policy, this.plan, operation)
      } catch {
        return fail('CONTROL_FAILED')
      }
      const state = await this.preflight()
      assertSameEndpoint(before.endpoint, state.endpoint)
      assertExpectedPostOperationResources(state.resources, this.policy, operation)
      return this.diagnostics(operation, state)
    } finally {
      this.busy = false
    }
  }

  private async preflight(): Promise<VerifiedState> {
    const first = await this.inspectTrustedEndpoint()
    let runtimeValue: HostRuntimeObservation
    let inventoryValue: readonly HostOwnedResource[]
    try {
      runtimeValue = await this.options.control.observe(this.policy)
      inventoryValue = await this.options.control.inventory(this.policy)
    } catch {
      return fail('CONTROL_FAILED')
    }
    const runtime = validateRuntimeObservation(runtimeValue, this.policy)
    const resources = validateInventory(inventoryValue, this.policy)
    let realizedValue: HostRealizedState
    try {
      realizedValue = await this.options.control.inspectResources(this.policy, resources)
    } catch {
      return fail('CONTROL_FAILED')
    }
    validateRealizedResources(realizedValue, this.policy, resources)
    const second = await this.inspectTrustedEndpoint()
    assertSameEndpoint(first, second)
    return { endpoint: second, runtime, resources }
  }

  private async inspectTrustedEndpoint(): Promise<HostEndpointObservation> {
    try {
      const inspect = this.options.inspectEndpoint ?? inspectUnixSocketEndpoint
      const value = await inspect(this.policy.endpoint.path)
      return validateEndpointObservation(value, this.policy)
    } catch (error) {
      if (error instanceof HostContainerControlError) throw error
      return fail('ENDPOINT_UNTRUSTED')
    }
  }

  private diagnostics(
    operation: HostContainerDiagnostics['operation'],
    state: VerifiedState,
  ): HostContainerDiagnostics {
    return Object.freeze({
      status: 'verified',
      operation,
      resourceCount: state.resources.length,
      serverVersion: state.runtime.serverVersion,
      apiVersion: state.runtime.apiVersion,
    })
  }
}
