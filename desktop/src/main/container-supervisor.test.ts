// Verifies exact host container policy validation and owned-resource lifecycle behavior.

import { chmod, mkdtemp, realpath, rm, symlink } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  HostContainerControlError,
  HostContainerSupervisor,
  confirmationPhrase,
  inspectUnixSocketEndpoint,
  parseHostComposePlan,
  parseHostContainerPolicy,
  type HostContainerControlPort,
  type HostEndpointObservation,
  type HostRuntimeObservation,
} from './container-supervisor'

const sha256 = (character: string): string => `${character.repeat(64)}`

function policyInput(): Record<string, unknown> {
  const labels = {
    'com.ancestryllm.owner': 'ancestryllm',
    'com.ancestryllm.profile': 'ancestryllm-local-arm64',
    'com.ancestryllm.project': 'ancestryllm-local',
  }
  return {
    schemaVersion: 1,
    platform: 'darwin',
    architecture: 'arm64',
    dockerExecutable: '/opt/ancestryllm/bin/docker',
    dockerComposeExecutable: '/opt/ancestryllm/bin/docker-compose',
    dockerConfigDirectory: '/private/ancestryllm/profiles/ancestryllm-local-arm64/docker-config',
    workingDirectory: '/private/ancestryllm/profiles/ancestryllm-local-arm64/control',
    runtimeProfile: 'ancestryllm-local-arm64',
    runtimeProfileRoot: '/private/ancestryllm/profiles/ancestryllm-local-arm64',
    dockerContext: 'colima-ancestryllm-local-arm64',
    endpoint: {
      scheme: 'unix',
      path: '/private/ancestryllm/profiles/ancestryllm-local-arm64/docker.sock',
      canonicalPath: '/private/ancestryllm/profiles/ancestryllm-local-arm64/docker.sock',
      ownerUid: 501,
      mode: 0o600,
    },
    engine: {
      id: 'engine-ancestryllm-local-arm64',
      serverVersion: '29.5.2',
      apiVersion: '1.54',
      operatingSystem: 'linux',
      architecture: 'arm64',
      securityOptions: ['cgroupns', 'seccomp'],
    },
    compose: {
      projectName: 'ancestryllm-local',
      labels,
      services: {
        gateway: {
          containerName: 'ancestryllm-local-gateway',
          image: `ghcr.io/sodejm/ancestryllm-gateway@sha256:${sha256('a')}`,
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
            kind: 'volume', source: 'ancestryllm-local-data',
            target: '/var/lib/ancestryllm', readOnly: false,
          }],
          networks: ['ancestryllm-local-private'],
          ports: [{
            hostIp: '127.0.0.1', published: 49152,
            target: 8000, protocol: 'tcp',
          }],
        },
      },
      networks: {
        'ancestryllm-local-private': { internal: true },
      },
      volumes: {
        'ancestryllm-local-data': { preserveOnUninstall: true },
      },
    },
  }
}

function composePlanInput(): Record<string, unknown> {
  const source = policyInput()
  const compose = structuredClone(source.compose) as Record<string, unknown>
  return {
    schemaVersion: 1,
    runtimeProfile: source.runtimeProfile,
    ...compose,
  }
}

function endpointObservation(overrides: Partial<HostEndpointObservation> = {}): HostEndpointObservation {
  return {
    scheme: 'unix',
    path: '/private/ancestryllm/profiles/ancestryllm-local-arm64/docker.sock',
    canonicalPath: '/private/ancestryllm/profiles/ancestryllm-local-arm64/docker.sock',
    ownerUid: 501,
    mode: 0o600,
    device: 16777234,
    inode: 4242,
    kind: 'socket',
    ...overrides,
  }
}

function runtimeObservation(overrides: Partial<HostRuntimeObservation> = {}): HostRuntimeObservation {
  return {
    runtimeProfile: 'ancestryllm-local-arm64',
    dockerContext: 'colima-ancestryllm-local-arm64',
    endpoint: 'unix:///private/ancestryllm/profiles/ancestryllm-local-arm64/docker.sock',
    engineId: 'engine-ancestryllm-local-arm64',
    serverVersion: '29.5.2',
    apiVersion: '1.54',
    operatingSystem: 'linux',
    architecture: 'arm64',
    securityOptions: ['cgroupns', 'seccomp'],
    ...overrides,
  }
}

function supervisorFixture(options: {
  endpoint?: () => Promise<HostEndpointObservation>
  observation?: HostRuntimeObservation | (() => Promise<HostRuntimeObservation>)
  resources?: Awaited<ReturnType<HostContainerControlPort['inventory']>>
    | (() => ReturnType<HostContainerControlPort['inventory']>)
} = {}) {
  const control: HostContainerControlPort = {
    observe: vi.fn(async () => typeof options.observation === 'function'
      ? options.observation()
      : options.observation ?? runtimeObservation()),
    inventory: vi.fn(async () => typeof options.resources === 'function'
      ? options.resources()
      : options.resources ?? []),
    apply: vi.fn(async () => undefined),
  }
  const inspectEndpoint = vi.fn(options.endpoint ?? (async () => endpointObservation()))
  const supervisor = new HostContainerSupervisor({
    policy: policyInput(),
    plan: composePlanInput(),
    control,
    inspectEndpoint,
    tokenFactory: () => 'T'.repeat(43),
    now: () => 1_700_000_000_000,
  })
  return { supervisor, control, inspectEndpoint }
}

describe('host container policy', () => {
  it('accepts the exact versioned app-owned runtime and pinned Compose model', () => {
    const policy = parseHostContainerPolicy(policyInput())
    const plan = parseHostComposePlan(composePlanInput(), policy)

    expect(policy.schemaVersion).toBe(1)
    expect(policy.dockerContext).toBe('colima-ancestryllm-local-arm64')
    expect(policy.dockerComposeExecutable).toBe('/opt/ancestryllm/bin/docker-compose')
    expect(policy.compose.services.gateway?.image).toMatch(/@sha256:[a-f0-9]{64}$/)
    expect(policy.compose.services.gateway).toMatchObject({
      cpus: '1.0',
      memory: '256m',
      pidsLimit: 128,
      logging: { driver: 'local', maxSize: '10m', maxFiles: 3 },
    })
    expect(plan).toEqual(expect.objectContaining({
      runtimeProfile: 'ancestryllm-local-arm64',
      projectName: 'ancestryllm-local',
    }))
  })

  it.each([
    ['unknown schema', (value: Record<string, unknown>) => { value.schemaVersion = 2 }],
    ['unsupported operating system', (value: Record<string, unknown>) => { value.platform = 'linux' }],
    ['unsupported architecture', (value: Record<string, unknown>) => { value.architecture = 'x64' }],
    ['remote endpoint', (value: Record<string, unknown>) => {
      value.endpoint = { ...(value.endpoint as object), scheme: 'tcp', path: 'tcp://127.0.0.1:2375' }
    }],
    ['ambient context', (value: Record<string, unknown>) => { value.dockerContext = 'default' }],
    ['hostile context', (value: Record<string, unknown>) => { value.dockerContext = 'default; rm -rf /' }],
    ['external Docker config', (value: Record<string, unknown>) => {
      value.dockerConfigDirectory = '/Users/alice/.docker'
    }],
    ['external working directory', (value: Record<string, unknown>) => {
      value.workingDirectory = '/private/tmp'
    }],
    ['unpinned image', (value: Record<string, unknown>) => {
      const compose = value.compose as { services: { gateway: { image: string } } }
      compose.services.gateway.image = 'ghcr.io/sodejm/ancestryllm-gateway:latest'
    }],
    ['broad socket mode', (value: Record<string, unknown>) => {
      const endpoint = value.endpoint as { mode: number }
      endpoint.mode = 0o660
    }],
    ['duplicate mount target', (value: Record<string, unknown>) => {
      const compose = value.compose as {
        services: { gateway: { mounts: Array<Record<string, unknown>> } }
      }
      compose.services.gateway.mounts.push({
        ...compose.services.gateway.mounts[0],
        source: 'ancestryllm-local-data',
      })
    }],
    ['duplicate published port', (value: Record<string, unknown>) => {
      const compose = value.compose as {
        services: { gateway: { ports: Array<Record<string, unknown>> } }
      }
      compose.services.gateway.ports.push({
        ...compose.services.gateway.ports[0],
        target: 8001,
      })
    }],
  ])('rejects a %s before any engine access', (_name, mutate) => {
    const input = policyInput()
    mutate(input)
    expect(() => parseHostContainerPolicy(input)).toThrowError(
      expect.objectContaining({ code: 'INVALID_POLICY' }),
    )
  })

  it('rejects unknown, hidden, accessor, and prototype-bearing policy fields', () => {
    const unknown = policyInput()
    unknown.extra = true
    expect(() => parseHostContainerPolicy(unknown)).toThrowError(
      expect.objectContaining({ code: 'INVALID_POLICY' }),
    )

    const hidden = policyInput()
    Object.defineProperty(hidden, 'secret', { value: 'canary', enumerable: false })
    expect(() => parseHostContainerPolicy(hidden)).toThrowError(
      expect.objectContaining({ code: 'INVALID_POLICY' }),
    )

    const accessor = policyInput()
    Object.defineProperty(accessor, 'runtimeProfile', { enumerable: true, get: () => 'unsafe' })
    expect(() => parseHostContainerPolicy(accessor)).toThrowError(
      expect.objectContaining({ code: 'INVALID_POLICY' }),
    )

    const inherited = Object.assign(Object.create({ privileged: true }), policyInput())
    expect(() => parseHostContainerPolicy(inherited)).toThrowError(
      expect.objectContaining({ code: 'INVALID_POLICY' }),
    )
  })
})

describe('generated Compose validation', () => {
  it.each([
    ['project name', (plan: Record<string, unknown>) => {
      plan.projectName = 'ancestryllm-attacker'
    }],
    ['override file', (plan: Record<string, unknown>) => { plan.overrideFiles = ['/tmp/hostile.yml'] }],
    ['command', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: Record<string, unknown> }
      services.gateway.command = ['sh', '-c', 'id']
    }],
    ['entrypoint', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: Record<string, unknown> }
      services.gateway.entrypoint = ['/bin/sh']
    }],
    ['environment injection', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: Record<string, unknown> }
      services.gateway.environment = { DOCKER_HOST: 'unix:///var/run/docker.sock' }
    }],
    ['privileged mode', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: Record<string, unknown> }
      services.gateway.privileged = true
    }],
    ['host network', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: Record<string, unknown> }
      services.gateway.networkMode = 'host'
    }],
    ['writable root', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: { readOnly: boolean } }
      services.gateway.readOnly = false
    }],
    ['unbounded CPU', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: Record<string, unknown> }
      services.gateway.cpus = '0'
    }],
    ['oversized memory', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: Record<string, unknown> }
      services.gateway.memory = '16384m'
    }],
    ['unbounded PID count', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: Record<string, unknown> }
      services.gateway.pidsLimit = 0
    }],
    ['unbounded logs', (plan: Record<string, unknown>) => {
      const services = plan.services as {
        gateway: { logging: { maxFiles: number } }
      }
      services.gateway.logging.maxFiles = 0
    }],
    ['elevated capability', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: { capDrop: string[] } }
      services.gateway.capDrop = []
    }],
    ['unexpected mount', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: { mounts: Array<{ source: string }> } }
      services.gateway.mounts[0]!.source = '/'
    }],
    ['wildcard port', (plan: Record<string, unknown>) => {
      const services = plan.services as { gateway: { ports: Array<{ hostIp: string }> } }
      services.gateway.ports[0]!.hostIp = '0.0.0.0'
    }],
    ['foreign label', (plan: Record<string, unknown>) => {
      const labels = plan.labels as Record<string, string>
      labels['com.ancestryllm.owner'] = 'attacker'
    }],
  ])('rejects an injected %s', (_name, mutate) => {
    const policy = parseHostContainerPolicy(policyInput())
    const plan = composePlanInput()
    mutate(plan)
    expect(() => parseHostComposePlan(plan, policy)).toThrowError(
      expect.objectContaining({ code: 'INVALID_PLAN' }),
    )
  })
})

describe('host-only lifecycle authority', () => {
  it('revalidates the socket, daemon identity, and owned inventory around a mutation', async () => {
    const { supervisor, control, inspectEndpoint } = supervisorFixture()
    const authorization = supervisor.authorize(
      'start',
      confirmationPhrase('start', 'ancestryllm-local'),
    )

    await expect(supervisor.start(authorization)).resolves.toEqual({
      status: 'verified', operation: 'start', resourceCount: 0,
      serverVersion: '29.5.2', apiVersion: '1.54',
    })

    expect(inspectEndpoint).toHaveBeenCalledTimes(4)
    expect(control.observe).toHaveBeenCalledTimes(2)
    expect(control.inventory).toHaveBeenCalledTimes(2)
    expect(control.apply).toHaveBeenCalledOnce()
    expect(control.apply).toHaveBeenCalledWith(
      expect.anything(), expect.anything(), 'start',
    )
  })

  it('rejects a replaced socket before invoking the lifecycle adapter', async () => {
    const observations = [
      endpointObservation(),
      endpointObservation({ inode: 9999 }),
    ]
    const { supervisor, control } = supervisorFixture({
      endpoint: async () => observations.shift() ?? endpointObservation({ inode: 9999 }),
    })
    const authorization = supervisor.authorize(
      'start',
      confirmationPhrase('start', 'ancestryllm-local'),
    )

    await expect(supervisor.start(authorization)).rejects.toMatchObject({
      code: 'ENDPOINT_CHANGED',
    })
    expect(control.apply).not.toHaveBeenCalled()
  })

  it('rejects a socket replacement after the lifecycle adapter returns', async () => {
    const observations = [
      endpointObservation(),
      endpointObservation(),
      endpointObservation({ inode: 9999 }),
      endpointObservation({ inode: 9999 }),
    ]
    const { supervisor, control } = supervisorFixture({
      endpoint: async () => observations.shift() ?? endpointObservation({ inode: 9999 }),
    })
    const authorization = supervisor.authorize(
      'start',
      confirmationPhrase('start', 'ancestryllm-local'),
    )

    await expect(supervisor.start(authorization)).rejects.toMatchObject({
      code: 'ENDPOINT_CHANGED',
    })
    expect(control.apply).toHaveBeenCalledOnce()
  })

  it('rejects daemon identity drift after the lifecycle adapter returns', async () => {
    const observations = [
      runtimeObservation(),
      runtimeObservation({ engineId: 'replacement-engine' }),
    ]
    const { supervisor, control } = supervisorFixture({
      observation: async () => observations.shift() ?? observations[0]!,
    })
    const authorization = supervisor.authorize(
      'repair',
      confirmationPhrase('repair', 'ancestryllm-local'),
    )

    await expect(supervisor.repair(authorization)).rejects.toMatchObject({
      code: 'ENGINE_UNTRUSTED',
    })
    expect(control.apply).toHaveBeenCalledOnce()
  })

  it.each([
    ['context', { dockerContext: 'default' }],
    ['endpoint', { endpoint: 'ssh://host/run/docker.sock' }],
    ['engine', { engineId: 'attacker-engine' }],
    ['version', { serverVersion: '30.0.0' }],
    ['API version', { apiVersion: '1.55' }],
    ['architecture', { architecture: 'amd64' }],
    ['security options', { securityOptions: ['cgroupns'] }],
  ])('rejects a compromised %s observation', async (_name, override) => {
    const { supervisor, control } = supervisorFixture({
      observation: runtimeObservation(override),
    })

    await expect(supervisor.inspect()).rejects.toMatchObject({ code: 'ENGINE_UNTRUSTED' })
    expect(control.apply).not.toHaveBeenCalled()
  })

  it('fails closed on unlabeled, foreign, duplicate, or unexpected resources', async () => {
    const labels = {
      'com.ancestryllm.owner': 'ancestryllm',
      'com.ancestryllm.profile': 'ancestryllm-local-arm64',
      'com.ancestryllm.project': 'ancestryllm-local',
    }
    const cases = [
      [{ kind: 'container' as const, name: 'ancestryllm-local-gateway', labels: {} }],
      [{ kind: 'container' as const, name: 'ancestryllm-local-gateway', labels: { ...labels, 'com.ancestryllm.owner': 'other' } }],
      [
        { kind: 'container' as const, name: 'ancestryllm-local-gateway', labels },
        { kind: 'container' as const, name: 'ancestryllm-local-gateway', labels },
      ],
      [{ kind: 'volume' as const, name: 'ancestryllm-local-foreign', labels }],
    ]

    for (const resources of cases) {
      const { supervisor } = supervisorFixture({ resources })
      await expect(supervisor.inspect()).rejects.toMatchObject({ code: 'RESOURCE_CONFLICT' })
    }
  })

  it('binds confirmations to one exact operation and consumes them once', async () => {
    const { supervisor, control } = supervisorFixture()

    expect(() => supervisor.authorize('repair', 'yes')).toThrowError(
      expect.objectContaining({ code: 'AUTHORIZATION_REQUIRED' }),
    )
    const startAuthorization = supervisor.authorize(
      'start', confirmationPhrase('start', 'ancestryllm-local'),
    )
    await supervisor.start(startAuthorization)
    await expect(supervisor.start(startAuthorization)).rejects.toMatchObject({
      code: 'AUTHORIZATION_REQUIRED',
    })

    const deleteAuthorization = supervisor.authorize(
      'uninstall-delete', confirmationPhrase('uninstall-delete', 'ancestryllm-local'),
    )
    await supervisor.uninstall({ deleteData: true, authorization: deleteAuthorization })
    expect(control.apply).toHaveBeenLastCalledWith(
      expect.anything(), expect.anything(), 'uninstall-delete',
    )
    await expect(supervisor.uninstall({
      deleteData: true, authorization: deleteAuthorization,
    })).rejects.toMatchObject({ code: 'AUTHORIZATION_REQUIRED' })
  })

  it('preserves data by default and exposes no generic Docker primitive', async () => {
    const { supervisor, control } = supervisorFixture()
    const authorization = supervisor.authorize(
      'uninstall-preserve',
      confirmationPhrase('uninstall-preserve', 'ancestryllm-local'),
    )
    await supervisor.uninstall({ deleteData: false, authorization })
    expect(control.apply).toHaveBeenLastCalledWith(
      expect.anything(), expect.anything(), 'uninstall-preserve',
    )

    const methods = Object.getOwnPropertyNames(HostContainerSupervisor.prototype)
    expect(methods).toEqual(expect.arrayContaining([
      'inspect', 'authorize', 'start', 'stop', 'repair', 'uninstall',
    ]))
    expect(methods).not.toEqual(expect.arrayContaining([
      'exec', 'build', 'copy', 'mount', 'run', 'request', 'command',
    ]))
  })

  it('returns stable structural errors without attacker output or local paths', async () => {
    const canary = 'canary-secret /Users/alice/family.ged ssh://attacker'
    const { supervisor, control } = supervisorFixture()
    vi.mocked(control.observe).mockRejectedValueOnce(new Error(canary))

    let failure: unknown
    try {
      await supervisor.inspect()
    } catch (error) {
      failure = error
    }
    expect(failure).toBeInstanceOf(HostContainerControlError)
    expect(failure).toMatchObject({ code: 'CONTROL_FAILED' })
    expect(String(failure)).not.toContain(canary)
    expect(String(failure)).not.toContain('/Users/')
  })
})

const temporaryDirectories: string[] = []

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map(async (directory) => {
    await rm(directory, { recursive: true, force: true })
  }))
})

describe('native Unix socket inspection', () => {
  it('accepts a canonical owner-only socket and rejects symlink substitution', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'ancestryllm-host-control-'))
    temporaryDirectories.push(directory)
    const canonicalDirectory = await realpath(directory)
    const socketPath = join(canonicalDirectory, 'docker.sock')
    const aliasPath = join(canonicalDirectory, 'docker-alias.sock')
    const server = createServer()
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(socketPath, resolve)
    })
    try {
      await chmod(socketPath, 0o600)
      const inspected = await inspectUnixSocketEndpoint(socketPath)
      expect(inspected).toEqual(expect.objectContaining({
        scheme: 'unix', path: socketPath, canonicalPath: socketPath,
        mode: 0o600, kind: 'socket',
      }))

      await symlink(socketPath, aliasPath)
      await expect(inspectUnixSocketEndpoint(aliasPath)).rejects.toMatchObject({
        code: 'ENDPOINT_UNTRUSTED',
      })
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })
})
