/** Launches the native sidecar process, validates readiness frames, and probes authenticated health. */
import { createHmac, timingSafeEqual } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { request } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import {
  API_CONTRACT,
  type RunningSidecar,
  type SidecarLaunchRequest,
  type SidecarReadyFrame,
} from './sidecar-supervisor'

const MAX_READINESS_BYTES = 1024
const MAX_HEALTH_BYTES = 8192
const TERMINATION_TIMEOUT_MS = 3000
const FORCE_TERMINATION_TIMEOUT_MS = 1000

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

class NativeRunningSidecar extends EventEmitter implements RunningSidecar {
  readonly ready: Promise<SidecarReadyFrame>
  private termination: Promise<void> | undefined

  constructor(
    private readonly child: ChildProcessWithoutNullStreams,
    private readonly workingDirectory: string,
  ) {
    super()
    this.ready = readiness(child)
    child.stderr.resume()
    child.once('exit', (code) => this.emit('exit', code))
  }

  terminate(): Promise<void> {
    if (this.termination) return this.termination
    this.termination = this.terminateOnce()
    return this.termination
  }

  private async terminateOnce(): Promise<void> {
    if (this.child.exitCode === null && this.child.signalCode === null) {
      this.child.kill('SIGTERM')
      const exited = await this.waitForExit(TERMINATION_TIMEOUT_MS)
      if (!exited && this.child.exitCode === null && this.child.signalCode === null) {
        this.child.kill('SIGKILL')
        await this.waitForExit(FORCE_TERMINATION_TIMEOUT_MS)
      }
    }
    await rm(this.workingDirectory, { recursive: true, force: true })
  }

  private waitForExit(timeoutMs: number): Promise<boolean> {
    if (this.child.exitCode !== null || this.child.signalCode !== null) {
      return Promise.resolve(true)
    }
    return new Promise((resolve) => {
      const onExit = (): void => {
        clearTimeout(timer)
        resolve(true)
      }
      const timer = setTimeout(() => {
        this.child.off('exit', onExit)
        resolve(false)
      }, timeoutMs)
      this.child.once('exit', onExit)
    })
  }
}

export async function launchNativeSidecar(
  requestDetails: SidecarLaunchRequest,
): Promise<RunningSidecar> {
  const workingDirectory = await mkdtemp(join(tmpdir(), 'ancestryllm-sidecar-'))
  let child: ChildProcessWithoutNullStreams
  try {
    child = spawn(requestDetails.executablePath, [], {
      cwd: workingDirectory,
      env: requestDetails.environment,
      shell: false,
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
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
