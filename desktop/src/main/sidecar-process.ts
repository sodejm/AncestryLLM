/** Launches, authenticates, probes, and terminates the native sidecar process. */
import { createHmac, timingSafeEqual } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { request } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  execFile,
  spawn,
  type ChildProcessWithoutNullStreams,
  type SpawnOptionsWithoutStdio,
} from 'node:child_process'
import {
  API_CONTRACT,
  type RunningSidecar,
  type SidecarLaunchRequest,
  type SidecarReadyFrame,
} from './sidecar-supervisor'

const MAX_READINESS_BYTES = 1024
const MAX_HEALTH_BYTES = 8192
const TERMINATION_TIMEOUT_MS = 12_000
const FORCE_TERMINATION_TIMEOUT_MS = 1000
const PROCESS_GROUP_POLL_INTERVAL_MS = 50

type WindowsTreeKillExecutor = (
  file: string,
  args: string[],
  options: { windowsHide: boolean },
  callback: (error: Error | null) => void,
) => void

type WindowsTreeTerminator = (pid: number) => Promise<void>
type NativeProcessTerminator = (
  child: ChildProcessWithoutNullStreams,
) => Promise<void>
type WorkingDirectoryRemover = (
  path: string,
  options: { recursive: true; force: true },
) => Promise<void>

export interface PosixProcessGroupController {
  signal: (pid: number, signal: NodeJS.Signals) => void
  exists: (pid: number) => boolean
}

type ProcessKill = (pid: number, signal?: string | number) => boolean

export function createPosixProcessGroupController(
  kill: ProcessKill = process.kill,
): PosixProcessGroupController {
  return {
    signal: (pid, signal) => { kill(-pid, signal) },
    exists: (pid) => {
      try {
        kill(-pid, 0)
        return true
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ESRCH') return false
        throw error
      }
    },
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function waitForProcessGroupExit(
  pid: number,
  controller: PosixProcessGroupController,
  timeoutMs: number,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (controller.exists(pid)) {
    const remaining = deadline - Date.now()
    if (remaining <= 0) return false
    await delay(Math.min(PROCESS_GROUP_POLL_INTERVAL_MS, remaining))
  }
  return true
}

function waitForChildExit(
  child: ChildProcessWithoutNullStreams,
  timeoutMs: number,
): Promise<boolean> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve(true)
  }
  return new Promise((resolve) => {
    const onExit = (): void => {
      clearTimeout(timer)
      resolve(true)
    }
    const timer = setTimeout(() => {
      child.off('exit', onExit)
      resolve(false)
    }, timeoutMs)
    child.once('exit', onExit)
  })
}

const executeWindowsTreeKill: WindowsTreeKillExecutor = (
  file,
  args,
  options,
  callback,
) => {
  execFile(file, args, options, (error) => callback(error))
}

export function terminateWindowsProcessTree(
  pid: number,
  execute: WindowsTreeKillExecutor = executeWindowsTreeKill,
): Promise<void> {
  return new Promise((resolve, reject) => {
    execute(
      'taskkill.exe',
      ['/PID', String(pid), '/T', '/F'],
      { windowsHide: true },
      (error) => error ? reject(error) : resolve(),
    )
  })
}

export async function terminateNativeSidecarProcess(
  child: ChildProcessWithoutNullStreams,
  platform: NodeJS.Platform = process.platform,
  terminateWindowsTree: WindowsTreeTerminator = terminateWindowsProcessTree,
  posixGroup: PosixProcessGroupController = createPosixProcessGroupController(),
  gracefulTimeoutMs: number = TERMINATION_TIMEOUT_MS,
): Promise<void> {
  if (platform === 'win32' && child.pid !== undefined) {
    // The packaged sidecar also owns a kill-on-close Job Object. If its leader
    // already exited, Windows closes that handle and terminates its descendants.
    if (child.exitCode !== null || child.signalCode !== null) return
    try {
      await terminateWindowsTree(child.pid)
    } catch {
      throw new Error('The sidecar process group could not be terminated.')
    }
    if (!(await waitForChildExit(child, FORCE_TERMINATION_TIMEOUT_MS))) {
      throw new Error('The sidecar process group did not terminate.')
    }
    return
  }

  if (platform !== 'win32' && child.pid !== undefined) {
    let groupTerminationCompleted = false
    let groupSurvived = false
    try {
      if (!posixGroup.exists(child.pid)) return
      posixGroup.signal(child.pid, 'SIGTERM')
      if (await waitForProcessGroupExit(child.pid, posixGroup, gracefulTimeoutMs)) return
      posixGroup.signal(child.pid, 'SIGKILL')
      groupSurvived = !(await waitForProcessGroupExit(
        child.pid,
        posixGroup,
        FORCE_TERMINATION_TIMEOUT_MS,
      ))
      groupTerminationCompleted = true
    } catch {
      throw new Error('The sidecar process group could not be terminated.')
    }
    if (groupTerminationCompleted) {
      if (groupSurvived) {
        throw new Error('The sidecar process group did not terminate.')
      }
      return
    }
  }

  if (child.exitCode !== null || child.signalCode !== null) return
  child.kill('SIGTERM')
  const exited = await waitForChildExit(child, gracefulTimeoutMs)
  if (!exited && child.exitCode === null && child.signalCode === null) {
    child.kill('SIGKILL')
    if (!(await waitForChildExit(child, FORCE_TERMINATION_TIMEOUT_MS))) {
      throw new Error('The sidecar process group did not terminate.')
    }
  }
}

export function nativeSidecarSpawnOptions(
  workingDirectory: string,
  environment: NodeJS.ProcessEnv,
  platform: NodeJS.Platform = process.platform,
): SpawnOptionsWithoutStdio & { stdio: ['pipe', 'pipe', 'pipe'] } {
  return {
    cwd: workingDirectory,
    env: environment,
    shell: false,
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
    detached: platform !== 'win32',
  }
}

function parseReadyFrame(line: string): SidecarReadyFrame {
  let value: unknown
  try {
    value = JSON.parse(line)
  } catch {
    throw new Error('The sidecar returned invalid readiness metadata.')
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('The sidecar returned invalid readiness metadata.')
  }
  const record = value as Record<string, unknown>
  if (
    Object.keys(record).sort().join(',') !== 'contract,port,sidecar_build'
    || typeof record.contract !== 'string'
    || typeof record.sidecar_build !== 'string'
    || typeof record.port !== 'number'
  ) {
    throw new Error('The sidecar returned invalid readiness metadata.')
  }
  return {
    contract: record.contract,
    sidecar_build: record.sidecar_build,
    port: record.port,
  }
}

function readiness(child: ChildProcessWithoutNullStreams): Promise<SidecarReadyFrame> {
  return new Promise((resolve, reject) => {
    let settled = false
    let buffered = Buffer.alloc(0)
    const fail = (error: Error): void => {
      if (settled) return
      settled = true
      reject(error)
    }
    child.once('error', () => fail(new Error('The packaged sidecar could not be launched.')))
    child.once('exit', () => fail(new Error('The packaged sidecar stopped before readiness.')))
    child.stdout.on('data', (chunk: Buffer) => {
      if (settled) return
      buffered = Buffer.concat([buffered, chunk])
      if (buffered.length > MAX_READINESS_BYTES) {
        fail(new Error('The sidecar readiness metadata exceeded its limit.'))
        return
      }
      const newline = buffered.indexOf(0x0a)
      if (newline < 0) return
      if (newline !== buffered.length - 1) {
        fail(new Error('The sidecar emitted unexpected startup output.'))
        return
      }
      try {
        const parsed = parseReadyFrame(buffered.subarray(0, newline).toString('utf8'))
        settled = true
        resolve(parsed)
      } catch (error) {
        fail(error instanceof Error ? error : new Error('Invalid sidecar readiness metadata.'))
      }
    })
  })
}

export class NativeRunningSidecar extends EventEmitter implements RunningSidecar {
  readonly ready: Promise<SidecarReadyFrame>
  private termination: Promise<void> | undefined

  constructor(
    private readonly child: ChildProcessWithoutNullStreams,
    private readonly workingDirectory: string,
    private readonly terminateProcess: NativeProcessTerminator = terminateNativeSidecarProcess,
    private readonly removeWorkingDirectory: WorkingDirectoryRemover = rm,
  ) {
    super()
    this.ready = readiness(child)
    child.stderr.resume()
    child.once('exit', (code) => this.emit('exit', code))
  }

  terminate(): Promise<void> {
    if (this.termination) return this.termination
    const termination = this.terminateOnce()
    this.termination = termination
    void termination.catch(() => {
      if (this.termination === termination) this.termination = undefined
    })
    return termination
  }

  private async terminateOnce(): Promise<void> {
    await this.terminateProcess(this.child)
    await this.removeWorkingDirectory(this.workingDirectory, {
      recursive: true,
      force: true,
    })
  }
}

export async function launchNativeSidecar(
  requestDetails: SidecarLaunchRequest,
): Promise<RunningSidecar> {
  const workingDirectory = await mkdtemp(join(tmpdir(), 'ancestryllm-sidecar-'))
  let child: ChildProcessWithoutNullStreams
  try {
    child = spawn(
      requestDetails.executablePath,
      [],
      nativeSidecarSpawnOptions(workingDirectory, requestDetails.environment),
    )
  } catch (error) {
    await rm(workingDirectory, { recursive: true, force: true })
    throw error
  }
  child.stdin.on('error', () => undefined)
  child.stdin.end(requestDetails.launchFrame, 'utf8')
  return new NativeRunningSidecar(child, workingDirectory)
}

interface HealthDocument {
  status: string
  api: { contract?: unknown }
  app_build: string
  sidecar_build: string
  readiness_proof: string
}

function expectedProof(token: string, appBuild: string, sidecarBuild: string): string {
  return createHmac('sha256', token)
    .update(`${API_CONTRACT}\n${appBuild}\n${sidecarBuild}`)
    .digest('hex')
}

export async function probeNativeSidecar(
  ready: SidecarReadyFrame,
  bearerToken: string,
  appBuild: string,
): Promise<void> {
  const body = await new Promise<string>((resolve, reject) => {
    const healthRequest = request({
      host: '127.0.0.1',
      port: ready.port,
      path: '/api/v1/health',
      method: 'GET',
      agent: false,
      headers: {
        Authorization: `Bearer ${bearerToken}`,
        'X-Ancestry-API-Version': API_CONTRACT,
        'X-Ancestry-App-Build': appBuild,
      },
    }, (response) => {
      let total = 0
      const chunks: Buffer[] = []
      response.on('data', (chunk: Buffer) => {
        total += chunk.length
        if (total > MAX_HEALTH_BYTES) {
          response.destroy(new Error('The sidecar health response exceeded its limit.'))
          return
        }
        chunks.push(chunk)
      })
      response.once('error', reject)
      response.once('end', () => {
        if (response.statusCode !== 200) {
          reject(new Error('The sidecar health probe failed.'))
          return
        }
        resolve(Buffer.concat(chunks).toString('utf8'))
      })
    })
    healthRequest.once('error', reject)
    healthRequest.end()
  })

  let health: HealthDocument
  try {
    health = JSON.parse(body) as HealthDocument
  } catch {
    throw new Error('The sidecar health response was invalid.')
  }
  const proof = expectedProof(bearerToken, appBuild, ready.sidecar_build)
  const actualProof = typeof health?.readiness_proof === 'string'
    ? Buffer.from(health.readiness_proof, 'utf8')
    : Buffer.alloc(0)
  const expected = Buffer.from(proof, 'utf8')
  if (
    health?.status !== 'ready'
    || health.api?.contract !== API_CONTRACT
    || health.app_build !== appBuild
    || health.sidecar_build !== ready.sidecar_build
    || actualProof.length !== expected.length
    || !timingSafeEqual(actualProof, expected)
  ) {
    throw new Error('The sidecar readiness proof was invalid.')
  }
}
