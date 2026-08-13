import { randomBytes } from 'node:crypto'
import { join, posix } from 'node:path'
import { SidecarIntegrityError } from './sidecar-integrity'

export const API_CONTRACT = 'ancestryllm.internal-api/1'
export const LINUX_KEYRING_VERIFICATION_SWITCH = 'ancestryllm-linux-keyring-verification-root'

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
  verify: () => Promise<void>
  launch: LaunchSidecar
  probe: ProbeSidecar
  tokenFactory?: () => string
  startupTimeoutMs: number
  shutdownTimeoutMs?: number
  maxRestarts: number
  maxManualRetries?: number
  platform?: NodeJS.Platform
  sourceEnvironment?: NodeJS.ProcessEnv
  linuxKeyringVerificationRoot?: string | undefined
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

class SidecarStoppingError extends Error {
  constructor() {
    super('Sidecar supervisor is stopping.')
    this.name = 'SidecarStoppingError'
  }
}

class SidecarShutdownTimeoutError extends Error {
  constructor() {
    super('Sidecar shutdown timed out.')
    this.name = 'SidecarShutdownTimeoutError'
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
  linuxKeyringVerificationRoot?: string,
): NodeJS.ProcessEnv {
  validateLinuxKeyringVerificationRoot(platform, linuxKeyringVerificationRoot)
  const platformAllowed = platform === 'win32'
    ? ['SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP']
    : platform === 'linux'
      ? [
          'LANG',
          'LC_ALL',
          'TMPDIR',
          'DBUS_SESSION_BUS_ADDRESS',
          'XDG_RUNTIME_DIR',
        ]
      : ['LANG', 'LC_ALL', 'TMPDIR']
  const environment = Object.fromEntries(
    platformAllowed.flatMap(
      (name) => source[name] === undefined ? [] : [[name, source[name]]],
    ),
  )
  if (platform === 'linux') {
    environment.PYTHON_KEYRING_BACKEND = 'keyring.backends.SecretService.Keyring'
    if (linuxKeyringVerificationRoot !== undefined) {
      const home = posix.join(linuxKeyringVerificationRoot, 'home')
      environment.HOME = home
      environment.XDG_CACHE_HOME = posix.join(home, '.cache')
      environment.XDG_CONFIG_HOME = posix.join(home, '.config')
      environment.XDG_DATA_HOME = posix.join(home, '.local', 'share')
      environment.XDG_RUNTIME_DIR = posix.join(linuxKeyringVerificationRoot, 'runtime')
    }
  }
  return environment
}

function validateLinuxKeyringVerificationRoot(
  platform: NodeJS.Platform,
  root: string | undefined,
): void {
  if (root === undefined) return
  if (platform !== 'linux') {
    throw new Error('The Linux keyring verification root is supported on Linux only.')
  }
  if (!root || !posix.isAbsolute(root)) {
    throw new Error('The Linux keyring verification root must be an absolute Linux path.')
  }
}

export function resolveSidecarExecutable(
  resourcesPath: string,
  platform: NodeJS.Platform,
  architecture: string,
): string {
  const targetRoot = resolveSidecarTargetRoot(resourcesPath, platform, architecture)
  const executable = platform === 'win32' ? 'ancestryllm-sidecar.exe' : 'ancestryllm-sidecar'
  return join(targetRoot, 'ancestryllm-sidecar', executable)
}

export function resolveSidecarTargetRoot(
  resourcesPath: string,
  platform: NodeJS.Platform,
  architecture: string,
): string {
  const target = `${platform}-${architecture}`
  if (!new Set([
    'darwin-arm64',
    'darwin-x64',
    'win32-arm64',
    'win32-x64',
    'linux-x64',
  ]).has(target)) {
    throw new Error(`Unsupported desktop target: ${target}`)
  }
  return join(resourcesPath, 'sidecar', target)
}

function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  timeoutError: () => Error = () => new SidecarTimeoutError(),
): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(timeoutError()),
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
  private readonly failedTerminations = new Set<RunningSidecar>()
  private activeSession: Readonly<AuthenticatedSidecarSession> | undefined
  private hasExposedAuthenticatedSession = false
  private remainingRestarts: number
  private remainingManualRetries: number
  private lifecycleState: SidecarLifecycleState = 'idle'
  private lastFailure: SidecarFailure | null = null
  private stopping = false
  private stopPromise: Promise<void> | undefined
  private readonly inFlightLaunches = new Set<Promise<void>>()
  private readonly stopRequested: Promise<void>
  private resolveStopRequested: () => void = () => undefined
  private manualRetryPromise: Promise<boolean> | undefined
  private readonly sessionInvalidationListeners = new Set<() => void>()

  constructor(private readonly options: SidecarSupervisorOptions) {
    if (!Number.isInteger(options.maxRestarts) || options.maxRestarts < 0) {
      throw new Error('maxRestarts must be a non-negative integer.')
    }
    if (!Number.isFinite(options.startupTimeoutMs) || options.startupTimeoutMs <= 0) {
      throw new Error('startupTimeoutMs must be positive.')
    }
    if (
      options.shutdownTimeoutMs !== undefined
      && (!Number.isFinite(options.shutdownTimeoutMs) || options.shutdownTimeoutMs <= 0)
    ) {
      throw new Error('shutdownTimeoutMs must be positive.')
    }
    if (
      options.maxManualRetries !== undefined
      && (!Number.isInteger(options.maxManualRetries) || options.maxManualRetries < 0)
    ) {
      throw new Error('maxManualRetries must be a non-negative integer.')
    }
    validateLinuxKeyringVerificationRoot(
      options.platform ?? process.platform,
      options.linuxKeyringVerificationRoot,
    )
    this.remainingRestarts = options.maxRestarts
    this.remainingManualRetries = options.maxManualRetries ?? 0
    this.stopRequested = new Promise((resolve) => {
      this.resolveStopRequested = resolve
    })
  }

  async start(): Promise<void> {
    if (this.lifecycleState !== 'idle' || this.stopping) {
      throw new Error('Sidecar supervisor cannot start.')
    }
    this.transition('starting', null)
    try {
      await this.launchOneTracked()
    } catch (error) {
      if (error instanceof SidecarIntegrityError) {
        this.reportUnavailable('startup_failed')
        throw error
      }
      if (error instanceof SidecarCompatibilityError) {
        this.reportUnavailable('incompatible_build')
        throw error
      }
      let lastError = error
      while (this.remainingRestarts > 0 && !this.stopping) {
        this.remainingRestarts -= 1
        try {
          await this.launchOneTracked()
          return
        } catch (retryError) {
          if (retryError instanceof SidecarIntegrityError) {
            this.reportUnavailable('startup_failed')
            throw retryError
          }
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

  /** True only before this supervisor has ever exposed an authenticated job-capable session. */
  isExplicitSafeEmpty(): boolean {
    return !this.hasExposedAuthenticatedSession
      && this.activeSession === undefined
      && ['idle', 'starting', 'unavailable'].includes(this.lifecycleState)
  }

  /** Notifies when an authenticated session is revoked or replaced, not on first acquisition. */
  onSessionInvalidated(listener: () => void): () => void {
    this.sessionInvalidationListeners.add(listener)
    let subscribed = true
    return () => {
      if (!subscribed) return
      subscribed = false
      this.sessionInvalidationListeners.delete(listener)
    }
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

  stop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise
    this.stopping = true
    this.transition('stopping', null)
    this.resolveStopRequested()
    const active = this.current
    this.current = undefined
    this.setActiveSession(undefined)
    const processes = new Set(this.pending)
    if (active) processes.add(active)
    for (const failed of this.failedTerminations) processes.add(failed)
    const terminations = [...processes].map((process) => this.terminateOnce(process))
    const initialTerminationResults = Promise.allSettled(terminations)
    const launches = [...this.inFlightLaunches]
    const attempt = this.finishStop(launches, initialTerminationResults, processes)
    this.stopPromise = attempt
    void attempt.catch(() => {
      if (this.stopPromise === attempt) this.stopPromise = undefined
    })
    return attempt
  }

  private async launchOne(): Promise<void> {
    await this.cancelOnStop(this.options.verify())
    if (this.stopping) throw new SidecarStoppingError()
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
        this.options.linuxKeyringVerificationRoot,
      ),
      launchFrame,
    })
    this.pending.add(sidecar)
    try {
      if (this.stopping) throw new SidecarStoppingError()
      const ready = await this.cancelOnStop(
        withTimeout(sidecar.ready, this.options.startupTimeoutMs),
      )
      requireCompatible(ready, this.options.appBuild)
      await this.cancelOnStop(
        withTimeout(
          this.options.probe(ready, token, this.options.appBuild),
          this.options.startupTimeoutMs,
        ),
      )
      if (this.stopping) throw new SidecarStoppingError()
      this.current = sidecar
      this.setActiveSession(Object.freeze({
        host: '127.0.0.1',
        port: ready.port,
        contract: API_CONTRACT,
        appBuild: this.options.appBuild,
        sidecarBuild: ready.sidecar_build,
        bearerToken: token,
      }))
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
    this.setActiveSession(undefined)
    this.transition('restarting', null)
    void this.cleanupAndRestartAfterExit(sidecar)
  }

  private async cleanupAndRestartAfterExit(sidecar: RunningSidecar): Promise<void> {
    try {
      await this.terminateOnce(sidecar)
    } catch {
      if (!this.stopping) this.reportUnavailable('startup_failed')
      return
    }
    if (this.stopping) return
    await this.restartAfterExit()
  }

  private async restartAfterExit(): Promise<void> {
    while (this.remainingRestarts > 0 && !this.stopping) {
      this.remainingRestarts -= 1
      try {
        await this.launchOneTracked()
        return
      } catch (error) {
        if (error instanceof SidecarIntegrityError) {
          this.reportUnavailable('startup_failed')
          return
        }
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
      await this.launchOneTracked()
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
    this.setActiveSession(undefined)
    this.transition('unavailable', failure)
    this.options.onFatal?.(this.diagnostics())
  }

  private setActiveSession(
    session: Readonly<AuthenticatedSidecarSession> | undefined,
  ): void {
    if (this.activeSession === session) return
    const previous = this.activeSession
    this.activeSession = session
    if (session) this.hasExposedAuthenticatedSession = true
    if (!previous) return
    for (const listener of [...this.sessionInvalidationListeners]) {
      try {
        listener()
      } catch {
        // Session invalidation must not affect sidecar lifecycle management.
      }
    }
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
    void termination.then(
      () => { this.failedTerminations.delete(sidecar) },
      () => {
        this.failedTerminations.add(sidecar)
        if (this.terminationRequests.get(sidecar) === termination) {
          this.terminationRequests.delete(sidecar)
        }
      },
    )
    return termination
  }

  private launchOneTracked(): Promise<void> {
    const launch = this.launchOne()
    this.inFlightLaunches.add(launch)
    void launch.finally(() => { this.inFlightLaunches.delete(launch) }).catch(() => undefined)
    return launch
  }

  private async finishStop(
    launches: readonly Promise<void>[],
    initialTerminationResults: Promise<readonly PromiseSettledResult<void>[]>,
    initiallyAttempted: ReadonlySet<RunningSidecar>,
  ): Promise<void> {
    try {
      await withTimeout(
        this.drainStop(launches, initialTerminationResults, initiallyAttempted),
        this.options.shutdownTimeoutMs ?? 15_000,
        () => new SidecarShutdownTimeoutError(),
      )
    } catch (error) {
      this.reportUnavailable('startup_failed')
      if (error instanceof SidecarShutdownTimeoutError) throw error
      throw new Error('Sidecar shutdown failed.')
    }
    this.transition('stopped', null)
  }

  private async drainStop(
    launches: readonly Promise<void>[],
    initialTerminationResults: Promise<readonly PromiseSettledResult<void>[]>,
    initiallyAttempted: ReadonlySet<RunningSidecar>,
  ): Promise<void> {
    await Promise.allSettled(launches)
    const lateProcesses = new Set(this.pending)
    if (this.current) lateProcesses.add(this.current)
    for (const process of initiallyAttempted) lateProcesses.delete(process)
    const lateTerminationResults = Promise.allSettled(
      [...lateProcesses].map((process) => this.terminateOnce(process)),
    )
    const [initialResults, lateResults] = await Promise.all([
      initialTerminationResults,
      lateTerminationResults,
    ])
    const terminations = await Promise.allSettled([
      ...new Set(this.terminationRequests.values()),
    ])
    if (
      [...initialResults, ...lateResults, ...terminations]
        .some((result) => result.status === 'rejected')
      || this.failedTerminations.size > 0
    ) {
      throw new Error('Sidecar shutdown failed.')
    }
  }

  private cancelOnStop<T>(operation: Promise<T>): Promise<T> {
    return Promise.race([
      operation,
      this.stopRequested.then(() => { throw new SidecarStoppingError() }),
    ])
  }
}
