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

class FakeSidecar extends EventEmitter implements RunningSidecar {
  readonly terminate = vi.fn(async () => undefined)
  readonly ready: Promise<{ contract: string; sidecar_build: string; port: number }>

  constructor(ready: Promise<{ contract: string; sidecar_build: string; port: number }>) {
    super()
    this.ready = ready
  }
}

const ready = { contract: API_CONTRACT, sidecar_build: '0.5.0-dev', port: 49152 }

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
      LANG: 'en_US.UTF-8',
    }
    expect(minimalSidecarEnvironment('linux', source)).toEqual({ LANG: 'en_US.UTF-8' })
    expect(minimalSidecarEnvironment('win32', source)).toEqual({ SYSTEMROOT: 'C:\\Windows', TEMP: 'C:\\Temp' })
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
  it('reports sanitized lifecycle transitions and grants sessions only while ready', async () => {
    const sidecar = new FakeSidecar(Promise.resolve(ready))
    const launch = vi.fn(async () => sidecar)
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/private/canary-sidecar', launch,
      probe: async () => undefined, tokenFactory: () => 'S'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 0, maxManualRetries: 1,
    })

    expect(supervisor.diagnostics()).toEqual({
      state: 'idle', failure: null, automaticRestartsRemaining: 0,
      manualRetriesRemaining: 1,
    })
    expect(supervisor.session()).toBeUndefined()

    await supervisor.start()

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
    expect(supervisor.session()).toBeUndefined()
    expect(supervisor.diagnostics().failure).toBe('crash_loop')
  })

  it('keeps the bearer in the private stdin frame and validates readiness', async () => {
    const launch = vi.fn(async () => new FakeSidecar(Promise.resolve(ready)))
    const probe = vi.fn(async () => undefined)
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', launch, probe,
      tokenFactory: () => 'T'.repeat(43), startupTimeoutMs: 100, maxRestarts: 2,
    })

    await supervisor.start()

    expect(launch).toHaveBeenCalledWith(expect.objectContaining({
      executablePath: '/bundle/sidecar',
      launchFrame: `${JSON.stringify({ contract: API_CONTRACT, app_build: '0.5.0-dev', bearer_token: 'T'.repeat(43) })}\n`,
    }))
    expect(probe).toHaveBeenCalledWith(ready, 'T'.repeat(43), '0.5.0-dev')
  })

  it('fails closed immediately on protocol or build mismatch', async () => {
    const sidecar = new FakeSidecar(Promise.resolve({ ...ready, sidecar_build: 'wrong-build' }))
    const launch = vi.fn(async () => sidecar)
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', launch,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 2,
    })

    await expect(supervisor.start()).rejects.toBeInstanceOf(SidecarCompatibilityError)
    expect(launch).toHaveBeenCalledTimes(1)
    expect(sidecar.terminate).toHaveBeenCalledTimes(1)
  })

  it('restarts crashes deterministically and stops after the bounded budget', async () => {
    const processes = Array.from({ length: 3 }, () => new FakeSidecar(Promise.resolve(ready)))
    let nextProcess = 0
    const launch = vi.fn(async () => processes[nextProcess++]!)
    const probe = vi.fn(async () => undefined)
    const fatal = vi.fn()
    const supervisor = new SidecarSupervisor({
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', launch,
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
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', launch,
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
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', launch: async () => sidecar,
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
      appBuild: '0.5.0-dev', executablePath: privatePath,
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
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', launch,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 0, maxManualRetries: 1,
    })
    await expect(supervisor.start()).rejects.toThrow('initial failure')

    const firstRetry = supervisor.retry()
    const concurrentRetry = supervisor.retry()
    expect(launch).toHaveBeenCalledTimes(2)
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
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', launch,
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
      appBuild: '0.5.0-dev', executablePath: '/bundle/sidecar', launch: async () => sidecar,
      probe: async () => undefined, tokenFactory: () => 'T'.repeat(43),
      startupTimeoutMs: 100, maxRestarts: 2,
    })
    await supervisor.start()

    await Promise.all([supervisor.stop(), supervisor.stop()])
    expect(sidecar.terminate).toHaveBeenCalledTimes(1)
  })
})
