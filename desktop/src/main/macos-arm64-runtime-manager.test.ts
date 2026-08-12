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

function runner(): RunHostProcess {
  return vi.fn(async ({ arguments: args }) => ({
    stdout: args[0] === 'status'
      ? '{"status":"Running","arch":"aarch64","runtime":"docker"}'
      : args[0] === '--config' && args.includes('info')
        ? '{"OSType":"linux","Architecture":"aarch64"}'
        : '',
  }))
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
  const runProcess = overrides.runProcess ?? runner()
  const download = overrides.download ?? downloader()
  const now = vi.fn(() => new Date('2026-08-12T12:00:00.000Z'))
  return {
    rootDirectory,
    runProcess,
    download,
    now,
    manager: new MacosArm64RuntimeManager({
      rootDirectory,
      policy: runtimePolicy,
      host: overrides.runtimeHost ?? host(),
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
    const { manager } = await managerFixture({ download, runProcess })
    const preview = await manager.preview({ schema_version: 1, operation: 'setup', offline: false })

    await expect(manager.apply({
      schema_version: 1,
      operation: 'setup',
      offline: false,
      plan_revision: preview.plan_revision,
      confirmation: preview.confirmation_phrase,
    })).rejects.toEqual(new MacosRuntimeError('RUNTIME_ARTIFACT_INTEGRITY'))
    expect(runProcess).not.toHaveBeenCalled()
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
