/** Verifies the closed runtime policy schema, archive safety, and executable allowlists. */

import { createHash } from 'node:crypto'
import { gzipSync } from 'node:zlib'
import { mkdir, mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  RuntimePolicyError,
  extractReviewedTarGzip,
  parseMacosArm64RuntimePolicy,
} from './macos-arm64-runtime-policy'

const temporaryRoots: string[] = []
const digest = (value: Buffer | string): string =>
  createHash('sha256').update(value).digest('hex')
const digest512 = (value: Buffer | string): string =>
  createHash('sha512').update(value).digest('hex')

function component(
  name: string,
  repository: string,
  assetName: string,
  bytes: Buffer,
): Record<string, unknown> {
  const version = '1.2.3'
  const license = Buffer.from(`${name} license`)
  return {
    name,
    version,
    repository,
    license: {
      spdx_id: 'MIT',
      url: `https://raw.githubusercontent.com/${repository}/v${version}/LICENSE`,
      sha256: digest(license),
      size_bytes: license.length,
    },
    artifact: {
      asset_name: assetName,
      url: `https://github.com/${repository}/releases/download/v${version}/${assetName}`,
      sha256: digest(bytes),
      size_bytes: bytes.length,
      archive_format: 'binary',
      excluded_members: [],
      install: [{
        source_path: assetName,
        install_path: `bin/${name}`,
        sha256: digest(bytes),
        size_bytes: bytes.length,
        executable: true,
      }],
    },
  }
}

function policyInput(): Record<string, unknown> {
  const image = Buffer.from('reviewed native arm64 virtual machine image')
  return {
    schema_version: 1,
    target: {
      platform: 'darwin',
      architecture: 'arm64',
      minimum_macos_major: 13,
      minimum_free_gib: 24,
    },
    ownership: {
      profile: 'ancestryllm-local-arm64',
      context: 'colima-ancestryllm-local-arm64',
    },
    resources: {
      minimum_cpus: 2,
      maximum_cpus: 4,
      minimum_memory_gib: 4,
      maximum_memory_gib: 8,
      disk_gib: 20,
    },
    vm_image: {
      version: '1.2.3',
      repository: 'abiosoft/colima-core',
      asset_name: 'ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz',
      url: 'https://github.com/abiosoft/colima-core/releases/download/v1.2.3/ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz',
      sha256: digest(image),
      sha512: digest512(image),
      size_bytes: image.length,
    },
    components: [
      component('colima', 'abiosoft/colima', 'colima-Darwin-arm64', Buffer.from('colima')),
      component('lima', 'lima-vm/lima', 'lima-Darwin-arm64', Buffer.from('lima')),
      component('docker-cli', 'docker/cli', 'docker-Darwin-arm64', Buffer.from('docker')),
      component('docker-buildx', 'docker/buildx', 'buildx-Darwin-arm64', Buffer.from('buildx')),
      component('docker-compose', 'docker/compose', 'compose-Darwin-arm64', Buffer.from('compose')),
    ],
  }
}

function tarHeader(name: string, size: number, type = '0', linkTarget = ''): Buffer {
  const header = Buffer.alloc(512)
  header.write(name, 0, 100, 'utf8')
  header.write('0000755\0', 100, 8, 'ascii')
  header.write('0000000\0', 108, 8, 'ascii')
  header.write('0000000\0', 116, 8, 'ascii')
  header.write(`${size.toString(8).padStart(11, '0')}\0`, 124, 12, 'ascii')
  header.write('00000000000\0', 136, 12, 'ascii')
  header.fill(0x20, 148, 156)
  header.write(type, 156, 1, 'ascii')
  header.write(linkTarget, 157, 100, 'utf8')
  header.write('ustar\0', 257, 6, 'ascii')
  header.write('00', 263, 2, 'ascii')
  const checksum = header.reduce((sum, byte) => sum + byte, 0)
  header.write(`${checksum.toString(8).padStart(6, '0')}\0 `, 148, 8, 'ascii')
  return header
}

function tarGzip(entries: readonly {
  name: string
  body: Buffer
  type?: string
  linkTarget?: string
}[]): Buffer {
  const chunks: Buffer[] = []
  for (const entry of entries) {
    chunks.push(tarHeader(entry.name, entry.body.length, entry.type, entry.linkTarget))
    chunks.push(entry.body)
    const padding = (512 - (entry.body.length % 512)) % 512
    if (padding > 0) chunks.push(Buffer.alloc(padding))
  }
  chunks.push(Buffer.alloc(1024))
  return gzipSync(Buffer.concat(chunks))
}

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, {
    recursive: true,
    force: true,
  })))
})

describe('macOS arm64 runtime policy', () => {
  it('accepts only the exact five-component app-owned policy contract', () => {
    const policy = parseMacosArm64RuntimePolicy(policyInput())

    expect(policy.schemaVersion).toBe(1)
    expect(policy.target).toEqual({
      platform: 'darwin',
      architecture: 'arm64',
      minimumMacosMajor: 13,
      minimumFreeGib: 24,
    })
    expect(policy.ownership).toEqual({
      profile: 'ancestryllm-local-arm64',
      context: 'colima-ancestryllm-local-arm64',
    })
    expect(policy.vmImage).toMatchObject({
      repository: 'abiosoft/colima-core',
      assetName: 'ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz',
      sha512: digest512('reviewed native arm64 virtual machine image'),
    })
    expect(policy.components.map(({ name }) => name)).toEqual([
      'colima',
      'lima',
      'docker-cli',
      'docker-buildx',
      'docker-compose',
    ])
  })

  it.each([
    ['unknown schema', (policy: Record<string, unknown>) => { policy.schema_version = 2 }],
    ['unknown field', (policy: Record<string, unknown>) => { policy.latest = true }],
    ['wrong architecture', (policy: Record<string, unknown>) => {
      ;(policy.target as Record<string, unknown>).architecture = 'x64'
    }],
    ['alternate URL', (policy: Record<string, unknown>) => {
      const first = (policy.components as Record<string, unknown>[])[0]!
      ;(first.artifact as Record<string, unknown>).url = 'https://mirror.invalid/colima'
    }],
    ['unreviewed license', (policy: Record<string, unknown>) => {
      const first = (policy.components as Record<string, unknown>[])[0]!
      ;(first.license as Record<string, unknown>).spdx_id = 'LicenseRef-unknown'
    }],
    ['wrong VM image architecture', (policy: Record<string, unknown>) => {
      ;(policy.vm_image as Record<string, unknown>).asset_name = 'ubuntu-x86_64.raw.gz'
    }],
    ['missing VM SHA-512', (policy: Record<string, unknown>) => {
      delete (policy.vm_image as Record<string, unknown>).sha512
    }],
  ])('rejects %s', (_label, mutate) => {
    const policy = policyInput()
    mutate(policy)
    expect(() => parseMacosArm64RuntimePolicy(policy)).toThrow(RuntimePolicyError)
  })

  it('locks the shipped Lima payload allowlist to the reviewed release bytes', async () => {
    const source = JSON.parse(await readFile(
      resolve(process.cwd(), 'resources/macos-arm64-runtime-policy-v1.json'),
      'utf8',
    )) as unknown
    const policy = parseMacosArm64RuntimePolicy(source)
    const lima = policy.components.find(({ name }) => name === 'lima')

    expect(lima?.artifact.install).toEqual([
      {
        sourcePath: 'bin/limactl',
        installPath: 'bin/limactl',
        sha256: 'f19a4fca3875e1017a5285672be4a62699c1e55918fb6a7afce86a14199e10d9',
        sizeBytes: 32_669_616,
        executable: true,
      },
      {
        sourcePath: 'share/lima/lima-guestagent.Linux-aarch64.gz',
        installPath: 'share/lima/lima-guestagent.Linux-aarch64.gz',
        sha256: 'd3fda5670ef5fcf14094efec95d410021cd4c585a2a1b6a16a97131f73fbe2f1',
        sizeBytes: 7_275_764,
        executable: false,
      },
    ])
  })
})

describe('reviewed runtime archives', () => {
  it.each(['.', './'])('accepts the harmless %j root directory entry emitted by tar', async (archiveName) => {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-runtime-archive-'))
    temporaryRoots.push(root)
    const output = join(root, 'output')
    await mkdir(output)
    const payload = Buffer.from('verified limactl')

    await extractReviewedTarGzip(
      tarGzip([
        { name: archiveName, body: Buffer.alloc(0), type: '5' },
        { name: './bin/limactl', body: payload },
      ]),
      [{
        sourcePath: 'bin/limactl',
        installPath: 'bin/limactl',
        sha256: digest(payload),
        sizeBytes: payload.length,
        executable: true,
      }],
      [],
      output,
    )

    expect(await readFile(join(output, 'bin/limactl'))).toEqual(payload)
  })

  it('rejects a root archive entry unless it is an empty directory', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-runtime-archive-'))
    temporaryRoots.push(root)
    const output = join(root, 'output')
    await mkdir(output)

    await expect(extractReviewedTarGzip(
      tarGzip([{ name: '.', body: Buffer.from('unsafe'), type: '0' }]),
      [{
        sourcePath: 'bin/limactl',
        installPath: 'bin/limactl',
        sha256: digest('unsafe'),
        sizeBytes: 6,
        executable: true,
      }],
      [],
      output,
    )).rejects.toMatchObject({ code: 'RUNTIME_ARCHIVE_UNSAFE_MEMBER' })
  })

  it('extracts only reviewed regular files with matching payload hashes', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-runtime-archive-'))
    temporaryRoots.push(root)
    const output = join(root, 'output')
    await mkdir(output)
    const payload = Buffer.from('verified limactl')

    await extractReviewedTarGzip(
      tarGzip([{ name: 'bin/limactl', body: payload }]),
      [{
        sourcePath: 'bin/limactl',
        installPath: 'bin/limactl',
        sha256: digest(payload),
        sizeBytes: payload.length,
        executable: true,
      }],
      [],
      output,
    )

    expect(await readFile(join(output, 'bin/limactl'))).toEqual(payload)
  })

  it.each([
    ['parent traversal', '../outside', '0'],
    ['absolute path', '/tmp/outside', '0'],
    ['symbolic link', 'bin/limactl', '2'],
    ['hard link', 'bin/limactl', '1'],
    ['device', 'bin/limactl', '3'],
  ])('rejects %s members without writing outside the root', async (_label, name, type) => {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-runtime-archive-'))
    temporaryRoots.push(root)
    const output = join(root, 'output')
    await mkdir(output)
    const payload = Buffer.from('unsafe')

    await expect(extractReviewedTarGzip(
      tarGzip([{ name, body: payload, type }]),
      [{
        sourcePath: 'bin/limactl',
        installPath: 'bin/limactl',
        sha256: digest(payload),
        sizeBytes: payload.length,
        executable: true,
      }],
      [],
      output,
    )).rejects.toBeInstanceOf(RuntimePolicyError)
  })

  it('skips only the exact reviewed Lima archive symlink without materializing it', async () => {
    const root = await mkdtemp(join(tmpdir(), 'ancestryllm-runtime-archive-'))
    temporaryRoots.push(root)
    const output = join(root, 'output')
    await mkdir(output)
    const payload = Buffer.from('verified limactl')

    await extractReviewedTarGzip(
      tarGzip([
        { name: './bin/limactl', body: payload },
        {
          name: './share/doc/lima/templates',
          body: Buffer.alloc(0),
          type: '2',
          linkTarget: '../../lima/templates',
        },
      ]),
      [{
        sourcePath: 'bin/limactl',
        installPath: 'bin/limactl',
        sha256: digest(payload),
        sizeBytes: payload.length,
        executable: true,
      }],
      [{
        sourcePath: 'share/doc/lima/templates',
        type: 'symlink',
        linkTarget: '../../lima/templates',
      }],
      output,
    )

    expect(await readFile(join(output, 'bin/limactl'))).toEqual(payload)
    await expect(readFile(join(output, 'share/doc/lima/templates'))).rejects.toMatchObject({
      code: 'ENOENT',
    })
  })
})
