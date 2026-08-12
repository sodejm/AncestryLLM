// @vitest-environment node

import { lstat } from 'node:fs/promises'
import { describe, expect, it } from 'vitest'
import {
  DockerCliHostControl,
  minimalDockerEnvironment,
  runBoundedHostProcess,
  type HostProcessRequest,
} from './container-process'
import {
  HostContainerSupervisor,
  confirmationPhrase,
  parseHostComposePlan,
  parseHostContainerPolicy,
} from './container-supervisor'

const nativeEvidenceEnabled = process.env.ANCESTRYLLM_NATIVE_CONTAINER_EVIDENCE === '1'
const runtimeProfile = 'ancestryllm-363-evidence'
const dockerContext = `colima-${runtimeProfile}`
const projectName = 'ancestryllm-363-native'
const containerName = `${projectName}-witness`
const networkName = `${projectName}-private`
const volumeName = `${projectName}-data`

function requiredEnvironment(name: string): string {
  const value = process.env[name]
  if (value === undefined || value.length === 0 || value.includes('\0')) {
    throw new Error(`Missing required native-evidence input: ${name}`)
  }
  return value
}

function parseSecurityOptions(): readonly string[] {
  const value: unknown = JSON.parse(requiredEnvironment('ANCESTRYLLM_NATIVE_SECURITY_OPTIONS'))
  if (!Array.isArray(value) || value.some((option) => typeof option !== 'string')) {
    throw new Error('Invalid native-evidence security-options input.')
  }
  return [...value].sort()
}

interface NativeContainerInspection {
  readonly Config: {
    readonly Env: readonly string[] | null
    readonly Labels: Readonly<Record<string, string>>
    readonly User: string
  }
  readonly HostConfig: {
    readonly Binds: readonly string[] | null
    readonly CapAdd: readonly string[] | null
    readonly CapDrop: readonly string[] | null
    readonly DeviceCgroupRules: readonly string[] | null
    readonly DeviceRequests: readonly unknown[] | null
    readonly Devices: readonly unknown[] | null
    readonly NetworkMode: string
    readonly PortBindings: Readonly<Record<string, unknown>> | null
    readonly Privileged: boolean
    readonly ReadonlyRootfs: boolean
    readonly SecurityOpt: readonly string[] | null
    readonly NanoCpus: number
    readonly Memory: number
    readonly PidsLimit: number | null
    readonly LogConfig: {
      readonly Type: string
      readonly Config: Readonly<Record<string, string>>
    }
  }
  readonly Mounts: ReadonlyArray<{
    readonly Destination: string
    readonly Name: string
    readonly RW: boolean
    readonly Type: string
  }>
  readonly NetworkSettings: {
    readonly Networks: Readonly<Record<string, unknown>>
    readonly Ports: Readonly<Record<string, unknown>>
  }
}

async function inspectNativeContainer(
  dockerExecutable: string,
  dockerConfigDirectory: string,
  workingDirectory: string,
): Promise<NativeContainerInspection> {
  const result = await runBoundedHostProcess({
    executablePath: dockerExecutable,
    arguments: [
      '--config', dockerConfigDirectory,
      '--context', dockerContext,
      'container', 'inspect', containerName, '--format', '{{json .}}',
    ],
    workingDirectory,
    environment: minimalDockerEnvironment('darwin', process.env),
    standardInput: undefined,
    timeoutMs: 30_000,
    maxInputBytes: 1024,
    maxOutputBytes: 256 * 1024,
  })
  return JSON.parse(result.stdout) as NativeContainerInspection
}

describe.skipIf(!nativeEvidenceEnabled)('native macOS arm64 host-container evidence', () => {
  it('isolates a named runtime and completes bounded lifecycle and deletion', async () => {
    expect(process.platform).toBe('darwin')
    expect(process.arch).toBe('arm64')

    const runtimeProfileRoot = requiredEnvironment('ANCESTRYLLM_NATIVE_PROFILE_ROOT')
    const endpointPath = `${runtimeProfileRoot}/docker.sock`
    const dockerConfigDirectory = `${runtimeProfileRoot}/docker-config`
    const workingDirectory = `${runtimeProfileRoot}/control`
    const endpointMetadata = await lstat(endpointPath)
    const image = requiredEnvironment('ANCESTRYLLM_NATIVE_IMAGE')
    const securityOptions = parseSecurityOptions()
    const labels = {
      'com.ancestryllm.owner': 'ancestryllm',
      'com.ancestryllm.profile': runtimeProfile,
      'com.ancestryllm.project': projectName,
    }
    const compose = {
      projectName,
      labels,
      services: {
        witness: {
          containerName,
          image,
          user: '65532:65532',
          readOnly: true,
          capDrop: ['ALL'],
          securityOptions: ['no-new-privileges:true'],
          init: true,
          cpus: '1.0',
          memory: '256m',
          pidsLimit: 128,
          logging: { driver: 'local', maxSize: '10m', maxFiles: 3 },
          mounts: [{
            kind: 'volume', source: volumeName, target: '/var/lib/ancestryllm', readOnly: false,
          }],
          networks: [networkName],
          ports: [],
        },
      },
      networks: { [networkName]: { internal: true } },
      volumes: { [volumeName]: { preserveOnUninstall: true } },
    }
    const policy = parseHostContainerPolicy({
      schemaVersion: 1,
      platform: 'darwin',
      architecture: 'arm64',
      dockerExecutable: requiredEnvironment('ANCESTRYLLM_NATIVE_DOCKER_EXECUTABLE'),
      dockerComposeExecutable: requiredEnvironment('ANCESTRYLLM_NATIVE_COMPOSE_EXECUTABLE'),
      dockerConfigDirectory,
      workingDirectory,
      runtimeProfile,
      runtimeProfileRoot,
      dockerContext,
      endpoint: {
        scheme: 'unix',
        path: endpointPath,
        canonicalPath: endpointPath,
        ownerUid: endpointMetadata.uid,
        mode: 0o600,
      },
      engine: {
        id: requiredEnvironment('ANCESTRYLLM_NATIVE_ENGINE_ID'),
        serverVersion: requiredEnvironment('ANCESTRYLLM_NATIVE_SERVER_VERSION'),
        apiVersion: requiredEnvironment('ANCESTRYLLM_NATIVE_API_VERSION'),
        operatingSystem: 'linux',
        architecture: 'arm64',
        securityOptions,
      },
      compose,
    })
    const plan = parseHostComposePlan({
      schemaVersion: 1,
      runtimeProfile,
      ...structuredClone(compose),
    }, policy)
    const requests: HostProcessRequest[] = []
    const control = new DockerCliHostControl({
      sourceEnvironment: {
        DOCKER_HOST: 'tcp://attacker.invalid:2375',
        DOCKER_CONTEXT: 'attacker',
        DOCKER_CONFIG: '/private/attacker',
        HOME: '/Users/attacker',
        PATH: '/private/attacker/bin',
        OPENAI_API_KEY: 'native-evidence-canary',
        LANG: 'C.UTF-8',
      },
      run: async (request) => {
        requests.push(request)
        return runBoundedHostProcess(request)
      },
    })
    const supervisor = new HostContainerSupervisor({ policy, plan, control })
    const authorize = (
      operation: 'start' | 'repair' | 'uninstall-preserve' | 'uninstall-delete',
    ) => supervisor.authorize(operation, confirmationPhrase(operation, projectName))
    let deletionCompleted = false

    try {
      await expect(supervisor.inspect()).resolves.toMatchObject({ resourceCount: 0 })
      await expect(supervisor.start(authorize('start'))).resolves.toMatchObject({ resourceCount: 3 })

      const inspection = await inspectNativeContainer(
        policy.dockerExecutable,
        policy.dockerConfigDirectory,
        policy.workingDirectory,
      )
      expect(inspection.Config.User).toBe('65532:65532')
      expect(inspection.Config.Labels).toMatchObject(labels)
      expect(inspection.Config.Env ?? []).not.toContainEqual(expect.stringMatching(
        /^(?:DOCKER_HOST|DOCKER_CONTEXT|DOCKER_CONFIG|OPENAI_API_KEY)=/,
      ))
      expect(inspection.HostConfig).toMatchObject({
        Binds: [`${volumeName}:/var/lib/ancestryllm:rw`],
        CapAdd: null,
        CapDrop: ['ALL'],
        DeviceCgroupRules: null,
        DeviceRequests: null,
        Devices: null,
        NetworkMode: networkName,
        PortBindings: {},
        Privileged: false,
        ReadonlyRootfs: true,
        SecurityOpt: ['no-new-privileges:true'],
        NanoCpus: 1_000_000_000,
        Memory: 256 * 1024 * 1024,
        PidsLimit: 128,
        LogConfig: {
          Type: 'local',
          Config: { 'max-file': '3', 'max-size': '10m' },
        },
      })
      expect(inspection.Mounts).toEqual([expect.objectContaining({
        Destination: '/var/lib/ancestryllm', Name: volumeName, RW: true, Type: 'volume',
      })])
      expect(Object.keys(inspection.NetworkSettings.Networks)).toEqual([networkName])
      expect(inspection.NetworkSettings.Ports).toEqual({})
      expect(JSON.stringify(inspection.Mounts)).not.toContain('docker.sock')

      await expect(supervisor.stop()).resolves.toMatchObject({ resourceCount: 3 })
      await expect(supervisor.repair(authorize('repair'))).resolves.toMatchObject({ resourceCount: 3 })
      await expect(supervisor.uninstall({
        deleteData: false,
        authorization: authorize('uninstall-preserve'),
      })).resolves.toMatchObject({ resourceCount: 1 })
      await expect(supervisor.start(authorize('start'))).resolves.toMatchObject({ resourceCount: 3 })
      await expect(supervisor.uninstall({
        deleteData: true,
        authorization: authorize('uninstall-delete'),
      })).resolves.toMatchObject({ resourceCount: 0 })
      deletionCompleted = true

      expect(requests.length).toBeGreaterThan(0)
      expect(requests.every((request) => (
        request.environment.DOCKER_HOST === undefined
        && request.environment.OPENAI_API_KEY === undefined
        && request.environment.HOME === undefined
        && request.environment.PATH === undefined
        && request.arguments.every((argument) => !argument.includes('attacker'))
      ))).toBe(true)
      expect(requests.filter((request) => (
        request.executablePath === policy.dockerExecutable
      )).every((request) => (
        request.environment.DOCKER_CONTEXT === undefined
        && request.environment.DOCKER_CONFIG === undefined
      ))).toBe(true)
      expect(requests.filter((request) => (
        request.executablePath === policy.dockerComposeExecutable
      )).every((request) => (
        request.environment.DOCKER_CONTEXT === dockerContext
        && request.environment.DOCKER_CONFIG === dockerConfigDirectory
      ))).toBe(true)

      process.stdout.write(`ANCESTRYLLM_NATIVE_EVIDENCE=${JSON.stringify({
        schema_version: 1,
        platform: 'darwin',
        architecture: 'arm64',
        runtime_profile: runtimeProfile,
        docker_context: dockerContext,
        endpoint: { scheme: 'unix', owner_match: true, mode: '0600', stable: true },
        engine: {
          identity_match: true,
          operating_system: 'linux',
          architecture: 'arm64',
          server_version: policy.engine.serverVersion,
          api_version: policy.engine.apiVersion,
          security_options: policy.engine.securityOptions,
        },
        isolation: {
          ambient_selection_ignored: true,
          app_owned_context: true,
          internal_network_only: true,
          renderer_or_container_socket_access: false,
        },
        hardening: {
          digest_pinned_image: true,
          non_root_user: true,
          read_only_root: true,
          capabilities_dropped: true,
          no_new_privileges: true,
          named_volume_only: true,
          published_ports: 0,
          resource_limits: {
            cpus: '1.0',
            memory: '256m',
            pids: 128,
            log_driver: 'local',
            log_max_size: '10m',
            log_max_files: 3,
          },
        },
        lifecycle_resource_counts: {
          initial: 0,
          started: 3,
          stopped: 3,
          repaired: 3,
          uninstalled_preserving_data: 1,
          restarted: 3,
          uninstalled_deleting_data: 0,
        },
        status: 'verified',
      })}\n`)
    } finally {
      if (!deletionCompleted) {
        await supervisor.uninstall({
          deleteData: true,
          authorization: authorize('uninstall-delete'),
        }).catch(() => undefined)
      }
    }
  }, 20 * 60 * 1000)
})
