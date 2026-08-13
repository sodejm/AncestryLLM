import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'
import {
  API_CONTRACT,
  SidecarCompatibilityError,
  SidecarSupervisor,
  createLaunchToken,
  minimalSidecarEnvironment,
  resolveSidecarExecutable,
  type RunningSidecar,
} from './sidecar-supervisor'
import { SidecarIntegrityError, verifySidecarPayload } from './sidecar-integrity'

class FakeSidecar extends EventEmitter implements RunningSidecar {
  readonly terminate = vi.fn(async () => undefined)
  readonly ready: Promise<{ contract: string; sidecar_build: string; port: number }>

  constructor(ready: Promise<{ contract: string; sidecar_build: string; port: number }>) {
    super()
    this.ready = ready
  }
}

const ready = { contract: API_CONTRACT, sidecar_build: '0.5.0-dev', port: 49152 }
const verify = async (): Promise<void> => undefined

describe('sidecar launch boundary', () => {
  it('creates a fresh 256-bit URL-safe launch token', () => {
    const token = createLaunchToken(() => Buffer.alloc(32, 0xff))
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(Buffer.from(token, 'base64url')).toHaveLength(32)
  })

  it('passes only a minimal platform environment without provider credentials', () => {
    const source = {
      PATH: '/unsafe/path', HOME: '/private/home', OPENAI_API_KEY: 'canary-openai',
      ANTHROPIC_API_KEY: 'canary-anthropic', SYSTEMROOT: 'C:\\Windows', TEMP: 'C:\\Temp',
      DBUS_SESSION_BUS_ADDRESS: 'unix:path=/run/user/1000/bus',
      XDG_RUNTIME_DIR: '/run/user/1000', LANG: 'en_US.UTF-8',
    }
    expect(minimalSidecarEnvironment('linux', source)).toEqual({
      DBUS_SESSION_BUS_ADDRESS: 'unix:path=/run/user/1000/bus',
      XDG_RUNTIME_DIR: '/run/user/1000',
      LANG: 'en_US.UTF-8',
    })
    expect(minimalSidecarEnvironment('win32', source)).toEqual({ SYSTEMROOT: 'C:\\Windows', TEMP: 'C:\\Temp' })
  })

  it('adds only disposable Linux keyring directories for the exact verifier marker', () => {
    const source = {
      ANCESTRYLLM_NATIVE_KEYRING_SESSION: '1',
      DBUS_SESSION_BUS_ADDRESS: 'unix:path=/run/user/1000/bus',
      HOME: '/verification/home',
      XDG_CACHE_HOME: '/verification/home/.cache',
      XDG_CONFIG_HOME: '/verification/home/.config',
      XDG_DATA_HOME: '/verification/home/.local/share',
      XDG_RUNTIME_DIR: '/verification/runtime',
      LANG: 'en_US.UTF-8',
      PATH: '/unsafe/path',
      OPENAI_API_KEY: 'canary-openai',
      PYTHON_KEYRING_BACKEND: 'keyrings.alt.file.PlaintextKeyring',
    }

    expect(minimalSidecarEnvironment('linux', source)).toEqual({
      DBUS_SESSION_BUS_ADDRESS: 'unix:path=/run/user/1000/bus',
      HOME: '/verification/home',
      XDG_CACHE_HOME: '/verification/home/.cache',
      XDG_CONFIG_HOME: '/verification/home/.config',
      XDG_DATA_HOME: '/verification/home/.local/share',
      XDG_RUNTIME_DIR: '/verification/runtime',
      LANG: 'en_US.UTF-8',
    })
    expect(minimalSidecarEnvironment('linux', {
      ...source,
      ANCESTRYLLM_NATIVE_KEYRING_SESSION: 'true',
    })).toEqual({
      DBUS_SESSION_BUS_ADDRESS: 'unix:path=/run/user/1000/bus',
      XDG_RUNTIME_DIR: '/verification/runtime',
      LANG: 'en_US.UTF-8',
    })
  })

  it('resolves only supported native bundle targets', () => {
    expect(resolveSidecarExecutable('/app/resources', 'darwin', 'arm64')).toBe(
      '/app/resources/sidecar/darwin-arm64/ancestryllm-sidecar/ancestryllm-sidecar',
    )
    expect(resolveSidecarExecutable('/app/resources', 'win32', 'x64')).toBe(
      '/app/resources/sidecar/win32-x64/ancestryllm-sidecar/ancestryllm-sidecar.exe',
    )
    expect(resolveSidecarExecutable('/app/resources', 'win32', 'arm64')).toBe(
      '/app/resources/sidecar/win32-arm64/ancestryllm-sidecar/ancestryllm-sidecar.exe',
    )
    expect(() => resolveSidecarExecutable('/app/resources', 'linux', 'arm64')).toThrow('Unsupported desktop target')
  })
})

describe('SidecarSupervisor', () => {
  it('verifies the immutable payload before token creation and does not restart integrity failures', async () => {
    const events: string[] = []
    const launch = vi.fn(async () => new FakeSidecar(Promise.resolve(ready)))
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar',
      verify: async () => {
        events.push('verify')
        await verifySidecarPayload({
          targetRoot: '/bundle/missing-sidecar-target',
          expectedManifestSha256: '0'.repeat(64),
          expectedTarget: 'darwin-arm64',
          appBuild: '0.5.0-dev',
        })
      },
      launch,
      probe: async () => undefined,
      tokenFactory: () => { events.push('token'); return 'T'.repeat(43) },
      startupTimeoutMs: 100, maxRestarts: 2,
    })

    await expect(supervisor.start()).rejects.toBeInstanceOf(SidecarIntegrityError)
    expect(events).toEqual(['verify'])
    expect(launch).not.toHaveBeenCalled()
    expect(supervisor.diagnostics()).toEqual({
      state: 'unavailable', failure: 'startup_failed',
      automaticRestartsRemaining: 2, manualRetriesRemaining: 0,
    })
  })

  it('preserves automatic retries while a repaired integrity failure uses the manual budget', async () => {
    const sidecar = new FakeSidecar(Promise.resolve(ready))
    const launch = vi.fn(async () => sidecar)
    const verifier = vi.fn()
      .mockRejectedValueOnce(new SidecarIntegrityError())
      .mockResolvedValueOnce(undefined)
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify: verifier, launch,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 2, maxManualRetries: 1,
    })

    await expect(supervisor.start()).rejects.toBeInstanceOf(SidecarIntegrityError)
    expect(supervisor.diagnostics()).toEqual({
      state: 'unavailable', failure: 'startup_failed',
      automaticRestartsRemaining: 2, manualRetriesRemaining: 1,
    })

    await expect(supervisor.retry()).resolves.toBe(true)
    expect(verifier).toHaveBeenCalledTimes(2)
    expect(launch).toHaveBeenCalledOnce()
    expect(supervisor.diagnostics()).toEqual({
      state: 'ready', failure: null,
      automaticRestartsRemaining: 2, manualRetriesRemaining: 0,
    })
  })

  it('reports sanitized lifecycle transitions and grants sessions only while ready', async () => {
    const sidecar = new FakeSidecar(Promise.resolve(ready))
    const launch = vi.fn(async () => sidecar)
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/private/canary-sidecar', verify, launch,
      probe: async () => undefined, tokenFactory: () => 'S'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 0, maxManualRetries: 1,
    })
    const sessionInvalidated = vi.fn()
    const unsubscribe = supervisor.onSessionInvalidated(sessionInvalidated)

    expect(supervisor.diagnostics()).toEqual({
      state: 'idle', failure: null, automaticRestartsRemaining: 0,
      manualRetriesRemaining: 1,
    })
    expect(supervisor.session()).toBeUndefined()

    await supervisor.start()
    expect(sessionInvalidated).not.toHaveBeenCalled()

    expect(supervisor.diagnostics()).toEqual({
      state: 'ready', failure: null, automaticRestartsRemaining: 0,
      manualRetriesRemaining: 1,
    })
    expect(supervisor.session()).toEqual({
      host: '127.0.0.1', port: ready.port, contract: API_CONTRACT,
      appBuild: '0.5.0-dev', sidecarBuild: '0.5.0-dev',
      bearerToken: 'S'.repeat(43),
    })

    sidecar.emit('exit', 1)
    await vi.waitFor(() => expect(supervisor.diagnostics().state).toBe('unavailable'))
    expect(sessionInvalidated).toHaveBeenCalledOnce()
    expect(supervisor.session()).toBeUndefined()
    expect(supervisor.diagnostics().failure).toBe('crash_loop')
    unsubscribe()
    expect(() => unsubscribe()).not.toThrow()
  })

  it('keeps the bearer in the private stdin frame and validates readiness', async () => {
    const launch = vi.fn(async () => new FakeSidecar(Promise.resolve(ready)))
    const probe = vi.fn(async () => undefined)
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify, launch, probe,
      tokenFactory: () => 'T'.repeat(43), startupTimeoutMs: 100, maxRestarts: 2,
    })

    await supervisor.start()

    expect(launch).toHaveBeenCalledWith(expect.objectContaining({
      executablePath: '/bundle/sidecar',
      launchFrame: `${JSON.stringify({ contract: API_CONTRACT, app_build: '0.5.0-dev', bearer_token: 'T'.repeat(43) })}\n`,
    }))
    expect(probe).toHaveBeenCalledWith(ready, 'T'.repeat(43), '0.5.0-dev')
  })

  it.each([
    ['protocol', { ...ready, contract: 'ancestryllm.sidecar/wrong' }],
    ['build', { ...ready, sidecar_build: 'wrong-build' }],
  ])('fails closed immediately on a manifest-valid %s mismatch without retry', async (_kind, response) => {
    const sidecar = new FakeSidecar(Promise.resolve(response))
    const launch = vi.fn(async () => sidecar)
    const verifier = vi.fn(verify)
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify: verifier, launch,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 2,
    })

    await expect(supervisor.start()).rejects.toBeInstanceOf(SidecarCompatibilityError)
    expect(verifier).toHaveBeenCalledOnce()
    expect(launch).toHaveBeenCalledTimes(1)
    expect(sidecar.terminate).toHaveBeenCalledTimes(1)
    expect(supervisor.diagnostics()).toEqual({
      state: 'unavailable', failure: 'incompatible_build',
      automaticRestartsRemaining: 2, manualRetriesRemaining: 0,
    })
  })

  it('restarts crashes deterministically and stops after the bounded budget', async () => {
    const processes = Array.from({ length: 3 }, () => new FakeSidecar(Promise.resolve(ready)))
    let nextProcess = 0
    const launch = vi.fn(async () => processes[nextProcess++]!)
    const probe = vi.fn(async () => undefined)
    const fatal = vi.fn()
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify, launch,
      probe, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 2, onFatal: fatal,
    })
    await supervisor.start()

    processes[0]?.emit('exit', 1)
    await vi.waitFor(() => expect(probe).toHaveBeenCalledTimes(2))
    processes[1]?.emit('exit', 1)
    await vi.waitFor(() => expect(probe).toHaveBeenCalledTimes(3))
    processes[2]?.emit('exit', 1)
    await vi.waitFor(() => expect(fatal).toHaveBeenCalledTimes(1))
  })

  it('uses the remaining restart budget when a crash replacement fails to launch', async () => {
    const first = new FakeSidecar(Promise.resolve(ready))
    const replacement = new FakeSidecar(Promise.resolve(ready))
    const launch = vi.fn()
      .mockResolvedValueOnce(first)
      .mockRejectedValueOnce(new Error('replacement failed'))
      .mockResolvedValueOnce(replacement)
    const fatal = vi.fn()
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify, launch,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 2, onFatal: fatal,
    })
    await supervisor.start()

    first.emit('exit', 1)

    await vi.waitFor(() => expect(launch).toHaveBeenCalledTimes(3))
    expect(fatal).not.toHaveBeenCalled()
    replacement.emit('exit', 1)
    await vi.waitFor(() => expect(fatal).toHaveBeenCalledTimes(1))
  })

  it('bounds startup time and terminates the stalled process', async () => {
    const sidecar = new FakeSidecar(new Promise(() => undefined))
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify,
      launch: async () => sidecar,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 5, maxRestarts: 0,
    })

    await expect(supervisor.start()).rejects.toThrow('readiness timed out')
    expect(sidecar.terminate).toHaveBeenCalledTimes(1)
  })

  it('keeps failures degraded and diagnostics free of secret process details', async () => {
    const secret = 'secret-token-canary'
    const privatePath = '/private/path/canary-sidecar'
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: privatePath, verify,
      launch: async () => { throw new Error(`${secret} ${privatePath}\nprivate stderr\nstack`) },
      probe: async () => undefined, tokenFactory: () => secret,
      startupTimeoutMs: 100, maxRestarts: 0, maxManualRetries: 1,
    })

    await expect(supervisor.start()).rejects.toThrow()

    const serialized = JSON.stringify(supervisor.diagnostics())
    expect(supervisor.diagnostics()).toEqual({
      state: 'unavailable', failure: 'startup_failed',
      automaticRestartsRemaining: 0, manualRetriesRemaining: 1,
    })
    expect(serialized).not.toContain(secret)
    expect(serialized).not.toContain(privatePath)
    expect(serialized).not.toContain('stderr')
    expect(serialized).not.toContain('stack')
    expect(supervisor.session()).toBeUndefined()
  })

  it('coalesces concurrent manual retries and enforces their lifetime budget', async () => {
    let releaseRetry: (() => void) | undefined
    const retryReady = new Promise<typeof ready>((resolve) => { releaseRetry = () => resolve(ready) })
    const retrySidecar = new FakeSidecar(retryReady)
    const launch = vi.fn()
      .mockRejectedValueOnce(new Error('initial failure'))
      .mockResolvedValueOnce(retrySidecar)
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify, launch,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 0, maxManualRetries: 1,
    })
    await expect(supervisor.start()).rejects.toThrow('initial failure')

    const firstRetry = supervisor.retry()
    const concurrentRetry = supervisor.retry()
    await vi.waitFor(() => expect(launch).toHaveBeenCalledTimes(2))
    expect(supervisor.diagnostics().manualRetriesRemaining).toBe(0)
    releaseRetry?.()
    await expect(Promise.all([firstRetry, concurrentRetry])).resolves.toEqual([true, true])
    expect(launch).toHaveBeenCalledTimes(2)

    retrySidecar.emit('exit', 1)
    await vi.waitFor(() => expect(supervisor.diagnostics().state).toBe('unavailable'))
    await expect(supervisor.retry()).resolves.toBe(false)
    expect(launch).toHaveBeenCalledTimes(2)
  })

  it('cancels an in-flight manual retry during shutdown', async () => {
    const retrySidecar = new FakeSidecar(new Promise(() => undefined))
    const launch = vi.fn()
      .mockRejectedValueOnce(new Error('initial failure'))
      .mockResolvedValueOnce(retrySidecar)
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify, launch,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 10, maxRestarts: 0, maxManualRetries: 1,
    })
    await expect(supervisor.start()).rejects.toThrow('initial failure')

    const retry = supervisor.retry()
    await supervisor.stop()

    await expect(retry).resolves.toBe(false)
    expect(retrySidecar.terminate).toHaveBeenCalledTimes(1)
    expect(supervisor.diagnostics().state).toBe('stopped')
    expect(supervisor.session()).toBeUndefined()
  })

  it('terminates the active sidecar exactly once on app shutdown', async () => {
    const sidecar = new FakeSidecar(Promise.resolve(ready))
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify,
      launch: async () => sidecar,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 2,
    })
    await supervisor.start()

    await Promise.all([supervisor.stop(), supervisor.stop()])
    expect(sidecar.terminate).toHaveBeenCalledTimes(1)
  })

  it('fails shutdown closed when process-tree termination cannot be verified', async () => {
    const privateFailure = '/private/process-tree-cleanup-failure'
    const sidecar = new FakeSidecar(Promise.resolve(ready))
    sidecar.terminate.mockRejectedValueOnce(new Error(privateFailure))
    const fatal = vi.fn()
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify,
      launch: async () => sidecar,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 2, onFatal: fatal,
    })
    await supervisor.start()

    const firstStop = supervisor.stop()
    const concurrentStop = supervisor.stop()
    const results = await Promise.allSettled([firstStop, concurrentStop])

    expect(results).toEqual([
      { status: 'rejected', reason: new Error('Sidecar shutdown failed.') },
      { status: 'rejected', reason: new Error('Sidecar shutdown failed.') },
    ])
    expect(sidecar.terminate).toHaveBeenCalledOnce()
    expect(supervisor.diagnostics()).toEqual({
      state: 'unavailable', failure: 'startup_failed',
      automaticRestartsRemaining: 2, manualRetriesRemaining: 0,
    })
    expect(JSON.stringify(results)).not.toContain(privateFailure)
    expect(JSON.stringify(fatal.mock.calls)).not.toContain(privateFailure)
  })

  it('fails closed without an unhandled restart when crash cleanup fails', async () => {
    const sidecar = new FakeSidecar(Promise.resolve(ready))
    sidecar.terminate.mockRejectedValueOnce(new Error('/private cleanup failure'))
    const launch = vi.fn(async () => sidecar)
    const fatal = vi.fn()
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', verify, launch,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 2, onFatal: fatal,
    })
    await supervisor.start()

    sidecar.emit('exit', 1)

    await vi.waitFor(() => expect(supervisor.diagnostics()).toEqual({
      state: 'unavailable', failure: 'startup_failed',
      automaticRestartsRemaining: 2, manualRetriesRemaining: 0,
    }))
    expect(launch).toHaveBeenCalledOnce()
    expect(JSON.stringify(fatal.mock.calls)).not.toContain('/private cleanup failure')
  })
})
