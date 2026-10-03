/** Validates the macOS ARM64 runtime trust policy and safely extracts verified tools. */

import { createHash, timingSafeEqual } from 'node:crypto'
import { chmod, lstat, mkdir, open } from 'node:fs/promises'
import { isAbsolute, join, posix, resolve, sep } from 'node:path'
import { gunzipSync } from 'node:zlib'

const SHA256_PATTERN = /^[0-9a-f]{64}$/
const SHA512_PATTERN = /^[0-9a-f]{128}$/
const VERSION_PATTERN = /^\d+\.\d+\.\d+$/
const ASSET_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,159}$/
const COMPONENT_NAMES = [
  'colima',
  'lima',
  'docker-cli',
  'docker-buildx',
  'docker-compose',
] as const
const COMPONENT_REPOSITORIES: Readonly<Record<RuntimeComponentName, string>> = {
  colima: 'abiosoft/colima',
  lima: 'lima-vm/lima',
  'docker-cli': 'docker/cli',
  'docker-buildx': 'docker/buildx',
  'docker-compose': 'docker/compose',
}
const REVIEWED_LICENSES = new Set(['Apache-2.0', 'MIT'])
const MAX_COMPONENT_BYTES = 512 * 1024 * 1024
const MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024

/**
 * Restricts installation to the exact reviewed runtime component set.
 */
export type RuntimeComponentName = typeof COMPONENT_NAMES[number]
/**
 * Limits component packaging to directly hashed binaries or reviewed gzip-compressed tar archives.
 */
export type RuntimeArchiveFormat = 'binary' | 'tar.gz'

/**
 * Maps one verified archive member to its bounded app-owned destination and executable mode.
 */
export interface RuntimeInstallEntry {
  readonly sourcePath: string
  readonly installPath: string
  readonly sha256: string
  readonly sizeBytes: number
  readonly executable: boolean
}

/**
 * Records a reviewed symlink that must be omitted rather than followed during extraction.
 */
export interface RuntimeExcludedArchiveMember {
  readonly sourcePath: string
  readonly type: 'symlink'
  readonly linkTarget: string
}

/**
 * Binds an exact release asset URL, digest, size, format, and extraction allowlist.
 */
export interface RuntimeArtifactPolicy {
  readonly assetName: string
  readonly url: string
  readonly sha256: string
  readonly sizeBytes: number
  readonly archiveFormat: RuntimeArchiveFormat
  readonly install: readonly RuntimeInstallEntry[]
  readonly excludedMembers: readonly RuntimeExcludedArchiveMember[]
}

/**
 * Binds each component to a reviewed SPDX license document and digest.
 */
export interface RuntimeLicensePolicy {
  readonly spdxId: 'Apache-2.0' | 'MIT'
  readonly url: string
  readonly sha256: string
  readonly sizeBytes: number
}

/**
 * Joins a component's trusted source identity and version to its license and release artifact.
 */
export interface RuntimeComponentPolicy {
  readonly name: RuntimeComponentName
  readonly version: string
  readonly repository: string
  readonly license: RuntimeLicensePolicy
  readonly artifact: RuntimeArtifactPolicy
}

/**
 * Pins the exact ARM64 VM image provenance, filename, size, and dual digests.
 */
export interface RuntimeVmImagePolicy {
  readonly version: string
  readonly repository: 'abiosoft/colima-core'
  readonly assetName: 'ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz'
  readonly url: string
  readonly sha256: string
  readonly sha512: string
  readonly sizeBytes: number
}

/**
 * Defines the complete reviewed trust root, host constraints, ownership namespace, and resource bounds.
 */
export interface MacosArm64RuntimePolicy {
  readonly schemaVersion: 1
  readonly target: Readonly<{
    platform: 'darwin'
    architecture: 'arm64'
    minimumMacosMajor: number
    minimumFreeGib: number
  }>
  readonly ownership: Readonly<{
    profile: 'ancestryllm-local-arm64'
    context: 'colima-ancestryllm-local-arm64'
  }>
  readonly resources: Readonly<{
    minimumCpus: number
    maximumCpus: number
    minimumMemoryGib: number
    maximumMemoryGib: number
    diskGib: number
  }>
  readonly vmImage: RuntimeVmImagePolicy
  readonly components: readonly RuntimeComponentPolicy[]
}

/**
 * Reports a stable coded failure from reviewed macOS ARM64 runtime artifact installation without leaking sensitive host details.
 */
export class RuntimePolicyError extends Error {
  readonly code: string

  constructor(code = 'RUNTIME_POLICY_INVALID') {
    super(code)
    this.name = 'RuntimePolicyError'
    this.code = code
  }
}

function fail(code = 'RUNTIME_POLICY_INVALID'): never {
  throw new RuntimePolicyError(code)
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) fail()
  return value as Record<string, unknown>
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): void {
  const actual = Object.keys(value).sort()
  const wanted = [...expected].sort()
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) fail()
}

function exactString(value: unknown, expected?: string): string {
  if (typeof value !== 'string' || value.length === 0 || value.length > 512) fail()
  if (expected !== undefined && value !== expected) fail()
  return value
}

function integer(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isInteger(value) || (value as number) < minimum || (value as number) > maximum) fail()
  return value as number
}

function digest(value: unknown): string {
  const parsed = exactString(value)
  if (!SHA256_PATTERN.test(parsed)) fail()
  return parsed
}

function digest512(value: unknown): string {
  const parsed = exactString(value)
  if (!SHA512_PATTERN.test(parsed)) fail()
  return parsed
}

/** Accepts only normalized POSIX-relative archive paths without traversal or drive roots. */
function safeRelativePath(value: unknown): string {
  const parsed = exactString(value)
  if (
    parsed.includes('\\')
    || parsed.includes('\0')
    || isAbsolute(parsed)
    || /^[A-Za-z]:/.test(parsed)
  ) fail()
  const normalized = posix.normalize(parsed)
  const segments = parsed.split('/')
  if (
    normalized === '.'
    || normalized !== parsed
    || segments.some((segment) => segment === '' || segment === '.' || segment === '..')
  ) fail()
  return parsed
}

function parseLicense(
  value: unknown,
  repository: string,
  version: string,
): RuntimeLicensePolicy {
  const input = record(value)
  exactKeys(input, ['spdx_id', 'url', 'sha256', 'size_bytes'])
  const spdxId = exactString(input.spdx_id)
  if (!REVIEWED_LICENSES.has(spdxId)) fail()
  const url = exactString(input.url)
  const permitted = [
    `https://raw.githubusercontent.com/${repository}/v${version}/LICENSE`,
    `https://raw.githubusercontent.com/${repository}/v${version}/LICENSE.md`,
    `https://raw.githubusercontent.com/${repository}/v${version}/LICENSE.txt`,
  ]
  if (!permitted.includes(url)) fail()
  return {
    spdxId: spdxId as RuntimeLicensePolicy['spdxId'],
    url,
    sha256: digest(input.sha256),
    sizeBytes: integer(input.size_bytes, 1, 1024 * 1024),
  }
}

function parseInstall(
  value: unknown,
  componentName: RuntimeComponentName,
): readonly RuntimeInstallEntry[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 64) fail()
  const install = value.map((entry): RuntimeInstallEntry => {
    const input = record(entry)
    exactKeys(input, [
      'source_path',
      'install_path',
      'sha256',
      'size_bytes',
      'executable',
    ])
    if (typeof input.executable !== 'boolean') fail()
    const installPath = safeRelativePath(input.install_path)
    const permittedDestination = installPath.startsWith('bin/')
      || installPath.startsWith('docker-config/cli-plugins/')
      || (componentName === 'lima' && installPath.startsWith('share/lima/'))
    if (!permittedDestination) fail()
    return {
      sourcePath: safeRelativePath(input.source_path),
      installPath,
      sha256: digest(input.sha256),
      sizeBytes: integer(input.size_bytes, 1, MAX_COMPONENT_BYTES),
      executable: input.executable,
    }
  })
  const sources = new Set(install.map(({ sourcePath }) => sourcePath))
  const destinations = new Set(install.map(({ installPath }) => installPath))
  if (sources.size !== install.length || destinations.size !== install.length) fail()
  return install
}

function parseExcludedMembers(
  value: unknown,
  componentName: RuntimeComponentName,
): readonly RuntimeExcludedArchiveMember[] {
  if (!Array.isArray(value) || value.length > 16) fail()
  const members = value.map((entry): RuntimeExcludedArchiveMember => {
    const input = record(entry)
    exactKeys(input, ['source_path', 'type', 'link_target'])
    const type = exactString(input.type, 'symlink')
    return {
      sourcePath: safeRelativePath(input.source_path),
      type: type as 'symlink',
      linkTarget: exactString(input.link_target),
    }
  })
  const identities = new Set(members.map(({ sourcePath, type, linkTarget }) => (
    `${sourcePath}\0${type}\0${linkTarget}`
  )))
  if (identities.size !== members.length) fail()
  if (componentName !== 'lima' && members.length !== 0) fail()
  return members
}

function parseArtifact(
  value: unknown,
  componentName: RuntimeComponentName,
  repository: string,
  version: string,
): RuntimeArtifactPolicy {
  const input = record(value)
  exactKeys(input, [
    'asset_name',
    'url',
    'sha256',
    'size_bytes',
    'archive_format',
    'install',
    'excluded_members',
  ])
  const assetName = exactString(input.asset_name)
  if (!ASSET_PATTERN.test(assetName)) fail()
  const url = exactString(input.url)
  const githubUrl = `https://github.com/${repository}/releases/download/v${version}/${assetName}`
  const dockerUrl = `https://download.docker.com/mac/static/stable/aarch64/${assetName}`
  if (url !== githubUrl && !(componentName === 'docker-cli' && url === dockerUrl)) fail()
  const archiveFormat = exactString(input.archive_format)
  if (archiveFormat !== 'binary' && archiveFormat !== 'tar.gz') fail()
  const install = parseInstall(input.install, componentName)
  const excludedMembers = parseExcludedMembers(input.excluded_members, componentName)
  if (archiveFormat === 'binary') {
    if (
      install.length !== 1
      || install[0]?.sourcePath !== assetName
      || excludedMembers.length !== 0
    ) fail()
  }
  return {
    assetName,
    url,
    sha256: digest(input.sha256),
    sizeBytes: integer(input.size_bytes, 1, MAX_COMPONENT_BYTES),
    archiveFormat,
    install,
    excludedMembers,
  }
}

function parseComponent(value: unknown, expectedName: RuntimeComponentName): RuntimeComponentPolicy {
  const input = record(value)
  exactKeys(input, ['name', 'version', 'repository', 'license', 'artifact'])
  const name = exactString(input.name, expectedName) as RuntimeComponentName
  const repository = exactString(input.repository, COMPONENT_REPOSITORIES[name])
  const version = exactString(input.version)
  if (!VERSION_PATTERN.test(version)) fail()
  return {
    name,
    version,
    repository,
    license: parseLicense(input.license, repository, version),
    artifact: parseArtifact(input.artifact, name, repository, version),
  }
}

function parseVmImage(value: unknown): RuntimeVmImagePolicy {
  const input = record(value)
  exactKeys(input, [
    'version',
    'repository',
    'asset_name',
    'url',
    'sha256',
    'sha512',
    'size_bytes',
  ])
  const version = exactString(input.version)
  if (!VERSION_PATTERN.test(version)) fail()
  const repository = exactString(input.repository, 'abiosoft/colima-core')
  const assetName = exactString(
    input.asset_name,
    'ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz',
  )
  const url = exactString(input.url)
  if (url !== `https://github.com/${repository}/releases/download/v${version}/${assetName}`) fail()
  return {
    version,
    repository: 'abiosoft/colima-core',
    assetName: 'ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz',
    url,
    sha256: digest(input.sha256),
    sha512: digest512(input.sha512),
    sizeBytes: integer(input.size_bytes, 1, MAX_COMPONENT_BYTES),
  }
}

/**
 * Parses macos arm64 runtime policy deterministically under reviewed macOS ARM64 runtime artifact installation.
 */
export function parseMacosArm64RuntimePolicy(value: unknown): MacosArm64RuntimePolicy {
  const input = record(value)
  exactKeys(input, [
    'schema_version',
    'target',
    'ownership',
    'resources',
    'vm_image',
    'components',
  ])
  if (input.schema_version !== 1) fail('RUNTIME_POLICY_SCHEMA_UNSUPPORTED')

  const target = record(input.target)
  exactKeys(target, ['platform', 'architecture', 'minimum_macos_major', 'minimum_free_gib'])
  exactString(target.platform, 'darwin')
  exactString(target.architecture, 'arm64')

  const ownership = record(input.ownership)
  exactKeys(ownership, ['profile', 'context'])
  exactString(ownership.profile, 'ancestryllm-local-arm64')
  exactString(ownership.context, 'colima-ancestryllm-local-arm64')

  const resources = record(input.resources)
  exactKeys(resources, [
    'minimum_cpus',
    'maximum_cpus',
    'minimum_memory_gib',
    'maximum_memory_gib',
    'disk_gib',
  ])
  const minimumCpus = integer(resources.minimum_cpus, 1, 32)
  const maximumCpus = integer(resources.maximum_cpus, minimumCpus, 32)
  const minimumMemoryGib = integer(resources.minimum_memory_gib, 2, 128)
  const maximumMemoryGib = integer(resources.maximum_memory_gib, minimumMemoryGib, 128)
  const diskGib = integer(resources.disk_gib, 10, 1024)

  const componentValues = input.components
  if (!Array.isArray(componentValues) || componentValues.length !== COMPONENT_NAMES.length) fail()
  const components = COMPONENT_NAMES.map((name, index) => parseComponent(componentValues[index], name))

  return {
    schemaVersion: 1,
    target: {
      platform: 'darwin',
      architecture: 'arm64',
      minimumMacosMajor: integer(target.minimum_macos_major, 13, 99),
      minimumFreeGib: integer(target.minimum_free_gib, diskGib, 4096),
    },
    ownership: {
      profile: 'ancestryllm-local-arm64',
      context: 'colima-ancestryllm-local-arm64',
    },
    resources: {
      minimumCpus,
      maximumCpus,
      minimumMemoryGib,
      maximumMemoryGib,
      diskGib,
    },
    vmImage: parseVmImage(input.vm_image),
    components,
  }
}

/**
 * Computes the SHA-256 identity of the canonical reviewed runtime policy JSON.
 */
export function runtimePolicyDigest(policy: MacosArm64RuntimePolicy): string {
  return createHash('sha256').update(JSON.stringify(policy)).digest('hex')
}

/** Compares archive bytes with the reviewed SHA-256 digest in constant time. */
function checkedDigest(bytes: Buffer, expected: string): boolean {
  const actual = createHash('sha256').update(bytes).digest()
  return timingSafeEqual(actual, Buffer.from(expected, 'hex'))
}

function tarString(header: Buffer, offset: number, length: number): string {
  const raw = header.subarray(offset, offset + length)
  const end = raw.indexOf(0)
  return raw.subarray(0, end === -1 ? raw.length : end).toString('utf8')
}

function tarOctal(header: Buffer, offset: number, length: number): number {
  const raw = tarString(header, offset, length).trim()
  if (!/^[0-7]+$/.test(raw)) fail('RUNTIME_ARCHIVE_INVALID')
  const value = Number.parseInt(raw, 8)
  if (!Number.isSafeInteger(value) || value < 0 || value > MAX_ARCHIVE_BYTES) fail('RUNTIME_ARCHIVE_INVALID')
  return value
}

function validateTarChecksum(header: Buffer): void {
  const expected = tarOctal(header, 148, 8)
  const copy = Buffer.from(header)
  copy.fill(0x20, 148, 156)
  const actual = copy.reduce((sum, byte) => sum + byte, 0)
  if (actual !== expected) fail('RUNTIME_ARCHIVE_INVALID')
}

interface TarMember {
  readonly name: string
  readonly type: string
  readonly linkTarget: string
  readonly bytes: Buffer
}

function tarMemberPath(value: string): string {
  return safeRelativePath(value.startsWith('./') ? value.slice(2) : value)
}

/** Parses a bounded gzip tar while rejecting malformed headers and unsafe member types. */
function parseTar(archive: Buffer): readonly TarMember[] {
  let tar: Buffer
  try {
    tar = gunzipSync(archive, { maxOutputLength: MAX_ARCHIVE_BYTES })
  } catch {
    fail('RUNTIME_ARCHIVE_INVALID')
  }
  const members: TarMember[] = []
  let offset = 0
  let zeroBlocks = 0
  while (offset + 512 <= tar.length) {
    const header = tar.subarray(offset, offset + 512)
    offset += 512
    if (header.every((byte) => byte === 0)) {
      zeroBlocks += 1
      if (zeroBlocks === 2) break
      continue
    }
    if (zeroBlocks !== 0) fail('RUNTIME_ARCHIVE_INVALID')
    validateTarChecksum(header)
    const baseName = tarString(header, 0, 100)
    const prefix = tarString(header, 345, 155)
    const archiveName = prefix === '' ? baseName : `${prefix}/${baseName}`
    const size = tarOctal(header, 124, 12)
    const typeByte = header[156]
    if (typeByte === undefined) fail('RUNTIME_ARCHIVE_INVALID')
    const type = typeByte === 0 ? '0' : String.fromCharCode(typeByte)
    if (type !== '0' && type !== '2' && type !== '5') fail('RUNTIME_ARCHIVE_UNSAFE_MEMBER')
    if ((type === '2' || type === '5') && size !== 0) fail('RUNTIME_ARCHIVE_INVALID')
    const linkTarget = tarString(header, 157, 100)
    if (type !== '2' && linkTarget !== '') fail('RUNTIME_ARCHIVE_INVALID')
    if (offset + size > tar.length) fail('RUNTIME_ARCHIVE_INVALID')
    const bytes = Buffer.from(tar.subarray(offset, offset + size))
    offset += Math.ceil(size / 512) * 512
    if (archiveName === '.' || archiveName === './') {
      if (type !== '5') fail('RUNTIME_ARCHIVE_UNSAFE_MEMBER')
      continue
    }
    members.push({ name: tarMemberPath(archiveName), type, linkTarget, bytes })
  }
  if (zeroBlocks !== 2 || offset > tar.length) fail('RUNTIME_ARCHIVE_INVALID')
  if (tar.subarray(offset).some((byte) => byte !== 0)) fail('RUNTIME_ARCHIVE_INVALID')
  return members
}

/** Resolves an extraction root only when it is an existing non-symlink directory. */
async function verifiedOutputRoot(root: string): Promise<string> {
  const resolved = resolve(root)
  const status = await lstat(resolved).catch(() => undefined)
  if (!status?.isDirectory() || status.isSymbolicLink()) fail('RUNTIME_ARCHIVE_OUTPUT_UNSAFE')
  return resolved
}

/** Creates extraction parents while rejecting any symlink introduced beneath the root. */
async function ensureParents(root: string, destination: string): Promise<void> {
  const relative = destination.slice(root.length + 1)
  const segments = relative.split(sep).slice(0, -1)
  let current = root
  for (const segment of segments) {
    current = join(current, segment)
    await mkdir(current, { mode: 0o700 }).catch((error: unknown) => {
      const code = (error as NodeJS.ErrnoException).code
      if (code !== 'EEXIST') throw error
    })
    const status = await lstat(current)
    if (!status.isDirectory() || status.isSymbolicLink()) fail('RUNTIME_ARCHIVE_OUTPUT_UNSAFE')
  }
}

/**
 * Extracts only policy-allowlisted regular files and rejects missing, duplicate, linked, or unexpected members.
 */
export async function extractReviewedTarGzip(
  archive: Buffer,
  install: readonly RuntimeInstallEntry[],
  excludedMembers: readonly RuntimeExcludedArchiveMember[],
  outputRoot: string,
): Promise<void> {
  const members = parseTar(archive)
  const regular = new Map<string, Buffer>()
  const remainingExclusions = new Set(excludedMembers.map(({ sourcePath, type, linkTarget }) => (
    `${sourcePath}\0${type}\0${linkTarget}`
  )))
  for (const member of members) {
    if (member.type === '0') {
      if (regular.has(member.name)) fail('RUNTIME_ARCHIVE_INVALID')
      regular.set(member.name, member.bytes)
    } else if (member.type === '2') {
      const identity = `${member.name}\0symlink\0${member.linkTarget}`
      if (!remainingExclusions.delete(identity)) fail('RUNTIME_ARCHIVE_UNSAFE_MEMBER')
    }
  }
  if (remainingExclusions.size !== 0) fail('RUNTIME_ARCHIVE_INVALID')

  const selected = install.map((entry) => {
    const bytes = regular.get(entry.sourcePath)
    if (
      bytes === undefined
      || bytes.length !== entry.sizeBytes
      || !checkedDigest(bytes, entry.sha256)
    ) fail('RUNTIME_ARCHIVE_MEMBER_INTEGRITY')
    return { entry, bytes }
  })

  const root = await verifiedOutputRoot(outputRoot)
  for (const { entry, bytes } of selected) {
    const destination = resolve(root, ...entry.installPath.split('/'))
    if (!destination.startsWith(`${root}${sep}`)) fail('RUNTIME_ARCHIVE_OUTPUT_UNSAFE')
    await ensureParents(root, destination)
    let handle
    try {
      handle = await open(destination, 'wx', entry.executable ? 0o700 : 0o600)
      await handle.writeFile(bytes)
      await handle.sync()
    } catch {
      fail('RUNTIME_ARCHIVE_OUTPUT_UNSAFE')
    } finally {
      await handle?.close()
    }
    await chmod(destination, entry.executable ? 0o700 : 0o600)
  }
}
