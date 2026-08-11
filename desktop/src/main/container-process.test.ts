// Verifies bounded, no-shell Docker process execution and stable fail-closed errors.

import { describe, expect, it, vi } from 'vitest'
import {
  DockerCliHostControl,
  HostContainerProcessError,
  dockerProcessSpawnOptions,
  minimalDockerEnvironment,
  runBoundedHostProcess,
  type HostProcessRequest,
  type RunHostProcess,
} from './container-process'
import {
  parseHostComposePlan,
  parseHostContainerPolicy,
  type HostContainerPolicy,
} from './container-supervisor'

const digest = 'a'.repeat(64)

function policy(): HostContainerPolicy {
  const labels = {
    'com.ancestryllm.owner': 'ancestryllm',
    'com.ancestryllm.profile': 'ancestryllm-local-arm64',
    'com.ancestryllm.project': 'ancestryllm-local',
  }
  return parseHostContainerPolicy({
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
      serverVersion: '29.5.2', apiVersion: '1.54', operatingSystem: 'linux',
      architecture: 'arm64', securityOptions: ['cgroupns', 'seccomp'],
    },
    compose: {
      projectName: 'ancestryllm-local', labels,
      services: {
        gateway: {
          containerName: 'ancestryllm-local-gateway',
          image: `ghcr.io/sodejm/ancestryllm-gateway@sha256:${digest}`,
          user: '65532:65532', readOnly: true, capDrop: ['ALL'],
          securityOptions: ['no-new-privileges:true'], init: true,
          cpus: '1.0', memory: '256m', pidsLimit: 128,
          logging: { driver: 'local', maxSize: '10m', maxFiles: 3 },
          mounts: [{
            kind: 'volume', source: 'ancestryllm-local-data',
            target: '/var/lib/ancestryllm', readOnly: false,
          }],
          networks: ['ancestryllm-local-private'],
          ports: [{ hostIp: '127.0.0.1', published: 49152, target: 8000, protocol: 'tcp' }],
        },
      },
      networks: { 'ancestryllm-local-private': { internal: true } },
      volumes: { 'ancestryllm-local-data': { preserveOnUninstall: true } },
    },
  })
}

function plan(runtimePolicy: HostContainerPolicy) {
  return parseHostComposePlan({
    schemaVersion: 1,
    runtimeProfile: runtimePolicy.runtimeProfile,
    ...structuredClone(runtimePolicy.compose),
  }, runtimePolicy)
}

describe('Docker CLI process boundary', () => {
  it('drops ambient daemon selection, credentials, provider secrets, and user paths', () => {
    expect(minimalDockerEnvironment('darwin', {
      DOCKER_HOST: 'ssh://attacker', DOCKER_CONTEXT: 'default',
      DOCKER_CERT_PATH: '/private/certs', DOCKER_TLS_VERIFY: '1',
      PATH: '/attacker/bin', HOME: '/Users/alice', OPENAI_API_KEY: 'canary',
      LANG: 'en_US.UTF-8', TMPDIR: '/private/tmp',
    })).toEqual({ LANG: 'en_US.UTF-8', TMPDIR: '/private/tmp' })
  })

  it('spawns an isolated process group with no shell and fixed pipes', () => {
    expect(dockerProcessSpawnOptions('/private/control', {}, 'darwin')).toEqual({
      cwd: '/private/control', env: {}, shell: false, detached: true,
      windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
    })
  })

  it('builds only exact context, inspection, inventory, and lifecycle argv', async () => {
    const requests: HostProcessRequest[] = []
    const outputs = [
      'colima-ancestryllm-local-arm64|ancestryllm-field|unix:///private/ancestryllm/profiles/ancestryllm-local-arm64/docker.sock\n',
      '29.5.2|ancestryllm-field|1.54\n',
      'engine-ancestryllm-local-arm64|ancestryllm-field|linux|ancestryllm-field|aarch64|ancestryllm-field|["cgroupns","seccomp"]\n',
      '', '', '', '', '', '',
      '', '', '',
    ]
    const run: RunHostProcess = vi.fn(async (request) => {
      requests.push(request)
      return { stdout: outputs.shift() ?? '' }
    })
    const runtimePolicy = policy()
    const composePlan = plan(runtimePolicy)
    const control = new DockerCliHostControl({
      run, sourceEnvironment: {
        DOCKER_HOST: 'tcp://attacker:2375', DOCKER_CONTEXT: 'attacker',
        LANG: 'C.UTF-8',
      },
    })

    await expect(control.observe(runtimePolicy)).resolves.toEqual({
      runtimeProfile: 'ancestryllm-local-arm64',
      dockerContext: 'colima-ancestryllm-local-arm64',
      endpoint: 'unix:///private/ancestryllm/profiles/ancestryllm-local-arm64/docker.sock',
      engineId: 'engine-ancestryllm-local-arm64', serverVersion: '29.5.2',
      apiVersion: '1.54', operatingSystem: 'linux', architecture: 'arm64',
      securityOptions: ['cgroupns', 'seccomp'],
    })
    await expect(control.inventory(runtimePolicy)).resolves.toEqual([])
    await control.apply(runtimePolicy, composePlan, 'start')
    await control.apply(runtimePolicy, composePlan, 'stop')
    await control.apply(runtimePolicy, composePlan, 'repair')
    await control.apply(runtimePolicy, composePlan, 'uninstall-preserve')
    await control.apply(runtimePolicy, composePlan, 'uninstall-delete')

    expect(requests.slice(0, 9).every((request) => (
      request.executablePath === '/opt/ancestryllm/bin/docker'
    ))).toBe(true)
    expect(requests.slice(-5).every((request) => (
      request.executablePath === '/opt/ancestryllm/bin/docker-compose'
    ))).toBe(true)
    expect(requests.every((request) => request.environment.DOCKER_HOST === undefined)).toBe(true)
    expect(requests.every((request) => request.arguments.every((argument) => (
      !argument.includes('\n') && !argument.includes('\r')
    )))).toBe(true)
    expect(requests.slice(0, 9).every((request) => (
      request.environment.DOCKER_CONTEXT === undefined
      && request.environment.DOCKER_CONFIG === undefined
    ))).toBe(true)
    expect(requests[0]?.arguments).toEqual([
      '--config', '/private/ancestryllm/profiles/ancestryllm-local-arm64/docker-config',
      'context', 'inspect', 'colima-ancestryllm-local-arm64', '--format',
      '{{.Name}}|ancestryllm-field|{{(index .Endpoints "docker").Host}}',
    ])
    expect(requests[2]?.arguments).toEqual([
      '--config', '/private/ancestryllm/profiles/ancestryllm-local-arm64/docker-config',
      '--context', 'colima-ancestryllm-local-arm64',
      'info', '--format',
      '{{.ID}}|ancestryllm-field|{{.OSType}}|ancestryllm-field|{{.Architecture}}|ancestryllm-field|{{json .SecurityOptions}}',
    ])
    const lifecycle = requests.slice(-5)
    expect(lifecycle.every((request) => (
      request.environment.DOCKER_CONTEXT === 'colima-ancestryllm-local-arm64'
      && request.environment.DOCKER_CONFIG
        === '/private/ancestryllm/profiles/ancestryllm-local-arm64/docker-config'
      && request.environment.LANG === 'C.UTF-8'
    ))).toBe(true)
    expect(lifecycle.every((request) => request.arguments.slice(0, 6).join(' ')
      === '--ansi never --project-name ancestryllm-local --file -')).toBe(true)
    expect(lifecycle.every((request) => !request.arguments.includes('compose'))).toBe(true)
    expect(lifecycle.map((request) => {
      const standardInputIndex = request.arguments.indexOf('-')
      return request.arguments.slice(standardInputIndex + 1)
    })).toEqual([
      ['up', '--detach', '--wait', '--remove-orphans'],
      ['stop', '--timeout', '20'],
      ['up', '--detach', '--wait', '--remove-orphans', '--force-recreate'],
      ['down', '--remove-orphans', '--timeout', '20'],
      ['down', '--remove-orphans', '--volumes', '--timeout', '20'],
    ])
    expect(lifecycle.every((request) => request.standardInput?.includes(`@sha256:${digest}`))).toBe(true)
    for (const request of lifecycle) {
      const compose = JSON.parse(request.standardInput ?? '{}') as {
        services?: { gateway?: Record<string, unknown> }
      }
      expect(compose.services?.gateway).toEqual(expect.objectContaining({
        cpus: '1.0',
        mem_limit: '256m',
        pids_limit: 128,
        logging: {
          driver: 'local',
          options: { 'max-file': '3', 'max-size': '10m' },
        },
      }))
    }
    expect(lifecycle.every((request) => request.arguments.includes('--file') && request.arguments.includes('-'))).toBe(true)
  })

  it('parses only exact owned inventory rows and preserves no attacker output', async () => {
    const labels = 'ancestryllm\tancestryllm-local-arm64\tancestryllm-local'
    const outputs = [
      `sha256:${'b'.repeat(64)}\tancestryllm-local-gateway\t${labels}\n`,
      `sha256:${'b'.repeat(64)}\tancestryllm-local-gateway\t${labels}\n`,
      `sha256:${'c'.repeat(64)}\tancestryllm-local-private\t${labels}\n`,
      `sha256:${'c'.repeat(64)}\tancestryllm-local-private\t${labels}\n`,
      `ancestryllm-local-data\t${labels}\n`,
      `ancestryllm-local-data\t${labels}\n`,
    ]
    const requests: HostProcessRequest[] = []
    const run: RunHostProcess = vi.fn(async (request) => {
      requests.push(request)
      return { stdout: outputs.shift() ?? '' }
    })
    const runtimePolicy = policy()
    const control = new DockerCliHostControl({ run })

    await expect(control.inventory(runtimePolicy)).resolves.toEqual([
      { kind: 'container', name: 'ancestryllm-local-gateway', labels: expect.any(Object) },
      { kind: 'network', name: 'ancestryllm-local-private', labels: expect.any(Object) },
      { kind: 'volume', name: 'ancestryllm-local-data', labels: expect.any(Object) },
    ])
    expect(requests).toHaveLength(6)
    expect(requests.filter((request) => request.arguments.includes(
      'label=com.ancestryllm.owner=ancestryllm',
    ))).toHaveLength(3)
    expect(requests.every((request) => request.arguments.some((argument) => (
      argument.includes('{{.Label "com.ancestryllm.owner"}}')
      && argument.includes('{{.Label "com.ancestryllm.profile"}}')
      && argument.includes('{{.Label "com.ancestryllm.project"}}')
    )))).toBe(true)

    vi.mocked(run).mockResolvedValueOnce({ stdout: 'malformed\t/private/canary\n' })
    await expect(control.inventory(runtimePolicy)).rejects.toMatchObject({
      code: 'PROCESS_RESPONSE_INVALID',
    })
  })

  it('rejects an unknown daemon architecture instead of weakening identity checks', async () => {
    const outputs = [
      'colima-ancestryllm-local-arm64|ancestryllm-field|unix:///private/ancestryllm/profiles/ancestryllm-local-arm64/docker.sock\n',
      '29.5.2|ancestryllm-field|1.54\n',
      'engine-ancestryllm-local-arm64|ancestryllm-field|linux|ancestryllm-field|armv8|ancestryllm-field|["cgroupns","seccomp"]\n',
    ]
    const control = new DockerCliHostControl({
      run: vi.fn(async () => ({ stdout: outputs.shift() ?? '' })),
    })

    await expect(control.observe(policy())).rejects.toMatchObject({
      code: 'PROCESS_RESPONSE_INVALID',
    })
  })
})

function processRequest(overrides: Partial<HostProcessRequest> = {}): HostProcessRequest {
  return {
    executablePath: process.execPath,
    arguments: ['-e', 'process.stdout.write("ok")'],
    workingDirectory: process.cwd(),
    environment: {},
    standardInput: undefined,
    timeoutMs: 2000,
    maxInputBytes: 1024,
    maxOutputBytes: 1024,
    ...overrides,
  }
}

describe('bounded host process execution', () => {
  it('returns bounded stdout for a successful no-shell child', async () => {
    await expect(runBoundedHostProcess(processRequest())).resolves.toEqual({ stdout: 'ok' })
  })

  it('fails closed on input overflow, output overflow, timeout, and nonzero exit', async () => {
    const cases: Array<[HostProcessRequest, HostContainerProcessError['code']]> = [
      [processRequest({ standardInput: 'x'.repeat(1025) }), 'PROCESS_INPUT_LIMIT'],
      [processRequest({
        arguments: ['-e', 'process.stdout.write("x".repeat(2048))'],
        maxOutputBytes: 1024,
      }), 'PROCESS_OUTPUT_LIMIT'],
      [processRequest({
        arguments: ['-e', 'setInterval(() => {}, 1000)'], timeoutMs: 20,
      }), 'PROCESS_TIMEOUT'],
      [processRequest({
        arguments: ['-e', 'process.stderr.write("canary /Users/alice/family.ged"); process.exit(7)'],
      }), 'PROCESS_EXIT'],
    ]

    for (const [request, code] of cases) {
      let failure: unknown
      try {
        await runBoundedHostProcess(request)
      } catch (error) {
        failure = error
      }
      expect(failure).toBeInstanceOf(HostContainerProcessError)
      expect(failure).toMatchObject({ code })
      expect(String(failure)).not.toContain('canary')
      expect(String(failure)).not.toContain('/Users/')
    }
  })

  it('rejects inherited, accessor, hidden, and malformed environment entries', () => {
    const inherited = Object.assign(Object.create({ DOCKER_HOST: 'ssh://attacker' }), {
      LANG: 'C.UTF-8',
    }) as NodeJS.ProcessEnv
    const accessor: NodeJS.ProcessEnv = {}
    const read = vi.fn(() => 'canary')
    Object.defineProperty(accessor, 'LANG', { enumerable: true, get: read })
    const hidden: NodeJS.ProcessEnv = {}
    Object.defineProperty(hidden, 'DOCKER_HOST', { value: 'ssh://attacker', enumerable: false })

    for (const environment of [
      inherited,
      accessor,
      hidden,
      { 'BAD\nKEY': 'value' },
      { LANG: 'value\nwith-newline' },
    ]) {
      expect(() => runBoundedHostProcess(processRequest({ environment }))).toThrowError(
        expect.objectContaining({ code: 'PROCESS_REQUEST_INVALID' }),
      )
    }
    expect(read).not.toHaveBeenCalled()
  })
})
