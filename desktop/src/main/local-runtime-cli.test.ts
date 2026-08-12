/** Verifies the non-interactive local-runtime command parser and redacted result handling. */

import { describe, expect, it, vi } from 'vitest'
import {
  DESKTOP_PROTOCOL_VERSION,
  type BridgeResult,
  type LocalRuntimePreview,
  type LocalRuntimeResult,
  type LocalRuntimeStatus,
} from '../shared-contract/desktop'
import type { LocalRuntimeControlPort } from './local-runtime-control'
import {
  isLocalRuntimeCliRequest,
  runLocalRuntimeCli,
  writeConcurrentLocalRuntimeCliFailure,
} from './local-runtime-cli'

const statusResult = Object.freeze({
  ok: true,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  data: Object.freeze({ schema_version: 1, state: 'not-installed' }),
}) as unknown as BridgeResult<LocalRuntimeStatus>

const previewResult = Object.freeze({
  ok: true,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  data: Object.freeze({ schema_version: 1, operation: 'repair' }),
}) as unknown as BridgeResult<LocalRuntimePreview>

const applyResult = Object.freeze({
  ok: true,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  data: Object.freeze({
    schema_version: 1,
    operation: 'repair',
    state: 'ready',
    code: 'RUNTIME_REPAIRED',
  }),
}) satisfies BridgeResult<LocalRuntimeResult>

function control(): LocalRuntimeControlPort {
  return {
    getLocalRuntimeStatus: vi.fn(async () => statusResult),
    previewLocalRuntime: vi.fn(async () => previewResult),
    applyLocalRuntime: vi.fn(async () => applyResult),
  }
}

describe('local runtime command interface', () => {
  it('writes one stable sanitized failure when another app process owns the runtime', () => {
    const write = vi.fn()

    expect(writeConcurrentLocalRuntimeCliFailure(write)).toBe(1)
    expect(write).toHaveBeenCalledOnce()
    expect(JSON.parse(write.mock.calls[0]?.[0] as string)).toEqual({
      ok: false,
      protocolVersion: DESKTOP_PROTOCOL_VERSION,
      error: {
        code: 'BRIDGE_OVERLOADED',
        message: 'Another AncestryLLM process currently owns local runtime access.',
        remediation: 'Wait for that process to finish, then try the command again.',
      },
    })
  })

  it('detects the explicit command marker without treating ordinary app arguments as commands', () => {
    expect(isLocalRuntimeCliRequest(['/path/to/app', '--local-runtime', 'status'])).toBe(true)
    expect(isLocalRuntimeCliRequest(['--inspect', 'status'])).toBe(false)
  })

  it('writes one JSON status result and returns success', async () => {
    const port = control()
    const write = vi.fn()

    await expect(runLocalRuntimeCli(
      ['/path/to/app', '--local-runtime', 'status'],
      port,
      write,
    )).resolves.toBe(0)

    expect(port.getLocalRuntimeStatus).toHaveBeenCalledOnce()
    expect(port.previewLocalRuntime).not.toHaveBeenCalled()
    expect(port.applyLocalRuntime).not.toHaveBeenCalled()
    expect(write).toHaveBeenCalledOnce()
    expect(JSON.parse(write.mock.calls[0]?.[0] as string)).toEqual(statusResult)
  })

  it('passes a validated offline preview request to the control boundary', async () => {
    const port = control()
    const write = vi.fn()

    await expect(runLocalRuntimeCli(
      ['--local-runtime', 'preview', 'repair', '--offline'],
      port,
      write,
    )).resolves.toBe(0)

    expect(port.previewLocalRuntime).toHaveBeenCalledWith({
      schema_version: 1,
      operation: 'repair',
      offline: true,
    })
    expect(JSON.parse(write.mock.calls[0]?.[0] as string)).toEqual(previewResult)
  })

  it('passes exact review evidence and confirmation to apply', async () => {
    const port = control()
    const write = vi.fn()
    const revision = 'a'.repeat(64)

    await expect(runLocalRuntimeCli([
      '--local-runtime',
      'apply',
      'repair',
      '--offline',
      '--plan-revision',
      revision,
      '--confirm',
      'REPAIR LOCAL RUNTIME',
    ], port, write)).resolves.toBe(0)

    expect(port.applyLocalRuntime).toHaveBeenCalledWith({
      schema_version: 1,
      operation: 'repair',
      offline: true,
      plan_revision: revision,
      confirmation: 'REPAIR LOCAL RUNTIME',
    })
    expect(JSON.parse(write.mock.calls[0]?.[0] as string)).toEqual(applyResult)
  })

  it.each([
    ['missing command', ['--local-runtime']],
    ['unknown command', ['--local-runtime', 'destroy']],
    ['duplicate offline flag', ['--local-runtime', 'preview', 'setup', '--offline', '--offline']],
    ['unknown flag', ['--local-runtime', 'preview', 'setup', '--token', 'sensitive-value']],
    ['missing review revision', ['--local-runtime', 'apply', 'setup', '--confirm', 'SET UP LOCAL RUNTIME']],
    ['duplicate command marker', ['--local-runtime', 'status', '--local-runtime']],
  ])('rejects %s without invoking the runtime or reflecting arguments', async (_name, arguments_) => {
    const port = control()
    const write = vi.fn()

    await expect(runLocalRuntimeCli(arguments_, port, write)).resolves.toBe(2)

    expect(port.getLocalRuntimeStatus).not.toHaveBeenCalled()
    expect(port.previewLocalRuntime).not.toHaveBeenCalled()
    expect(port.applyLocalRuntime).not.toHaveBeenCalled()
    const output = write.mock.calls[0]?.[0] as string
    expect(JSON.parse(output)).toMatchObject({
      ok: false,
      protocolVersion: DESKTOP_PROTOCOL_VERSION,
      error: { code: 'INVALID_REQUEST' },
    })
    expect(output).not.toContain('sensitive-value')
    expect(write).toHaveBeenCalledOnce()
  })

  it('preserves a control failure as JSON and a nonzero operation exit', async () => {
    const port = control()
    const failure = Object.freeze({
      ok: false,
      protocolVersion: DESKTOP_PROTOCOL_VERSION,
      error: Object.freeze({
        code: 'RUNTIME_HOST_UNSUPPORTED' as const,
        message: 'This host is not supported.',
        remediation: 'Use a supported Apple silicon Mac.',
      }),
    })
    vi.mocked(port.getLocalRuntimeStatus).mockResolvedValueOnce(failure)
    const write = vi.fn()

    await expect(runLocalRuntimeCli(
      ['--local-runtime', 'status'],
      port,
      write,
    )).resolves.toBe(1)
    expect(JSON.parse(write.mock.calls[0]?.[0] as string)).toEqual(failure)
  })
})
