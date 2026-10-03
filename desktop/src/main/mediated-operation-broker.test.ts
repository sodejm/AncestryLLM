/** Verifies bounded, path-free mediation across local-container and remote-service adapters. */

import { createHash } from 'node:crypto'
import {
  lstat,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rm,
  writeFile,
} from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type {
  ArtifactGrantRef,
  FileGrant,
  FileReadPurpose,
  FileWritePurpose,
  MediatedOperationRequest,
} from '../shared-contract/desktop'
import {
  FileGrantBroker,
  type NativeFileDialogPort,
} from './file-grant-broker'
import {
  MediatedOperationBroker,
  MediatedOperationBrokerError,
  type LocalMediatedOperationAdapter,
  type RemoteMediatedOperationAdapter,
  type TrustedLocalStagedOutput,
} from './mediated-operation-broker'

const temporaryRoots: string[] = []

async function temporaryRoot(): Promise<string> {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'ancestryllm-mediated-operation-')))
  temporaryRoots.push(root)
  return root
}

function queuedDialogs(openPaths: string[], savePaths: string[]): NativeFileDialogPort {
  return {
    selectOpenFile: vi.fn(async () => openPaths.shift() ?? null),
    selectSaveFile: vi.fn(async () => savePaths.shift() ?? null),
    confirmReplacement: vi.fn(async () => true),
  }
}

async function openGrant(
  broker: FileGrantBroker,
  owner: object,
  purpose: FileReadPurpose,
): Promise<Readonly<FileGrant>> {
  const grant = await broker.requestOpenGrant(owner, { purpose })
  if (grant === null) throw new Error('test dialog unexpectedly cancelled')
  return grant
}

async function saveGrant(
  broker: FileGrantBroker,
  owner: object,
  purpose: FileWritePurpose,
  suggestedName: string,
): Promise<Readonly<FileGrant>> {
  const grant = await broker.requestSaveGrant(owner, { purpose, suggestedName })
  if (grant === null) throw new Error('test dialog unexpectedly cancelled')
  return grant
}

function operationGrant(
  grant: Readonly<FileGrant>,
  operation: string,
  access: 'read' | 'write',
): ArtifactGrantRef {
  return { grant_id: grant.grantId, operation, access }
}

function request(
  operationIdCharacter: string,
  operation: string,
  transport: 'local-container' | 'remote-service',
  inputs: readonly Readonly<FileGrant>[],
  outputs: readonly Readonly<FileGrant>[],
): MediatedOperationRequest {
  return {
    operation_id: `op_${operationIdCharacter.repeat(64)}`,
    operation,
    transport,
    inputs: inputs.map((grant) => operationGrant(grant, operation, 'read')),
    outputs: outputs.map((grant) => operationGrant(grant, operation, 'write')),
  }
}

function unusedLocalAdapter(): LocalMediatedOperationAdapter {
  return { prepare: vi.fn(async () => { throw new Error('unexpected local preparation') }) }
}

function unusedRemoteAdapter(): RemoteMediatedOperationAdapter {
  return { execute: vi.fn(async () => { throw new Error('unexpected remote execution') }) }
}

afterEach(async () => {
  vi.useRealTimers()
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })))
})

describe('mediated operation broker', () => {
  it('keeps RootsMagic immutable while publishing fully validated local export artifacts', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'fictional.rmtree')
    const gedcomTarget = join(root, 'export.ged')
    const reportTarget = join(root, 'export.md')
    const rootsMagic = Buffer.concat([Buffer.from('SQLite format 3\0', 'ascii'), Buffer.alloc(256, 0x4a)])
    const gedcom = '0 HEAD\n1 SOUR ANCESTRYLLM\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n'
    const report = '# RootsMagic export\n\nRecords: 1\nWarnings: 0\n'
    await writeFile(source, rootsMagic)
    const beforeStat = await lstat(source)
    const owner = {}
    const fileGrants = new FileGrantBroker(queuedDialogs([source], [gedcomTarget, reportTarget]))
    const input = await openGrant(fileGrants, owner, 'rootsmagic-read')
    const gedcomOutput = await saveGrant(fileGrants, owner, 'gedcom-write', 'export.ged')
    const reportOutput = await saveGrant(fileGrants, owner, 'markdown-write', 'export.md')
    const local: LocalMediatedOperationAdapter = {
      prepare: vi.fn(async (context) => {
        expect(context.inputs).toHaveLength(1)
        expect(context.inputs[0]).toMatchObject({ purpose: 'rootsmagic-read' })
        expect(context.inputs[0]?.containerPath).toBe(
          `/run/ancestryllm/operations/${context.request.operation_id}/inputs/input-001.rmtree`,
        )
        expect(context.outputs.map((output: TrustedLocalStagedOutput) => output.purpose)).toEqual([
          'gedcom-write',
          'markdown-write',
        ])
        return {
          realizedMounts: context.mountPlan.mounts,
          execute: vi.fn(async () => {
            await writeFile(context.outputs[0]!.hostPath, gedcom, { mode: 0o600 })
            await writeFile(context.outputs[1]!.hostPath, report, { mode: 0o600 })
          }),
          dispose: vi.fn(async () => undefined),
        }
      }),
    }
    const broker = new MediatedOperationBroker({
      runtimeProfileRoot: root,
      fileGrants,
      localAdapter: local,
      remoteAdapter: unusedRemoteAdapter(),
    })
    const progress: unknown[] = []
    const operationRequest = request(
      'a',
      'rootsmagic.export',
      'local-container',
      [input],
      [gedcomOutput, reportOutput],
    )

    const result = await broker.execute(owner, operationRequest, (update) => progress.push(update))

    expect(result.operation_id).toBe(operationRequest.operation_id)
    expect(result.cleanup_status).toBe('complete')
    expect(result.outputs).toEqual([
      {
        artifact_id: expect.stringMatching(/^art_[a-f0-9]{64}$/),
        artifact_type: 'gedcom_export',
        media_type: 'text/vnd.familysearch.gedcom',
        sha256: createHash('sha256').update(gedcom).digest('hex'),
        size_bytes: Buffer.byteLength(gedcom),
        status: 'ready',
      },
      {
        artifact_id: expect.stringMatching(/^art_[a-f0-9]{64}$/),
        artifact_type: 'export_report',
        media_type: 'text/markdown',
        sha256: createHash('sha256').update(report).digest('hex'),
        size_bytes: Buffer.byteLength(report),
        status: 'ready',
      },
    ])
    expect(JSON.stringify(result)).not.toContain(root)
    expect(JSON.stringify(progress)).not.toContain(root)
    expect(progress).toEqual(expect.arrayContaining([
      expect.objectContaining({ phase: 'staging' }),
      expect.objectContaining({ phase: 'executing' }),
      expect.objectContaining({ phase: 'validating' }),
      expect.objectContaining({ phase: 'publishing' }),
      expect.objectContaining({ phase: 'completed' }),
    ]))
    await expect(readFile(source)).resolves.toEqual(rootsMagic)
    const afterStat = await lstat(source)
    expect({ dev: afterStat.dev, ino: afterStat.ino, size: afterStat.size })
      .toEqual({ dev: beforeStat.dev, ino: beforeStat.ino, size: beforeStat.size })
    await expect(readFile(gedcomTarget, 'utf8')).resolves.toBe(gedcom)
    await expect(readFile(reportTarget, 'utf8')).resolves.toBe(report)
    await expect(readdir(join(root, 'operation-staging'))).resolves.toEqual([])
  })

  it('returns committed artifacts with recovery status when staging cleanup fails', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'fictional.ged')
    const target = join(root, 'quality.md')
    const report = '# GEDCOM quality\n\nStatus: OK\n'
    await writeFile(source, '0 HEAD\n0 TRLR\n')
    const owner = {}
    const fileGrants = new FileGrantBroker(queuedDialogs([source], [target]))
    const input = await openGrant(fileGrants, owner, 'gedcom-read')
    const output = await saveGrant(fileGrants, owner, 'markdown-write', 'quality.md')
    const local: LocalMediatedOperationAdapter = {
      prepare: vi.fn(async (context) => ({
        realizedMounts: context.mountPlan.mounts,
        execute: vi.fn(async () => writeFile(context.outputs[0]!.hostPath, report, { mode: 0o600 })),
        dispose: vi.fn(async () => undefined),
      })),
    }
    const removeOperationRoot = vi.fn(async () => {
      throw Object.assign(new Error('locked staging directory'), { code: 'EBUSY' })
    })
    const operationRequest = request(
      'b',
      'gedcom.quality',
      'local-container',
      [input],
      [output],
    )
    const broker = new MediatedOperationBroker({
      runtimeProfileRoot: root,
      fileGrants,
      localAdapter: local,
      remoteAdapter: unusedRemoteAdapter(),
      removeOperationRoot,
    })

    const result = await broker.execute(owner, operationRequest)

    expect(result).toMatchObject({
      operation_id: operationRequest.operation_id,
      cleanup_status: 'recovery-required',
      outputs: [{ status: 'ready' }],
    })
    expect(removeOperationRoot).toHaveBeenCalledWith(
      join(root, 'operation-staging', operationRequest.operation_id),
    )
    await expect(readFile(target, 'utf8')).resolves.toBe(report)
  })

  it('gives the remote adapter bounded streams and never a local path', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'fictional.ged')
    const target = join(root, 'quality.md')
    const gedcom = '0 HEAD\n1 SOUR TEST\n0 @I1@ INDI\n1 NAME Lin /Example/\n0 TRLR\n'
    const report = '# GEDCOM quality\n\nStatus: OK\n'
    await writeFile(source, gedcom)
    const owner = {}
    const fileGrants = new FileGrantBroker(queuedDialogs([source], [target]))
    const input = await openGrant(fileGrants, owner, 'gedcom-read')
    const output = await saveGrant(fileGrants, owner, 'markdown-write', 'quality.md')
    let received = Buffer.alloc(0)
    const remote: RemoteMediatedOperationAdapter = {
      execute: vi.fn(async (context) => {
        const serialized = JSON.stringify(context)
        expect(serialized).not.toContain(root)
        expect(serialized).not.toMatch(/(?:hostPath|containerPath|mountPlan|operationRoot)/)
        const chunks: Buffer[] = []
        for await (const chunk of context.inputs[0]!.read()) chunks.push(Buffer.from(chunk))
        received = Buffer.concat(chunks)
        await context.outputs[0]!.write((async function* () {
          yield Buffer.from(report.slice(0, 5))
          yield Buffer.from(report.slice(5))
        })())
      }),
    }
    const broker = new MediatedOperationBroker({
      runtimeProfileRoot: root,
      fileGrants,
      localAdapter: unusedLocalAdapter(),
      remoteAdapter: remote,
    })

    const result = await broker.execute(owner, request(
      'b',
      'gedcom.quality',
      'remote-service',
      [input],
      [output],
    ))

    expect(received.toString('utf8')).toBe(gedcom)
    expect(result.outputs).toEqual([
      expect.objectContaining({
        artifact_type: 'quality_report',
        media_type: 'text/markdown',
        sha256: createHash('sha256').update(report).digest('hex'),
        size_bytes: Buffer.byteLength(report),
        status: 'ready',
      }),
    ])
    await expect(readFile(target, 'utf8')).resolves.toBe(report)
    await expect(readdir(join(root, 'operation-staging'))).resolves.toEqual([])
  })

  it('validates every staged output before publishing any selected target', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'source.rmtree')
    const gedcomTarget = join(root, 'must-not-publish.ged')
    const reportTarget = join(root, 'must-not-publish.md')
    await writeFile(source, Buffer.concat([Buffer.from('SQLite format 3\0', 'ascii'), Buffer.alloc(64)]))
    const owner = {}
    const fileGrants = new FileGrantBroker(queuedDialogs([source], [gedcomTarget, reportTarget]))
    const input = await openGrant(fileGrants, owner, 'rootsmagic-read')
    const gedcomOutput = await saveGrant(fileGrants, owner, 'gedcom-write', 'must-not-publish.ged')
    const reportOutput = await saveGrant(fileGrants, owner, 'markdown-write', 'must-not-publish.md')
    const local: LocalMediatedOperationAdapter = {
      prepare: vi.fn(async (context) => {
        return {
          realizedMounts: context.mountPlan.mounts,
          execute: vi.fn(async () => {
            await writeFile(context.outputs[0]!.hostPath, '0 HEAD\n0 TRLR\n')
            await writeFile(context.outputs[1]!.hostPath, Buffer.from([0x50, 0x4b, 0x03, 0x04]))
          }),
          dispose: vi.fn(async () => undefined),
        }
      }),
    }
    const broker = new MediatedOperationBroker({
      runtimeProfileRoot: root,
      fileGrants,
      localAdapter: local,
      remoteAdapter: unusedRemoteAdapter(),
    })

    await expect(broker.execute(owner, request(
      'c',
      'rootsmagic.export',
      'local-container',
      [input],
      [gedcomOutput, reportOutput],
    ))).rejects.toMatchObject({ code: 'OUTPUT_INVALID' })

    await expect(lstat(gedcomTarget)).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(lstat(reportTarget)).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(readdir(join(root, 'operation-staging'))).resolves.toEqual([])
  })

  it('rejects extra realized mounts before local execution or publication', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'source.ged')
    const target = join(root, 'must-not-publish.md')
    await writeFile(source, '0 HEAD\n0 TRLR\n')
    const owner = {}
    const fileGrants = new FileGrantBroker(queuedDialogs([source], [target]))
    const input = await openGrant(fileGrants, owner, 'gedcom-read')
    const output = await saveGrant(fileGrants, owner, 'markdown-write', 'must-not-publish.md')
    const execute = vi.fn(async () => {
      await writeFile(target, '# Unsafe\n')
    })
    const dispose = vi.fn(async () => undefined)
    const local: LocalMediatedOperationAdapter = {
      prepare: vi.fn(async (context) => {
        return {
          realizedMounts: [...context.mountPlan.mounts, {
            kind: 'bind',
            source: root,
            target: '/host',
            readOnly: true,
          }],
          execute,
          dispose,
        }
      }),
    }
    const broker = new MediatedOperationBroker({
      runtimeProfileRoot: root,
      fileGrants,
      localAdapter: local,
      remoteAdapter: unusedRemoteAdapter(),
    })

    await expect(broker.execute(owner, request(
      'd',
      'gedcom.quality',
      'local-container',
      [input],
      [output],
    ))).rejects.toMatchObject({
      code: 'MOUNT_MISMATCH',
      message: 'The realized operation mounts were not authorized.',
    })

    await expect(lstat(target)).rejects.toMatchObject({ code: 'ENOENT' })
    expect(execute).not.toHaveBeenCalled()
    expect(dispose).toHaveBeenCalledTimes(1)
    await expect(readdir(join(root, 'operation-staging'))).resolves.toEqual([])
  })

  it('rejects unknown operations and replays before redeeming another capability', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'source.ged')
    const target = join(root, 'quality.md')
    await writeFile(source, '0 HEAD\n0 TRLR\n')
    const owner = {}
    const fileGrants = new FileGrantBroker(queuedDialogs([source], [target]))
    const input = await openGrant(fileGrants, owner, 'gedcom-read')
    const output = await saveGrant(fileGrants, owner, 'markdown-write', 'quality.md')
    const unknown = request('e', 'gedcom.unknown', 'remote-service', [input], [output])
    const remote: RemoteMediatedOperationAdapter = {
      execute: vi.fn(async (context) => {
        await context.outputs[0]!.write((async function* () { yield Buffer.from('# Quality\n') })())
      }),
    }
    const broker = new MediatedOperationBroker({
      runtimeProfileRoot: root,
      fileGrants,
      localAdapter: unusedLocalAdapter(),
      remoteAdapter: remote,
    })

    await expect(broker.execute(owner, unknown)).rejects.toMatchObject({ code: 'INVALID_REQUEST' })
    const valid: MediatedOperationRequest = {
      ...unknown,
      operation: 'gedcom.quality',
      inputs: unknown.inputs.map((grant) => ({ ...grant, operation: 'gedcom.quality' })),
      outputs: unknown.outputs.map((grant) => ({ ...grant, operation: 'gedcom.quality' })),
    }
    await expect(broker.execute(owner, valid)).resolves.toMatchObject({ operation_id: valid.operation_id })
    await expect(broker.execute(owner, valid)).rejects.toMatchObject({
      code: 'OPERATION_REPLAYED',
      message: 'The mediated operation identifier has already been used.',
    })
    expect(remote.execute).toHaveBeenCalledTimes(1)
  })

  it('cancels cooperatively, enforces concurrency, and reports only stable errors', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'source.ged')
    const target = join(root, 'quality.md')
    await writeFile(source, '0 HEAD\n0 TRLR\n')
    const owner = {}
    const fileGrants = new FileGrantBroker(queuedDialogs([source], [target]))
    const input = await openGrant(fileGrants, owner, 'gedcom-read')
    const output = await saveGrant(fileGrants, owner, 'markdown-write', 'quality.md')
    const remote: RemoteMediatedOperationAdapter = {
      execute: vi.fn(async (_context, signal) => new Promise<void>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new Error(`${root}/must-not-leak`)), { once: true })
      })),
    }
    const broker = new MediatedOperationBroker({
      runtimeProfileRoot: root,
      fileGrants,
      localAdapter: unusedLocalAdapter(),
      remoteAdapter: remote,
      maxConcurrent: 1,
    })
    const controller = new AbortController()
    const active = broker.execute(owner, request(
      'f',
      'gedcom.quality',
      'remote-service',
      [input],
      [output],
    ), undefined, controller.signal)
    await vi.waitFor(() => expect(remote.execute).toHaveBeenCalledTimes(1))
    const fabricated = {
      operation_id: `op_${'0'.repeat(64)}`,
      operation: 'gedcom.quality',
      transport: 'remote-service',
      inputs: [{ grant_id: `grt_${'1'.repeat(64)}`, operation: 'gedcom.quality', access: 'read' }],
      outputs: [{ grant_id: `grt_${'2'.repeat(64)}`, operation: 'gedcom.quality', access: 'write' }],
    }
    await expect(broker.execute(owner, fabricated)).rejects.toMatchObject({ code: 'OPERATION_CONFLICT' })

    controller.abort(new Error(`${root}/abort-reason-must-not-leak`))
    const failure = await active.catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(MediatedOperationBrokerError)
    expect(failure).toMatchObject({
      code: 'CANCELLED',
      message: 'The mediated operation was cancelled.',
    })
    expect(JSON.stringify(failure)).not.toContain(root)
    await expect(lstat(target)).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(readdir(join(root, 'operation-staging'))).resolves.toEqual([])
  })

  it('enforces the operation deadline and reports a path-free timeout', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'source.ged')
    const target = join(root, 'quality.md')
    await writeFile(source, '0 HEAD\n0 TRLR\n')
    const owner = {}
    const fileGrants = new FileGrantBroker(queuedDialogs([source], [target]))
    const input = await openGrant(fileGrants, owner, 'gedcom-read')
    const output = await saveGrant(fileGrants, owner, 'markdown-write', 'quality.md')
    const remote: RemoteMediatedOperationAdapter = {
      execute: vi.fn(async (_context, signal) => new Promise<void>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new Error(`${root}/timeout-must-not-leak`)), { once: true })
      })),
    }
    const broker = new MediatedOperationBroker({
      runtimeProfileRoot: root,
      fileGrants,
      localAdapter: unusedLocalAdapter(),
      remoteAdapter: remote,
      maxDurationMs: 50,
    })

    const failure = await broker.execute(owner, request(
      '8',
      'gedcom.quality',
      'remote-service',
      [input],
      [output],
    )).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(MediatedOperationBrokerError)
    expect(failure).toMatchObject({
      code: 'TIMED_OUT',
      message: 'The mediated operation timed out.',
    })
    expect(JSON.stringify(failure)).not.toContain(root)
    await expect(lstat(target)).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(readdir(join(root, 'operation-staging'))).resolves.toEqual([])
  })

  it('releases local resources when an adapter ignores the operation deadline', async () => {
    const root = await temporaryRoot()
    const source = join(root, 'source.ged')
    const target = join(root, 'quality.md')
    await writeFile(source, '0 HEAD\n0 TRLR\n')
    const owner = {}
    const fileGrants = new FileGrantBroker(queuedDialogs([source], [target]))
    const input = await openGrant(fileGrants, owner, 'gedcom-read')
    const output = await saveGrant(fileGrants, owner, 'markdown-write', 'quality.md')
    const dispose = vi.fn(async () => undefined)
    let markExecuteStarted!: () => void
    const executeStarted = new Promise<void>((resolve) => { markExecuteStarted = resolve })
    const local: LocalMediatedOperationAdapter = {
      prepare: vi.fn(async (context) => ({
        realizedMounts: context.mountPlan.mounts,
        execute: vi.fn(() => {
          markExecuteStarted()
          return new Promise<void>(() => undefined)
        }),
        dispose,
      })),
    }
    const broker = new MediatedOperationBroker({
      runtimeProfileRoot: root,
      fileGrants,
      localAdapter: local,
      remoteAdapter: unusedRemoteAdapter(),
      maxDurationMs: 50,
    })

    vi.useFakeTimers()
    const execution = broker.execute(owner, request(
      '9',
      'gedcom.quality',
      'local-container',
      [input],
      [output],
    )).catch((error: unknown) => error)
    await executeStarted
    await vi.advanceTimersByTimeAsync(50)
    const failure = await execution

    expect(failure).toBeInstanceOf(MediatedOperationBrokerError)
    expect(failure).toMatchObject({
      code: 'TIMED_OUT',
      message: 'The mediated operation timed out.',
    })
    expect(dispose).toHaveBeenCalledTimes(1)
    await expect(lstat(target)).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(readdir(join(root, 'operation-staging'))).resolves.toEqual([])
  })
})
