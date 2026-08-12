/** Exercises macOS ARM64 runtime acquisition, recovery, lifecycle, and storage boundaries. */

import { createHash } from 'node:crypto'
import { appendFile, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  MacosArm64RuntimeManager,
  MacosRuntimeError,
  type DownloadRuntimeFile,
  type MacosRuntimeHost,
} from './macos-arm64-runtime-manager'
import { parseMacosArm64RuntimePolicy } from './macos-arm64-runtime-policy'
import type { RunHostProcess } from './container-process'

const temporaryRoots: string[] = []
const digest = (value: Buffer | string): string =>
  createHash('sha256').update(value).digest('hex')
const digest512 = (value: Buffer | string): string =>
  createHash('sha512').update(value).digest('hex')
const artifacts = new Map<string, Buffer>()

function component(name: string, repository: string): Record<string, unknown> {
  const version = '1.2.3'
  const assetName = `${name}-Darwin-arm64`
  const asset = Buffer.from(`${name} artifact`)
  const license = Buffer.from(`${name} license`)
  const artifactUrl = `https://github.com/${repository}/releases/download/v${version}/${assetName}`
  const licenseUrl = `https://raw.githubusercontent.com/${repository}/v${version}/LICENSE`
  artifacts.set(artifactUrl, asset)
  artifacts.set(licenseUrl, license)
  return {
    name,
    version,
    repository,
    license: {
      spdx_id: 'MIT',
      url: licenseUrl,
      sha256: digest(license),
      size_bytes: license.length,
    },
    artifact: {
      asset_name: assetName,
      url: artifactUrl,
      sha256: digest(asset),
      size_bytes: asset.length,
      archive_format: 'binary',
      excluded_members: [],
      install: [{
        source_path: assetName,
        install_path: name === 'docker-buildx'
          ? 'docker-config/cli-plugins/docker-buildx'
          : `bin/${name === 'docker-cli' ? 'docker' : name}`,
        sha256: digest(asset),
        size_bytes: asset.length,
        executable: true,
      }],
    },
  }
}

function policy() {
  artifacts.clear()
  const vmImage = Buffer.from('reviewed native arm64 virtual machine image')
  const vmImageUrl = 'https://github.com/abiosoft/colima-core/releases/download/v1.2.3/ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz'
  artifacts.set(vmImageUrl, vmImage)
  return parseMacosArm64RuntimePolicy({
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
      url: vmImageUrl,
      sha256: digest(vmImage),
      sha512: digest512(vmImage),
      size_bytes: vmImage.length,
    },
    components: [
      component('colima', 'abiosoft/colima'),
      component('lima', 'lima-vm/lima'),
      component('docker-cli', 'docker/cli'),
      component('docker-buildx', 'docker/buildx'),
      component('docker-compose', 'docker/compose'),
    ],
  })
}

function host(overrides: Partial<MacosRuntimeHost> = {}): MacosRuntimeHost {
  return {
    platform: 'darwin',
    architecture: 'arm64',
    inspect: vi.fn(async () => ({
      macosMajor: 15,
      virtualizationAvailable: true,
      logicalCpus: 10,
      totalMemoryBytes: 32 * 1024 ** 3,
      freeBytes: 80 * 1024 ** 3,
      existingDockerContexts: 3,
    })),
    ...overrides,
  }
}

function downloader(): DownloadRuntimeFile {
  return vi.fn(async ({ sourceUrl, targetPath, offsetBytes }) => {
    const bytes = artifacts.get(sourceUrl)
    if (!bytes) throw new Error('Unexpected fixture URL')
    if (offsetBytes === 0) await writeFile(targetPath, bytes)
    else await appendFile(targetPath, bytes.subarray(offsetBytes))
  })
}

function runner(overrides: {
  dockerEndpoint?: string
  engineId?: string
} = {}): RunHostProcess {
  return vi.fn(async ({ arguments: args, environment }) => {
    if (args[0] === 'status') {
      return { stdout: '{"status":"Running","arch":"aarch64","runtime":"docker"}' }
    }
    if (args[0] === '--config' && args.includes('context')) {
      const endpoint = overrides.dockerEndpoint
        ?? `unix://${join(environment.COLIMA_HOME ?? '', 'ancestryllm-local-arm64', 'docker.sock')}`
      return {
        stdout: JSON.stringify({
          Name: 'colima-ancestryllm-local-arm64',
          Endpoints: { docker: { Host: endpoint } },
        }),
      }
    }
    if (args[0] === '--config' && args.includes('info')) {
      return {
        stdout: JSON.stringify({
          ID: overrides.engineId ?? '4cee4408-1234-4567-89ab-cdef01234567',
          OSType: 'linux',
          Architecture: 'aarch64',
        }),
      }
    }
    return { stdout: '' }
  })
}

async function managerFixture(overrides: {
  runtimeHost?: MacosRuntimeHost
  download?: DownloadRuntimeFile
  runProcess?: RunHostProcess
} = {}) {
  const parent = await mkdtemp(join(tmpdir(), 'ancestryllm-macos-runtime-'))
  temporaryRoots.push(parent)
  const rootDirectory = join(parent, 'macos-arm64-runtime')
  const runtimePolicy = policy()
  const runtimeHost = overrides.runtimeHost ?? host()
  const runProcess = overrides.runProcess ?? runner()
  const download = overrides.download ?? downloader()
  const now = vi.fn(() => new Date('2026-08-12T12:00:00.000Z'))
  return {
    rootDirectory,
    runtimePolicy,
    runtimeHost,
    runProcess,
    download,
    now,
    manager: new MacosArm64RuntimeManager({
      rootDirectory,
      policy: runtimePolicy,
      host: runtimeHost,
      download,
      runProcess,
      now,
    }),
  }
}

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, {
    recursive: true,
    force: true,
  })))
})

describe('macOS arm64 runtime manager', () => {
  it('fails closed before network or process access on unsupported hosts', async () => {
    const download = downloader()
    const runProcess = runner()
    const runtimeHost = host({ architecture: 'x64' })
    const { manager } = await managerFixture({
      runtimeHost,
      download,
      runProcess,
    })

    await expect(manager.preview({ schema_version: 1, operation: 'setup', offline: false }))
      .rejects.toEqual(new MacosRuntimeError('RUNTIME_HOST_UNSUPPORTED'))
    expect(download).not.toHaveBeenCalled()
    expect(runProcess).not.toHaveBeenCalled()
    expect(runtimeHost.inspect).not.toHaveBeenCalled()
  })

  it('reserves the reviewed installation footprint before setup starts', async () => {
    const download = downloader()
    const runProcess = runner()
    const runtimeHost = host({
      inspect: vi.fn(async () => ({
        macosMajor: 15,
        virtualizationAvailable: true,
        logicalCpus: 10,
        totalMemoryBytes: 32 * 1024 ** 3,
        freeBytes: 24 * 1024 ** 3 + 1,
        existingDockerContexts: 0,
      })),
    })
    const { manager } = await managerFixture({ runtimeHost, download, runProcess })

    await expect(manager.preview({ schema_version: 1, operation: 'setup', offline: false }))
      .rejects.toEqual(new MacosRuntimeError('RUNTIME_HOST_UNSUPPORTED'))
    expect(download).not.toHaveBeenCalled()
    expect(runProcess).not.toHaveBeenCalled()
  })

  it('returns a sanitized measured plan without mutating existing Docker contexts', async () => {
    const { manager, rootDirectory } = await managerFixture()

    const status = await manager.status()
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    expect(status).toMatchObject({
      schema_version: 1,
      state: 'not-installed',
      supported: true,
      host: {
        operating_system: 'macos',
        architecture: 'arm64',
        virtualization: 'available',
        free_space: 'sufficient',
        existing_docker_contexts: 3,
      },
      allocation: { cpus: 4, memory_gib: 8, disk_gib: 20 },
    })
    expect(preview.actions.map(({ code }) => code)).toEqual([
      'VERIFY_HOST',
      'DOWNLOAD_PINNED_COMPONENTS',
      'CREATE_APP_PROFILE',
      'START_RUNTIME',
      'VERIFY_RUNTIME',
    ])
    expect(preview).toMatchObject({
      operation: 'setup',
      confirmation_phrase: 'SET UP LOCAL RUNTIME',
      preserves_data: true,
      deletes_data: false,
    })
    expect(JSON.stringify({ status, preview })).not.toContain(rootDirectory)
  })

  it('requires the exact current preview revision and confirmation', async () => {
    const { manager } = await managerFixture()
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: '0'.repeat(64),
      confirmation: preview.confirmation_phrase,
    })).rejects.toEqual(new MacosRuntimeError('RUNTIME_PLAN_STALE'))
    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.plan_revision,
      confirmation: 'yes',
    })).rejects.toEqual(new MacosRuntimeError('RUNTIME_CONFIRMATION_REQUIRED'))
  })

  it('never executes a component whose reviewed digest does not match', async () => {
    const runProcess = runner()
    const download: DownloadRuntimeFile = vi.fn(async ({ targetPath }) => {
      await writeFile(targetPath, 'corrupted')
    })
    const { manager, rootDirectory } = await managerFixture({ download, runProcess })
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.plan_revision,
      confirmation: preview.confirmation_phrase,
    })).rejects.toEqual(new MacosRuntimeError('RUNTIME_ARTIFACT_INTEGRITY'))
    expect(runProcess).not.toHaveBeenCalled()
    const first = policy().components[0]!
    await expect(readFile(join(rootDirectory, 'downloads', `${first.name}.artifact.part`)))
      .rejects.toThrow()
  })

  it('replaces a complete corrupt component partial with a fresh verified download', async () => {
    const { manager, rootDirectory, download } = await managerFixture()
    const first = policy().components[0]!
    const partialPath = join(rootDirectory, 'downloads', `${first.name}.artifact.part`)
    await mkdir(join(partialPath, '..'), { recursive: true })
    await writeFile(partialPath, Buffer.alloc(first.artifact.sizeBytes, 0x78))
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.plan_revision,
      confirmation: preview.confirmation_phrase,
    })).resolves.toMatchObject({ code: 'RUNTIME_READY' })
    expect(download).toHaveBeenCalledWith(expect.objectContaining({
      sourceUrl: first.artifact.url,
      offsetBytes: 0,
    }))
  })

  it('replaces a complete corrupt VM image partial with a fresh verified download', async () => {
    const { manager, rootDirectory, download } = await managerFixture()
    const image = policy().vmImage
    const partialPath = join(rootDirectory, 'downloads', 'vm-image.artifact.part')
    await mkdir(join(partialPath, '..'), { recursive: true })
    await writeFile(partialPath, Buffer.alloc(image.sizeBytes, 0x78))
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.plan_revision,
      confirmation: preview.confirmation_phrase,
    })).resolves.toMatchObject({ code: 'RUNTIME_READY' })
    expect(download).toHaveBeenCalledWith(expect.objectContaining({
      sourceUrl: image.url,
      offsetBytes: 0,
    }))
  })

  it.runIf(process.platform !== 'win32')('rejects a symlinked partial download before invoking the downloader', async () => {
    const { manager, rootDirectory, download } = await managerFixture()
    const first = policy().components[0]!
    const partialPath = join(rootDirectory, 'downloads', `${first.name}.artifact.part`)
    const outsidePath = join(rootDirectory, '..', 'outside.txt')
    await mkdir(join(partialPath, '..'), { recursive: true })
    await writeFile(outsidePath, 'must remain unchanged')
    await symlink(outsidePath, partialPath)
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.plan_revision,
      confirmation: preview.confirmation_phrase,
    })).rejects.toEqual(new MacosRuntimeError('RUNTIME_STORAGE_UNSAFE'))
    await expect(readFile(outsidePath, 'utf8')).resolves.toBe('must remain unchanged')
    expect(download).not.toHaveBeenCalled()
  })

  it('resumes verified downloads and starts only the app-owned native arm64 profile', async () => {
    const { manager, rootDirectory, download, runProcess, now } = await managerFixture()
    const first = policy().components[0]!
    const partialPath = join(rootDirectory, 'downloads', `${first.name}.artifact.part`)
    await mkdir(join(partialPath, '..'), { recursive: true })
    await writeFile(partialPath, artifacts.get(first.artifact.url)!.subarray(0, 2))
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    const result = await manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.plan_revision,
      confirmation: preview.confirmation_phrase,
    })

    expect(download).toHaveBeenCalledWith(expect.objectContaining({
      sourceUrl: first.artifact.url,
      offsetBytes: 2,
    }))
    expect(runProcess).toHaveBeenCalledWith(expect.objectContaining({
      arguments: expect.arrayContaining([
        'start',
        '--profile',
        'ancestryllm-local-arm64',
        '--arch',
        'aarch64',
        '--vm-type',
        'vz',
        '--kubernetes=false',
        '--network-address=false',
        '--network-host-addresses=false',
        '--activate=false',
        '--ssh-config=false',
        '--binfmt=false',
        '--vz-rosetta=false',
        '--mount',
        'none',
        '--cpus',
        '4',
        '--disk-image',
        join(rootDirectory, 'downloads', 'vm-image.artifact'),
      ]),
    }))
    expect(download).toHaveBeenCalledWith(expect.objectContaining({
      sourceUrl: policy().vmImage.url,
      expectedSizeBytes: policy().vmImage.sizeBytes,
    }))
    expect(runProcess).not.toHaveBeenCalledWith(expect.objectContaining({
      arguments: expect.arrayContaining(['default']),
    }))
    expect(runProcess).not.toHaveBeenCalledWith(expect.objectContaining({
      arguments: expect.arrayContaining(['--activate=true']),
    }))
    expect(now).toHaveBeenCalledTimes(2)
    expect(result).toMatchObject({ state: 'ready', code: 'RUNTIME_READY' })
  })

  it('preserves an interrupted partial download and resumes it on retry', async () => {
    const controller = new AbortController()
    let interrupted = false
    const download: DownloadRuntimeFile = vi.fn(async ({
      sourceUrl,
      targetPath,
      offsetBytes,
      signal,
    }) => {
      const bytes = artifacts.get(sourceUrl)
      if (!bytes) throw new Error('Unexpected fixture URL')
      if (!interrupted) {
        interrupted = true
        await writeFile(targetPath, bytes.subarray(0, 2))
        controller.abort(new Error('caller cancelled'))
        throw controller.signal.reason
      }
      if (offsetBytes === 0) await writeFile(targetPath, bytes)
      else await appendFile(targetPath, bytes.subarray(offsetBytes))
      expect(signal?.aborted).toBe(false)
    })
    const { manager } = await managerFixture({ download })
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.plan_revision,
      confirmation: preview.confirmation_phrase,
    }, controller.signal)).rejects.toThrow('caller cancelled')

    const retryPreview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })
    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: retryPreview.plan_revision,
      confirmation: retryPreview.confirmation_phrase,
    }, new AbortController().signal)).resolves.toMatchObject({ code: 'RUNTIME_READY' })
    expect(download).toHaveBeenCalledWith(expect.objectContaining({ offsetBytes: 2 }))
  })

  it('reports a corrupt installed VM image as component-integrity failure', async () => {
    const { manager, rootDirectory } = await managerFixture()
    const setup = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })
    await manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: setup.plan_revision,
      confirmation: setup.confirmation_phrase,
    })
    const image = policy().vmImage
    await writeFile(
      join(rootDirectory, 'downloads', 'vm-image.artifact'),
      Buffer.alloc(image.sizeBytes, 0x78),
    )

    await expect(manager.status()).resolves.toMatchObject({
      state: 'unhealthy',
      code: 'RUNTIME_COMPONENT_INTEGRITY',
      vm_image: { installed: false },
    })
  })

  it('rejects a Docker context that points outside the app-owned runtime', async () => {
    const { manager } = await managerFixture({
      runProcess: runner({ dockerEndpoint: 'unix:///tmp/foreign/docker.sock' }),
    })
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.plan_revision,
      confirmation: preview.confirmation_phrase,
    })).rejects.toEqual(new MacosRuntimeError('RUNTIME_HEALTH_FAILED'))
  })

  it('binds the app-owned endpoint to one Docker Engine identity', async () => {
    const {
      manager,
      rootDirectory,
      runtimePolicy,
      runtimeHost,
      download,
    } = await managerFixture()
    const setup = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })
    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: setup.plan_revision,
      confirmation: setup.confirmation_phrase,
    })).resolves.toMatchObject({ code: 'RUNTIME_READY' })
    await expect(readFile(join(rootDirectory, 'engine-identity.json'), 'utf8'))
      .resolves.toContain('4cee4408-1234-4567-89ab-cdef01234567')

    const changedManager = new MacosArm64RuntimeManager({
      rootDirectory,
      policy: runtimePolicy,
      host: runtimeHost,
      download,
      runProcess: runner({ engineId: '8d7de275-aaaa-bbbb-cccc-0123456789ab' }),
    })
    await expect(changedManager.status()).resolves.toMatchObject({
      state: 'stopped',
      code: 'RUNTIME_STOPPED',
    })
    const start = await changedManager.preview({
      schema_version: 1,
      operation: 'start',
      offline: true,
    })
    await expect(changedManager.apply({
      schema_version: 1,
      operation: 'start',
      offline: true,
      plan_revision: start.plan_revision,
      confirmation: start.confirmation_phrase,
    })).rejects.toEqual(new MacosRuntimeError('RUNTIME_HEALTH_FAILED'))
  })

  it('stops when an unrelated installed artifact and VM image are corrupt', async () => {
    const { manager, rootDirectory, runProcess } = await managerFixture()
    const setup = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })
    await manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: setup.plan_revision,
      confirmation: setup.confirmation_phrase,
    })
    await writeFile(join(rootDirectory, 'tools', 'bin', 'docker'), 'corrupt')
    await writeFile(join(rootDirectory, 'downloads', 'vm-image.artifact'), 'corrupt')
    const stop = await manager.preview({ schema_version: 1, operation: 'stop', offline: true })
    vi.mocked(runProcess).mockClear()

    await expect(manager.apply({
      schema_version: 1,
      operation: 'stop',
      offline: true,
      plan_revision: stop.plan_revision,
      confirmation: stop.confirmation_phrase,
    })).resolves.toMatchObject({ state: 'stopped', code: 'RUNTIME_STOPPED' })
    expect(runProcess).toHaveBeenCalledWith(expect.objectContaining({
      arguments: ['stop', '--profile', 'ancestryllm-local-arm64'],
    }))
  })

  it('allows lifecycle cleanup after a policy digest changes', async () => {
    const { manager, rootDirectory, runProcess } = await managerFixture()
    const setup = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })
    await manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: setup.plan_revision,
      confirmation: setup.confirmation_phrase,
    })
    const markerPath = join(rootDirectory, 'ownership.json')
    const marker = JSON.parse(await readFile(markerPath, 'utf8')) as Record<string, unknown>
    await writeFile(markerPath, JSON.stringify({ ...marker, policy_sha256: '0'.repeat(64) }))
    const stop = await manager.preview({ schema_version: 1, operation: 'stop', offline: true })
    await expect(manager.apply({
      schema_version: 1,
      operation: 'stop',
      offline: true,
      plan_revision: stop.plan_revision,
      confirmation: stop.confirmation_phrase,
    })).resolves.toMatchObject({ code: 'RUNTIME_STOPPED' })

    const remove = await manager.preview({
      schema_version: 1,
      operation: 'uninstall-preserve',
      offline: true,
    })
    await expect(manager.apply({
      schema_version: 1,
      operation: 'uninstall-preserve',
      offline: true,
      plan_revision: remove.plan_revision,
      confirmation: remove.confirmation_phrase,
    })).resolves.toMatchObject({ code: 'RUNTIME_REMOVED' })
    expect(runProcess).toHaveBeenCalledWith(expect.objectContaining({
      arguments: ['delete', '--profile', 'ancestryllm-local-arm64', '--force'],
    }))
  })

  it('preserves ownership and VM state when verified Colima is unavailable for uninstall', async () => {
    const { manager, rootDirectory, runProcess } = await managerFixture()
    const setup = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })
    await manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: setup.plan_revision,
      confirmation: setup.confirmation_phrase,
    })
    const colimaPath = join(rootDirectory, 'tools', 'bin', 'colima')
    await writeFile(colimaPath, 'corrupt')
    const remove = await manager.preview({
      schema_version: 1,
      operation: 'uninstall-preserve',
      offline: true,
    })
    vi.mocked(runProcess).mockClear()

    await expect(manager.apply({
      schema_version: 1,
      operation: 'uninstall-preserve',
      offline: true,
      plan_revision: remove.plan_revision,
      confirmation: remove.confirmation_phrase,
    })).rejects.toEqual(new MacosRuntimeError('RUNTIME_COMPONENT_INTEGRITY'))
    await expect(readFile(join(rootDirectory, 'ownership.json'), 'utf8')).resolves.toContain(
      'ancestryllm-local-arm64',
    )
    await expect(readFile(colimaPath, 'utf8')).resolves.toBe('corrupt')
    expect(runProcess).not.toHaveBeenCalledWith(expect.objectContaining({
      arguments: ['delete', '--profile', 'ancestryllm-local-arm64', '--force'],
    }))
  })

  it('uninstalls the app-owned runtime while preserving app data and the offline cache', async () => {
    const { manager, rootDirectory, runProcess } = await managerFixture()
    const setup = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })
    await manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: setup.plan_revision,
      confirmation: setup.confirmation_phrase,
    })
    const dataPath = join(rootDirectory, 'data', 'preserved.txt')
    const cachePath = join(rootDirectory, 'downloads', 'colima.artifact')
    await writeFile(dataPath, 'preserved app data')
    const cacheBytes = await readFile(cachePath)
    const preserve = await manager.preview({
      schema_version: 1,
      operation: 'uninstall-preserve',
      offline: true,
    })

    const result = await manager.apply({
      schema_version: 1,
      operation: 'uninstall-preserve',
      offline: true,
      plan_revision: preserve.plan_revision,
      confirmation: preserve.confirmation_phrase,
    })

    expect(result).toMatchObject({ state: 'not-installed', code: 'RUNTIME_REMOVED' })
    await expect(readFile(dataPath, 'utf8')).resolves.toBe('preserved app data')
    await expect(readFile(cachePath)).resolves.toEqual(cacheBytes)
    await expect(readFile(join(rootDirectory, 'tools', 'bin', 'colima'))).rejects.toThrow()
    await expect(readFile(join(rootDirectory, 'ownership.json'))).rejects.toThrow()
    await expect(readFile(join(rootDirectory, 'engine-identity.json'))).rejects.toThrow()
    expect(runProcess).toHaveBeenCalledWith(expect.objectContaining({
      arguments: ['delete', '--profile', 'ancestryllm-local-arm64', '--force'],
    }))
  })

  it('makes cache and app data deletion a separate explicit operation', async () => {
    const { manager, rootDirectory } = await managerFixture()
    const setup = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })
    await manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: setup.plan_revision,
      confirmation: setup.confirmation_phrase,
    })
    const dataPath = join(rootDirectory, 'data', 'deleted.txt')
    const cachePath = join(rootDirectory, 'downloads', 'colima.artifact')
    await writeFile(dataPath, 'delete only after explicit confirmation')
    const remove = await manager.preview({
      schema_version: 1,
      operation: 'uninstall-delete',
      offline: true,
    })

    expect(remove).toMatchObject({
      preserves_data: false,
      deletes_data: true,
      confirmation_phrase: 'DELETE LOCAL RUNTIME DATA',
    })

    await manager.apply({
      schema_version: 1,
      operation: 'uninstall-delete',
      offline: true,
      plan_revision: remove.plan_revision,
      confirmation: remove.confirmation_phrase,
    })

    await expect(readFile(dataPath)).rejects.toThrow()
    await expect(readFile(cachePath)).rejects.toThrow()
    await expect(readFile(join(rootDirectory, 'ownership.json'))).rejects.toThrow()
  })
})
