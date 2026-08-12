/** Adapts policy-bound macOS runtime lifecycle operations to the desktop bridge contract. */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import {
  DESKTOP_PROTOCOL_VERSION,
  type BridgeErrorCode,
  type BridgeResult,
  type LocalRuntimeApplyRequest,
  type LocalRuntimePreview,
  type LocalRuntimeRequest,
  type LocalRuntimeResult,
  type LocalRuntimeStatus,
} from '../shared-contract/desktop'
import {
  createMacosRuntimeHost,
  MacosArm64RuntimeManager,
  MacosRuntimeError,
} from './macos-arm64-runtime-manager'
import {
  parseMacosArm64RuntimePolicy,
  RuntimePolicyError,
  type MacosArm64RuntimePolicy,
} from './macos-arm64-runtime-policy'

export interface LocalRuntimeManagerPort {
  status(signal?: AbortSignal): Promise<LocalRuntimeStatus>
  preview(request: LocalRuntimeRequest, signal?: AbortSignal): Promise<LocalRuntimePreview>
  apply(request: LocalRuntimeApplyRequest, signal?: AbortSignal): Promise<LocalRuntimeResult>
}

export interface LocalRuntimeControlPort {
  getLocalRuntimeStatus(signal?: AbortSignal): Promise<BridgeResult<LocalRuntimeStatus>>
  previewLocalRuntime(
    request: LocalRuntimeRequest,
    signal?: AbortSignal,
  ): Promise<BridgeResult<LocalRuntimePreview>>
  applyLocalRuntime(
    request: LocalRuntimeApplyRequest,
    signal?: AbortSignal,
  ): Promise<BridgeResult<LocalRuntimeResult>>
}

export interface LocalRuntimeControlOptions {
  readonly policyFilePath: string
  readonly rootDirectory: string
  readonly loadPolicy?: () => Promise<unknown>
  readonly managerFactory?: (policy: MacosArm64RuntimePolicy) => LocalRuntimeManagerPort
}

const remediations: Readonly<Record<BridgeErrorCode, string>> = {
  INVALID_REQUEST: 'Review the request and try again.',
  UNAUTHORIZED_SENDER: 'Reload the AncestryLLM window.',
  INVALID_RESPONSE: 'Restart AncestryLLM.',
  BRIDGE_OVERLOADED: 'Wait for current desktop requests to finish and try again.',
  REQUEST_CANCELLED: 'Retry from the current AncestryLLM window.',
  REQUEST_TIMEOUT: 'Try again or restart AncestryLLM.',
  SIDECAR_UNAVAILABLE: 'Retry the service or restart AncestryLLM.',
  SIDECAR_REQUEST_FAILED: 'Try again or restart AncestryLLM.',
  STARTUP_MUTATION_BLOCKED: 'Open Diagnostics, resolve each blocking item, and retry.',
  PREFERENCES_UNAVAILABLE: 'Restart AncestryLLM.',
  PREFERENCES_CONFLICT: 'Reload current preferences and retry your change.',
  SETTINGS_UNAVAILABLE: 'Restart AncestryLLM.',
  SETTINGS_CONFLICT: 'Reload current settings and retry your change.',
  SETTINGS_INVALID: 'Review the settings and try again.',
  SECRET_STORE_UNAVAILABLE: 'Unlock the operating-system keyring and try again.',
  SECRET_ENVIRONMENT_MANAGED: 'Change the secret in the process environment.',
  SECRET_INVALID: 'Review the secret and try again.',
  PROVIDER_CONFIGURATION_UNAVAILABLE: 'Restart AncestryLLM and try again.',
  PROVIDER_CONFIGURATION_CONFLICT: 'Reload provider settings and retry.',
  PROVIDER_CONFIGURATION_INVALID: 'Review the provider settings and try again.',
  ENDPOINT_REJECTED: 'Choose a local or approved encrypted endpoint and retry.',
  CONSENT_INVALID: 'Review the consent request and try again.',
  CONSENT_PREVIEW_STALE: 'Review a fresh consent preview and try again.',
  FILE_SELECTION_INVALID: 'Choose a supported regular file and try again.',
  FILE_TOO_LARGE: 'Choose a smaller file and try again.',
  FILE_GRANT_FORBIDDEN: 'Select the file again for the requested operation.',
  FILE_GRANT_REVOKED: 'Select the file again and retry the operation.',
  FILE_GRANT_STALE: 'Review and select the file again.',
  FILE_GRANT_CONFLICT: 'Finish or cancel the other operation and try again.',
  FILE_DIALOG_FAILED: 'Try again or restart AncestryLLM.',
  RUNTIME_POLICY_INVALID: 'Install a release containing a valid reviewed runtime policy.',
  RUNTIME_POLICY_SCHEMA_UNSUPPORTED: 'Update AncestryLLM to a release that supports this policy.',
  RUNTIME_REQUEST_INVALID: 'Review the requested operation and try again.',
  RUNTIME_HOST_UNSUPPORTED: 'Use a supported Apple silicon Mac with virtualization and sufficient resources.',
  RUNTIME_PLAN_STALE: 'Review a fresh plan before applying the operation.',
  RUNTIME_CONFIRMATION_REQUIRED: 'Enter the exact confirmation phrase shown in the current plan.',
  RUNTIME_OFFLINE_UNAVAILABLE: 'Connect to the network once, or retry after all reviewed files are cached.',
  RUNTIME_DOWNLOAD_FAILED: 'Check the network and retry; an interrupted download can resume.',
  RUNTIME_ARTIFACT_INTEGRITY: 'Discard the damaged download through Repair and retry.',
  RUNTIME_COMPONENT_INTEGRITY: 'Run Repair to restore reviewed app-owned components.',
  RUNTIME_STORAGE_UNSAFE: 'Remove unsafe links or permissions from app-owned runtime storage and retry.',
  RUNTIME_NOT_INSTALLED: 'Set up the local runtime before using this operation.',
  RUNTIME_OWNERSHIP_INVALID: 'Run Repair or remove only the app-owned runtime after reviewing the warning.',
  RUNTIME_PROCESS_FAILED: 'Retry the operation or collect sanitized runtime diagnostics.',
  RUNTIME_HEALTH_FAILED: 'Run Repair, then retry the operation.',
  INTERNAL_ERROR: 'Try again or restart AncestryLLM.',
}

function success<T>(data: Readonly<T>): BridgeResult<T> {
  return Object.freeze({ ok: true, protocolVersion: DESKTOP_PROTOCOL_VERSION, data })
}

function failure<T>(code: BridgeErrorCode, message: string): BridgeResult<T> {
  return Object.freeze({
    ok: false,
    protocolVersion: DESKTOP_PROTOCOL_VERSION,
    error: Object.freeze({ code, message, remediation: remediations[code] }),
  })
}

function requireActive(signal?: AbortSignal): void {
  if (signal?.aborted) throw signal.reason
}

function runtimeFailure<T>(cause: unknown): BridgeResult<T> {
  if (cause instanceof MacosRuntimeError) {
    return failure(cause.code, cause.message)
  }
  if (cause instanceof RuntimePolicyError) {
    if (cause.code === 'RUNTIME_POLICY_SCHEMA_UNSUPPORTED') {
      return failure(cause.code, 'The local runtime policy schema is not supported.')
    }
    if (cause.code === 'RUNTIME_ARCHIVE_OUTPUT_UNSAFE' || cause.code === 'RUNTIME_ARCHIVE_UNSAFE_MEMBER') {
      return failure('RUNTIME_STORAGE_UNSAFE', 'A reviewed archive violated the local runtime storage boundary.')
    }
    if (cause.code === 'RUNTIME_ARCHIVE_INVALID' || cause.code === 'RUNTIME_ARCHIVE_MEMBER_INTEGRITY') {
      return failure('RUNTIME_ARTIFACT_INTEGRITY', 'A reviewed local runtime archive failed integrity verification.')
    }
    return failure('RUNTIME_POLICY_INVALID', 'The reviewed local runtime policy is invalid.')
  }
  return failure('INTERNAL_ERROR', 'The local runtime operation could not be completed.')
}

export function createLocalRuntimeControl(options: LocalRuntimeControlOptions): LocalRuntimeControlPort {
  let managerPromise: Promise<LocalRuntimeManagerPort> | undefined
  const manager = (): Promise<LocalRuntimeManagerPort> => {
    managerPromise ??= (async () => {
      const source = options.loadPolicy === undefined
        ? JSON.parse(await readFile(options.policyFilePath, 'utf8')) as unknown
        : await options.loadPolicy()
      const policy = parseMacosArm64RuntimePolicy(source)
      return options.managerFactory?.(policy) ?? new MacosArm64RuntimeManager({
        rootDirectory: options.rootDirectory,
        policy,
        host: createMacosRuntimeHost(options.rootDirectory),
      })
    })()
    return managerPromise
  }

  const perform = async <T>(
    operation: (runtimeManager: LocalRuntimeManagerPort) => Promise<T>,
    signal?: AbortSignal,
  ): Promise<BridgeResult<T>> => {
    try {
      requireActive(signal)
      const value = await operation(await manager())
      requireActive(signal)
      return success(Object.freeze(value))
    } catch (cause) {
      requireActive(signal)
      return runtimeFailure(cause)
    }
  }

  return Object.freeze({
    getLocalRuntimeStatus: (signal?: AbortSignal) => perform((value) => value.status(signal), signal),
    previewLocalRuntime: (request: LocalRuntimeRequest, signal?: AbortSignal) => (
      perform((value) => value.preview(request, signal), signal)
    ),
    applyLocalRuntime: (request: LocalRuntimeApplyRequest, signal?: AbortSignal) => (
      perform((value) => value.apply(request, signal), signal)
    ),
  })
}

export function createPackagedLocalRuntimeControl(
  resourcesDirectory: string,
  userDataDirectory: string,
): LocalRuntimeControlPort {
  const rootDirectory = join(userDataDirectory, 'local-runtime', 'macos-arm64-runtime')
  return createLocalRuntimeControl({
    policyFilePath: join(resourcesDirectory, 'runtime-policy', 'macos-arm64-runtime-policy-v1.json'),
    rootDirectory,
  })
}
