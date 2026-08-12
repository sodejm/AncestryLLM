/** Parses the bounded non-interactive local-runtime commands owned by Electron Main. */

import {
  DESKTOP_PROTOCOL_VERSION,
  type BridgeResult,
  type LocalRuntimeApplyRequest,
  type LocalRuntimePreview,
  type LocalRuntimeRequest,
  type LocalRuntimeResult,
  type LocalRuntimeStatus,
} from '../shared-contract/desktop'
import {
  parseLocalRuntimeApplyRequest,
  parseLocalRuntimeRequest,
} from '../shared-contract/runtime'
import type { LocalRuntimeControlPort } from './local-runtime-control'

const commandMarker = '--local-runtime'

type LocalRuntimeCliRequest =
  | Readonly<{ command: 'status' }>
  | Readonly<{ command: 'preview'; request: LocalRuntimeRequest }>
  | Readonly<{ command: 'apply'; request: LocalRuntimeApplyRequest }>

const invalidRequest = (): BridgeResult<never> => Object.freeze({
  ok: false,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  error: Object.freeze({
    code: 'INVALID_REQUEST',
    message: 'The local runtime command arguments are invalid.',
    remediation: 'Review the documented local runtime command syntax and try again.',
  }),
})

const internalError = (): BridgeResult<never> => Object.freeze({
  ok: false,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  error: Object.freeze({
    code: 'INTERNAL_ERROR',
    message: 'The local runtime command could not be completed.',
    remediation: 'Try again or collect sanitized local runtime diagnostics.',
  }),
})

const concurrentProcessError = (): BridgeResult<never> => Object.freeze({
  ok: false,
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  error: Object.freeze({
    code: 'BRIDGE_OVERLOADED',
    message: 'Another AncestryLLM process currently owns local runtime access.',
    remediation: 'Wait for that process to finish, then try the command again.',
  }),
})

function commandArguments(arguments_: readonly string[]): readonly string[] {
  const markerIndexes = arguments_.flatMap((value, index) => value === commandMarker ? [index] : [])
  if (markerIndexes.length !== 1) throw new Error('Invalid command marker')
  const markerIndex = markerIndexes[0]
  if (markerIndex === undefined) throw new Error('Missing command marker')
  const prefix = arguments_.slice(0, markerIndex)
  if (prefix.length > 1) throw new Error('Invalid command prefix')
  return arguments_.slice(markerIndex + 1)
}

function parsePreview(arguments_: readonly string[]): LocalRuntimeCliRequest {
  if (arguments_.length < 1 || arguments_.length > 2) throw new Error('Invalid preview command')
  const offline = arguments_.length === 2 && arguments_[1] === '--offline'
  if (arguments_.length === 2 && !offline) throw new Error('Invalid preview flag')
  return Object.freeze({
    command: 'preview',
    request: parseLocalRuntimeRequest({
      schema_version: 1,
      operation: arguments_[0],
      offline,
    }),
  })
}

function parseApply(arguments_: readonly string[]): LocalRuntimeCliRequest {
  const operation = arguments_[0]
  let offline = false
  let planRevision: string | undefined
  let confirmation: string | undefined
  for (let index = 1; index < arguments_.length; index += 1) {
    const flag = arguments_[index]
    if (flag === '--offline') {
      if (offline) throw new Error('Duplicate offline flag')
      offline = true
      continue
    }
    if (flag !== '--plan-revision' && flag !== '--confirm') throw new Error('Invalid apply flag')
    const value = arguments_[index + 1]
    if (value === undefined) throw new Error('Missing apply value')
    index += 1
    if (flag === '--plan-revision') {
      if (planRevision !== undefined) throw new Error('Duplicate plan revision')
      planRevision = value
    } else {
      if (confirmation !== undefined) throw new Error('Duplicate confirmation')
      confirmation = value
    }
  }
  return Object.freeze({
    command: 'apply',
    request: parseLocalRuntimeApplyRequest({
      schema_version: 1,
      operation,
      offline,
      plan_revision: planRevision,
      confirmation,
    }),
  })
}

function parseCommand(arguments_: readonly string[]): LocalRuntimeCliRequest {
  const [command, ...remainder] = commandArguments(arguments_)
  if (command === 'status' && remainder.length === 0) return Object.freeze({ command })
  if (command === 'preview') return parsePreview(remainder)
  if (command === 'apply') return parseApply(remainder)
  throw new Error('Invalid local runtime command')
}

export function isLocalRuntimeCliRequest(arguments_: readonly string[]): boolean {
  return arguments_.includes(commandMarker)
}

export function writeConcurrentLocalRuntimeCliFailure(write: (line: string) => void): number {
  write(`${JSON.stringify(concurrentProcessError())}\n`)
  return 1
}

export async function runLocalRuntimeCli(
  arguments_: readonly string[],
  control: LocalRuntimeControlPort,
  write: (line: string) => void,
): Promise<number> {
  let command: LocalRuntimeCliRequest
  try {
    command = parseCommand(arguments_)
  } catch {
    write(`${JSON.stringify(invalidRequest())}\n`)
    return 2
  }

  let result: BridgeResult<LocalRuntimeStatus | LocalRuntimePreview | LocalRuntimeResult>
  try {
    if (command.command === 'status') {
      result = await control.getLocalRuntimeStatus()
    } else if (command.command === 'preview') {
      result = await control.previewLocalRuntime(command.request)
    } else {
      result = await control.applyLocalRuntime(command.request)
    }
  } catch {
    result = internalError()
  }
  write(`${JSON.stringify(result)}\n`)
  return result.ok ? 0 : 1
}
