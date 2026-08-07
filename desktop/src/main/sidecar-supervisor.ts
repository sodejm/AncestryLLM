/** Supervises the packaged native sidecar lifecycle, restart policy, and authenticated session state. */
import { randomBytes } from 'node:crypto'
import { join } from 'node:path'

export const API_CONTRACT = 'ancestryllm.internal-api/1'

export interface SidecarReadyFrame {
  contract: string
  sidecar_build: string
  port: number
}

export interface RunningSidecar {
  ready: Promise<SidecarReadyFrame>
  terminate: () => Promise<void>
  once(event: 'exit', listener: (code: number | null) => void): this
}

export interface SidecarLaunchRequest {
  executablePath: string
  environment: NodeJS.ProcessEnv
  launchFrame: string
}

type LaunchSidecar = (request: SidecarLaunchRequest) => Promise<RunningSidecar>
type ProbeSidecar = (
  ready: SidecarReadyFrame,
  bearerToken: string,
  appBuild: string,
) => Promise<void>

interface SidecarSupervisorOptions {
  appBuild: string
  executablePath: string
  launch: LaunchSidecar
  probe: ProbeSidecar
  tokenFactory?: () => string
  startupTimeoutMs: number
  maxRestarts: number
  maxManualRetries?: number
  platform?: NodeJS.Platform
  sourceEnvironment?: NodeJS.ProcessEnv
  onFatal?: (diagnostics: SidecarDiagnostics) => void
}

export type SidecarLifecycleState =
  | 'idle'
  | 'starting'
  | 'ready'
  | 'restarting'
  | 'unavailable'
  | 'stopping'
  | 'stopped'

export type SidecarFailure =
  | 'startup_failed'
  | 'startup_timeout'
  | 'incompatible_build'
  | 'crash_loop'

export interface SidecarDiagnostics {
  state: SidecarLifecycleState
  failure: SidecarFailure | null
  automaticRestartsRemaining: number
  manualRetriesRemaining: number
}

/** Main-process-only capability for a fixed-route internal HTTP client. */
export interface AuthenticatedSidecarSession {
  host: '127.0.0.1'
  port: number
  contract: typeof API_CONTRACT
  appBuild: string
  sidecarBuild: string
  bearerToken: string
}

export class SidecarCompatibilityError extends Error {
  constructor() {
    super('The packaged sidecar is incompatible with this application build.')
    this.name = 'SidecarCompatibilityError'
  }
}

class SidecarTimeoutError extends Error {
  constructor() {
    super('Sidecar readiness timed out.')
    this.name = 'SidecarTimeoutError'
  }
}

export function createLaunchToken(
  randomSource: (size: number) => Buffer = randomBytes,
): string {
  const token = randomSource(32)
  if (token.length !== 32) throw new Error('Launch token source returned the wrong length.')
  return token.toString('base64url')
}

export function minimalSidecarEnvironment(
  platform: NodeJS.Platform,
  source: NodeJS.ProcessEnv,
): NodeJS.ProcessEnv {
  const allowed = platform === 'win32'
    ? ['SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP']
    : ['LANG', 'LC_ALL', 'TMPDIR']
  return Object.fromEntries(
    allowed.flatMap((name) => source[name] === undefined ? [] : [[name, source[name]]]),
  )
}

export function resolveSidecarExecutable(
  resourcesPath: string,
  platform: NodeJS.Platform,
  architecture: string,
): string {
  const target = `${platform}-${architecture}`
  if (!new Set([
    'darwin-arm64',
    'darwin-x64',
    'win32-x64',
    'linux-x64',
  ]).has(target)) {
    throw new Error(`Unsupported desktop target: ${target}`)
  }
  const executable = platform === 'win32' ? 'ancestryllm-sidecar.exe' : 'ancestryllm-sidecar'
  return join(resourcesPath, 'sidecar', target, 'ancestryllm-sidecar', executable)
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new SidecarTimeoutError()),
      timeoutMs,
    )
    void promise.then(
      (value) => { clearTimeout(timer); resolve(value) },
      (error: unknown) => { clearTimeout(timer); reject(error) },
    )
  })
}

function requireCompatible(ready: SidecarReadyFrame, appBuild: string): void {
  if (
    ready.contract !== API_CONTRACT
    || ready.sidecar_build !== appBuild
    || !Number.isInteger(ready.port)
    || ready.port < 1
    || ready.port > 65535
  ) {
    throw new SidecarCompatibilityError()
  }
}

export class SidecarSupervisor {
  private current: RunningSidecar | undefined
  private readonly pending = new Set<RunningSidecar>()
  private readonly terminationRequests = new Map<RunningSidecar, Promise<void>>()
  private activeSession: Readonly<AuthenticatedSidecarSession> | undefined
  private remainingRestarts: number
  private remainingManualRetries: number
  private lifecycleState: SidecarLifecycleState = 'idle'
  private lastFailure: SidecarFailure | null = null
  private stopping = false
  private stopPromise: Promise<void> | undefined
  private manualRetryPromise: Promise<boolean> | undefined

  constructor(private readonly options: SidecarSupervisorOptions) {
    if (!Number.isInteger(options.maxRestarts) || options.maxRestarts < 0) {
      throw new Error('maxRestarts must be a non-negative integer.')
    }
    if (!Number.isFinite(options.startupTimeoutMs) || options.startupTimeoutMs <= 0) {
      throw new Error('startupTimeoutMs must be positive.')
    }
    if (
      options.maxManualRetries !== undefined
      && (!Number.isInteger(options.maxManualRetries) || options.maxManualRetries < 0)
    ) {
      throw new Error('maxManualRetries must be a non-negative integer.')
    }
    this.remainingRestarts = options.maxRestarts
    this.remainingManualRetries = options.maxManualRetries ?? 0
  }

  async start(): Promise<void> {
    if (this.lifecycleState !== 'idle' || this.stopping) {
      throw new Error('Sidecar supervisor cannot start.')
    }
    this.transition('starting', null)
    try {
      await this.launchOne()
    } catch (error) {
      if (error instanceof SidecarCompatibilityError) {
        this.reportUnavailable('incompatible_build')
        throw error
      }
      let lastError = error
      while (this.remainingRestarts > 0 && !this.stopping) {
        this.remainingRestarts -= 1
        try {
          await this.launchOne()
          return
        } catch (retryError) {
          if (retryError instanceof SidecarCompatibilityError) {
            this.reportUnavailable('incompatible_build')
            throw retryError
          }
          lastError = retryError
        }
      }
      if (!this.stopping) this.reportUnavailable(this.classifyStartupFailure(lastError))
      throw lastError
    }
  }

  diagnostics(): Readonly<SidecarDiagnostics> {
    return Object.freeze({
      state: this.lifecycleState,
      failure: this.lastFailure,
      automaticRestartsRemaining: this.remainingRestarts,
      manualRetriesRemaining: this.remainingManualRetries,
    })
  }

  session(): Readonly<AuthenticatedSidecarSession> | undefined {
    return this.activeSession
  }

  retry(): Promise<boolean> {
    if (this.manualRetryPromise) return this.manualRetryPromise
    if (
      this.lifecycleState !== 'unavailable'
      || this.stopping
      || this.remainingManualRetries === 0
    ) {
      return Promise.resolve(false)
    }
    this.remainingManualRetries -= 1
    const retry = this.retryOnce()
    this.manualRetryPromise = retry
    void retry.finally(() => {
      if (this.manualRetryPromise === retry) this.manualRetryPromise = undefined
    })
    return retry
  }

  async stop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise
    this.stopping = true
    this.transition('stopping', null)
    const active = this.current
    this.current = undefined
    this.activeSession = undefined
    const processes = new Set(this.pending)
    if (active) processes.add(active)
    this.stopPromise = Promise.allSettled(
      [...processes].map(async (process) => this.terminateOnce(process)),
    ).then(() => { this.transition('stopped', null) })
    return this.stopPromise
  }

  private async launchOne(): Promise<void> {
    const token = (this.options.tokenFactory ?? createLaunchToken)()
    const launchFrame = `${JSON.stringify({
      contract: API_CONTRACT,
      app_build: this.options.appBuild,
      bearer_token: token,
    })}\n`
    const sidecar = await this.options.launch({
      executablePath: this.options.executablePath,
      environment: minimalSidecarEnvironment(
        this.options.platform ?? process.platform,
        this.options.sourceEnvironment ?? process.env,
      ),
      launchFrame,
    })
    this.pending.add(sidecar)
    try {
      const ready = await withTimeout(sidecar.ready, this.options.startupTimeoutMs)
      requireCompatible(ready, this.options.appBuild)
      await withTimeout(
        this.options.probe(ready, token, this.options.appBuild),
        this.options.startupTimeoutMs,
      )
      if (this.stopping) {
        await this.terminateOnce(sidecar)
        throw new Error('Sidecar supervisor is stopping.')
      }
      this.current = sidecar
      this.activeSession = Object.freeze({
        host: '127.0.0.1',
        port: ready.port,
        contract: API_CONTRACT,
        appBuild: this.options.appBuild,
        sidecarBuild: ready.sidecar_build,
        bearerToken: token,
      })
      this.transition('ready', null)
      sidecar.once('exit', () => { this.handleExit(sidecar) })
    } catch (error) {
      await this.terminateOnce(sidecar)
      throw error
    } finally {
      this.pending.delete(sidecar)
    }
  }

  private handleExit(sidecar: RunningSidecar): void {
    if (this.stopping || this.current !== sidecar) return
    this.current = undefined
    this.activeSession = undefined
    this.transition('restarting', null)
    void this.restartAfterExit()
  }

  private async restartAfterExit(): Promise<void> {
    while (this.remainingRestarts > 0 && !this.stopping) {
      this.remainingRestarts -= 1
      try {
        await this.launchOne()
        return
      } catch (error) {
        if (error instanceof SidecarCompatibilityError) {
          this.reportUnavailable('incompatible_build')
          return
        }
      }
    }
    if (!this.stopping) {
      this.reportUnavailable('crash_loop')
    }
  }

  private async retryOnce(): Promise<boolean> {
    this.transition('starting', null)
    try {
      await this.launchOne()
      return true
    } catch (error) {
      if (this.stopping) return false
      this.reportUnavailable(this.classifyStartupFailure(error))
      return false
    }
  }

  private classifyStartupFailure(error: unknown): SidecarFailure {
    if (error instanceof SidecarCompatibilityError) return 'incompatible_build'
    if (error instanceof SidecarTimeoutError) return 'startup_timeout'
    return 'startup_failed'
  }

  private reportUnavailable(failure: SidecarFailure): void {
    this.activeSession = undefined
    this.transition('unavailable', failure)
    this.options.onFatal?.(this.diagnostics())
  }

  private transition(state: SidecarLifecycleState, failure: SidecarFailure | null): void {
    this.lifecycleState = state
    this.lastFailure = failure
  }

  private terminateOnce(sidecar: RunningSidecar): Promise<void> {
    const existing = this.terminationRequests.get(sidecar)
    if (existing) return existing
    const termination = sidecar.terminate()
    this.terminationRequests.set(sidecar, termination)
    return termination
  }
}
