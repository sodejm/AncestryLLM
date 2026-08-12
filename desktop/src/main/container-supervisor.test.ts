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
  type HostOwnedResource,
  type HostRealizedContainer,
  type HostRealizedNetwork,
  type HostRealizedState,
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

const ownedLabels = {
  'com.ancestryllm.owner': 'ancestryllm',
  'com.ancestryllm.profile': 'ancestryllm-local-arm64',
  'com.ancestryllm.project': 'ancestryllm-local',
}

function plannedResources(): HostOwnedResource[] {
  return [
    { kind: 'container', name: 'ancestryllm-local-gateway', labels: { ...ownedLabels } },
    { kind: 'network', name: 'ancestryllm-local-private', labels: { ...ownedLabels } },
    { kind: 'volume', name: 'ancestryllm-local-data', labels: { ...ownedLabels } },
  ]
}

function realizedContainer(
  overrides: Partial<HostRealizedContainer> = {},
): HostRealizedContainer {
  return {
    containerName: 'ancestryllm-local-gateway',
    image: `ghcr.io/sodejm/ancestryllm-gateway@sha256:${sha256('a')}`,
    user: '65532:65532',
    readOnly: true,
    capDrop: ['ALL'],
    capAdd: [],
    securityOptions: ['no-new-privileges:true'],
    init: true,
    privileged: false,
    deviceCount: 0,
    deviceRequestCount: 0,
    deviceCgroupRuleCount: 0,
    nanoCpus: 1_000_000_000,
    memoryBytes: 256 * 1024 * 1024,
    pidsLimit: 128,
    logging: {
      driver: 'local',
      options: { 'max-file': '3', 'max-size': '10m' },
    },
    mounts: [{
      kind: 'volume',
      source: 'ancestryllm-local-data',
      target: '/var/lib/ancestryllm',
      readOnly: false,
    }],
    networks: ['ancestryllm-local-private'],
    ports: [{
      hostIp: '127.0.0.1',
      published: 49152,
      target: 8000,
      protocol: 'tcp',
    }],
    ...overrides,
  }
}

function realizedState(
  resources: readonly HostOwnedResource[],
  containerOverrides: Partial<HostRealizedContainer> = {},
  networkOverrides: Partial<HostRealizedNetwork> = {},
): HostRealizedState {
  return {
    containers: resources.some((resource) => resource.kind === 'container')
      ? [realizedContainer(containerOverrides)]
      : [],
    networks: resources.some((resource) => resource.kind === 'network')
      ? [{ name: 'ancestryllm-local-private', internal: true, ...networkOverrides }]
      : [],
  }
}

function supervisorFixture(options: {
  endpoint?: () => Promise<HostEndpointObservation>
  observation?: HostRuntimeObservation | (() => Promise<HostRuntimeObservation>)
  resources?: readonly HostOwnedResource[]
    | (() => Promise<readonly HostOwnedResource[]>)
  realization?: HostRealizedState
    | ((resources: readonly HostOwnedResource[]) => Promise<HostRealizedState>)
} = {}) {
  let resources: readonly HostOwnedResource[] = []
  const hasCustomResources = Object.hasOwn(options, 'resources')
  const control: HostContainerControlPort = {
    observe: vi.fn(async () => typeof options.observation === 'function'
      ? options.observation()
      : options.observation ?? runtimeObservation()),
    inventory: vi.fn(async () => {
      if (typeof options.resources === 'function') return options.resources()
      return options.resources ?? resources
    }),
    inspectResources: vi.fn(async (_policy, inventory) => (
      typeof options.realization === 'function'
        ? options.realization(inventory)
        : options.realization ?? realizedState(inventory)
    )),
    apply: vi.fn(async (_policy, _plan, operation) => {
      if (hasCustomResources) return
      if (operation === 'uninstall-delete') {
        resources = []
      } else if (operation === 'uninstall-preserve') {
        resources = plannedResources().filter((resource) => resource.kind === 'volume')
      } else if (operation === 'start' || operation === 'repair') {
        resources = plannedResources()
      }
    }),
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
    ['cross-service published port collision', (value: Record<string, unknown>) => {
      const compose = value.compose as {
        services: Record<string, Record<string, unknown>>
      }
      const gateway = compose.services.gateway!
      compose.services.worker = {
        ...structuredClone(gateway),
        containerName: 'ancestryllm-local-worker',
        ports: [{
          ...(gateway.ports as Array<Record<string, unknown>>)[0],
          target: 8001,
        }],
      }
    }],
    ['prototype-chain volume reference', (value: Record<string, unknown>) => {
      const compose = value.compose as {
        services: { gateway: { mounts: Array<{ source: string }> } }
      }
      compose.services.gateway.mounts[0]!.source = 'constructor'
    }],
    ['prototype-chain network reference', (value: Record<string, unknown>) => {
      const compose = value.compose as {
        services: { gateway: { networks: string[] } }
      }
      compose.services.gateway.networks[0] = 'constructor'
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
  it.each(['start', 'stop', 'repair'] as const)(
    'does not report a successful %s until every planned resource exists',
    async (operation) => {
      let inventoryCall = 0
      const before = operation === 'start' ? [] : plannedResources()
      const incomplete = plannedResources()
        .filter((resource) => resource.kind !== 'network')
      const { supervisor, control } = supervisorFixture({
        resources: async () => {
          inventoryCall += 1
          return inventoryCall === 1 ? before : incomplete
        },
      })

      const result = operation === 'stop'
        ? supervisor.stop()
        : operation === 'start'
          ? supervisor.start(supervisor.authorize(
              'start', confirmationPhrase('start', 'ancestryllm-local'),
            ))
          : supervisor.repair(supervisor.authorize(
              'repair', confirmationPhrase('repair', 'ancestryllm-local'),
            ))
      await expect(result).rejects.toMatchObject({ code: 'RESOURCE_CONFLICT' })
      expect(control.apply).toHaveBeenCalledOnce()
    },
  )

  it('requires realized container hardening inspection before reporting verified state', async () => {
    const resources = plannedResources()
      .filter((resource) => resource.kind === 'container')
    const { supervisor, control } = supervisorFixture({
      resources,
    })

    await supervisor.inspect()
    expect(control.inspectResources).toHaveBeenCalledOnce()
    expect(control.inspectResources).toHaveBeenCalledWith(expect.anything(), resources)
  })

  const realizedContainerDriftCases: ReadonlyArray<[
    string,
    Partial<HostRealizedContainer>,
  ]> = [
    ['image digest', { image: `ghcr.io/sodejm/ancestryllm-gateway@sha256:${sha256('b')}` }],
    ['numeric user', { user: '0:0' }],
    ['read-only root', { readOnly: false }],
    ['dropped capabilities', { capDrop: [] }],
    ['added capabilities', { capAdd: ['NET_ADMIN'] }],
    ['security options', { securityOptions: [] }],
    ['init setting', { init: false }],
    ['privileged setting', { privileged: true }],
    ['device access', { deviceCount: 1 }],
    ['device request access', { deviceRequestCount: 1 }],
    ['device cgroup rule access', { deviceCgroupRuleCount: 1 }],
    ['CPU limit', { nanoCpus: 2_000_000_000 }],
    ['memory limit', { memoryBytes: 512 * 1024 * 1024 }],
    ['PID limit', { pidsLimit: 256 }],
    ['logging limit', { logging: { driver: 'json-file', options: {} } }],
    ['mount set', { mounts: [] }],
    ['network attachment', { networks: [] }],
    ['port binding', { ports: [] }],
  ]

  it.each(realizedContainerDriftCases)(
    'fails closed when realized container %s drifts',
    async (_name, overrides) => {
    const resources = plannedResources()
    const { supervisor, control } = supervisorFixture({
      resources,
      realization: realizedState(resources, overrides),
    })

    await expect(supervisor.inspect()).rejects.toMatchObject({ code: 'RESOURCE_CONFLICT' })
    expect(control.apply).not.toHaveBeenCalled()
    },
  )

  it('fails closed when a realized network is not private', async () => {
    const resources = plannedResources()
    const { supervisor, control } = supervisorFixture({
      resources,
      realization: realizedState(resources, {}, { internal: false }),
    })

    await expect(supervisor.inspect()).rejects.toMatchObject({ code: 'RESOURCE_CONFLICT' })
    expect(control.apply).not.toHaveBeenCalled()
  })

  it('revalidates the socket, daemon identity, and owned inventory around a mutation', async () => {
    const { supervisor, control, inspectEndpoint } = supervisorFixture()
    const authorization = supervisor.authorize(
      'start',
      confirmationPhrase('start', 'ancestryllm-local'),
    )

    await expect(supervisor.start(authorization)).resolves.toEqual({
      status: 'verified', operation: 'start', resourceCount: 3,
      serverVersion: '29.5.2', apiVersion: '1.54',
    })

    expect(inspectEndpoint).toHaveBeenCalledTimes(4)
    expect(control.observe).toHaveBeenCalledTimes(2)
    expect(control.inventory).toHaveBeenCalledTimes(2)
    expect(control.inspectResources).toHaveBeenCalledTimes(2)
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

  it('treats Docker resource namespaces independently when names overlap', async () => {
    const policyValue = policyInput()
    const compose = policyValue.compose as {
      networks: Record<string, unknown>
      volumes: Record<string, unknown>
      services: {
        gateway: {
          mounts: Array<{ source: string }>
          networks: string[]
        }
      }
    }
    const sharedName = 'ancestryllm-local-shared'
    compose.networks = { [sharedName]: { internal: true } }
    compose.volumes = { [sharedName]: { preserveOnUninstall: true } }
    compose.services.gateway.mounts[0]!.source = sharedName
    compose.services.gateway.networks = [sharedName]
    const planValue = {
      schemaVersion: 1,
      runtimeProfile: policyValue.runtimeProfile,
      ...structuredClone(compose),
    }
    const resources: HostOwnedResource[] = [
      { kind: 'container', name: 'ancestryllm-local-gateway', labels: { ...ownedLabels } },
      { kind: 'network', name: sharedName, labels: { ...ownedLabels } },
      { kind: 'volume', name: sharedName, labels: { ...ownedLabels } },
    ]
    const control: HostContainerControlPort = {
      observe: vi.fn(async () => runtimeObservation()),
      inventory: vi.fn(async () => resources),
      inspectResources: vi.fn(async () => ({
        containers: [realizedContainer({
          mounts: [{
            kind: 'volume', source: sharedName,
            target: '/var/lib/ancestryllm', readOnly: false,
          }],
          networks: [sharedName],
        })],
        networks: [{ name: sharedName, internal: true }],
      })),
      apply: vi.fn(async () => undefined),
    }
    const supervisor = new HostContainerSupervisor({
      policy: policyValue,
      plan: planValue,
      control,
      inspectEndpoint: async () => endpointObservation(),
    })

    await expect(supervisor.inspect()).resolves.toMatchObject({
      status: 'verified', operation: 'inspect', resourceCount: 3,
    })
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

  it.each([
    ['uninstall-preserve' as const, ['volume:ancestryllm-local-data']],
    ['uninstall-delete' as const, []],
  ])('requires the exact %s postcondition', async (operation, expected) => {
    const before = plannedResources()
    let inventoryCall = 0
    const { supervisor, control } = supervisorFixture({
      resources: async () => {
        inventoryCall += 1
        if (inventoryCall === 1) return before
        return operation === 'uninstall-preserve'
          ? before.filter((resource) => resource.kind === 'volume')
          : []
      },
    })
    const authorization = supervisor.authorize(
      operation,
      confirmationPhrase(operation, 'ancestryllm-local'),
    )

    const diagnostics = await supervisor.uninstall({
      deleteData: operation === 'uninstall-delete',
      authorization,
    })
    expect(diagnostics.resourceCount).toBe(expected.length)
    expect(control.apply).toHaveBeenCalledWith(expect.anything(), expect.anything(), operation)
  })

  it.each([
    ['uninstall-preserve' as const, plannedResources()],
    ['uninstall-delete' as const, plannedResources()
      .filter((resource) => resource.kind === 'volume')],
  ])('rejects an incomplete %s postcondition', async (operation, after) => {
    let inventoryCall = 0
    const { supervisor } = supervisorFixture({
      resources: async () => {
        inventoryCall += 1
        return inventoryCall === 1 ? plannedResources() : after
      },
    })
    const authorization = supervisor.authorize(
      operation,
      confirmationPhrase(operation, 'ancestryllm-local'),
    )

    await expect(supervisor.uninstall({
      deleteData: operation === 'uninstall-delete',
      authorization,
    })).rejects.toMatchObject({ code: 'RESOURCE_CONFLICT' })
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
