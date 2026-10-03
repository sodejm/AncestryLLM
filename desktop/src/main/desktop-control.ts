/** Builds the main-process bridge for safe app, sidecar, and preferences operations. */
import {
  DESKTOP_PROTOCOL_VERSION,
  type AppInfo,
  type ApplicationSettingsPatch,
  type BridgeErrorCode,
  type BridgeResult,
  type ChatSessionCreateRequest,
  type ChatSessionRequest,
  type ChatEvent,
  type ChatStreamCancelRequest,
  type ChatStreamStartRequest,
  type ConsentCreateRequest,
  type ConsentPreviewRequest,
  type ConsentRevokeRequest,
  type JobEvent,
  type JobEventSubscriptionRequest,
  type JobRequest,
  type PreferenceUpdate,
  type ProviderEndpointValidationRequest,
  type ProviderProfileCreateRequest,
  type SecretReferenceRequest,
  type SecretSetRequest,
  type StartupDiagnosticReport,
  type StartupDiagnostics,
  type StartupFailure,
  type StartupState,
} from '../shared-contract/desktop'
import type { SidecarDiagnostics, SidecarLifecycleState } from './sidecar-supervisor'
import { PreferencesConflictError, type PreferencesStore } from './preferences-store'
import type { MainDesktopBridge } from './ipc-handlers'
import {
  SidecarClientError,
  type ChatEventFlowControl,
  type ChatEventStreamRequest,
  type SidecarClient,
} from './sidecar-client'

/**
 * Re-exports the in-memory preference adapter and its optimistic-concurrency error for tests.
 */
export { MemoryPreferencesStore, PreferencesConflictError } from './preferences-store'
/**
 * Re-exports the preference persistence port consumed by the desktop control bridge.
 */
export type { PreferencesStore } from './preferences-store'

/**
 * Defines the narrow supervisor operations exposed to desktop control orchestration.
 */
export interface SidecarControlPort {
  diagnostics(): Readonly<SidecarDiagnostics>
  retry(): Promise<boolean>
}

/**
 * Exposes sidecar-backed desktop operations while reserving local-runtime control for its dedicated adapter.
 */
export type SidecarDesktopBridge = Omit<
  MainDesktopBridge,
  'getLocalRuntimeStatus' | 'previewLocalRuntime' | 'applyLocalRuntime'
>

function requireActive(signal?: AbortSignal): void {
  if (signal?.aborted) throw signal.reason
}

function frozen<T extends object>(value: T): Readonly<T> {
  return Object.freeze(value)
}

function success<T extends object>(data: Readonly<T>): BridgeResult<T> {
  return frozen({ ok: true, protocolVersion: DESKTOP_PROTOCOL_VERSION, data })
}

function failure<T>(code: BridgeErrorCode, message: string, remediation: string): BridgeResult<T> {
  return frozen({
    ok: false,
    protocolVersion: DESKTOP_PROTOCOL_VERSION,
    error: frozen({ code, message, remediation }),
  })
}

function rendererState(state: SidecarLifecycleState): StartupState {
  if (state === 'ready') return 'ready'
  if (state === 'unavailable') return 'degraded'
  if (state === 'stopping' || state === 'stopped') return 'stopped'
  return 'starting'
}

function safeDiagnostics(
  value: Readonly<SidecarDiagnostics>,
  report: Readonly<StartupDiagnosticReport> | null,
): Readonly<StartupDiagnostics> {
  return frozen({
    state: report?.status === 'degraded' ? 'degraded' : rendererState(value.state),
    failure: value.failure as StartupFailure,
    automaticRestartsRemaining: value.automaticRestartsRemaining,
    manualRetriesRemaining: value.manualRetriesRemaining,
    report,
  })
}

const startupMutationBlocked = <T>(): BridgeResult<T> => failure(
  'STARTUP_MUTATION_BLOCKED',
  'Changes are disabled until startup diagnostics pass.',
  'Open Diagnostics, resolve each blocking item, and retry.',
)

function jobFailure<T>(cause: unknown): BridgeResult<T> {
  const reason = cause instanceof SidecarClientError ? cause.reason : null
  if (reason === 'startup_mutation_blocked') return startupMutationBlocked()
  if (reason === 'job_id_invalid') {
    return failure('JOB_ID_INVALID', 'The selected task identifier is invalid.', 'Refresh the task center and try again.')
  }
  if (reason === 'job_not_found') {
    return failure('JOB_NOT_FOUND', 'The selected task is no longer available.', 'Refresh the task center.')
  }
  if (reason === 'job_event_cursor_invalid') {
    return failure('JOB_EVENT_CURSOR_INVALID', 'Task updates could not resume from that point.', 'Refresh the task snapshot and reconnect.')
  }
  if (reason === 'job_event_replay_expired') {
    return failure('JOB_EVENT_REPLAY_EXPIRED', 'Earlier task updates are no longer available.', 'Refresh the task snapshot and reconnect.')
  }
  if (reason === 'job_subscriber_limit') {
    return failure('JOB_SUBSCRIBER_LIMIT', 'The task update limit has been reached.', 'Close another task view and try again.')
  }
  if (reason === 'job_subscription_closed') {
    return failure('JOB_SUBSCRIPTION_CLOSED', 'The task update stream has ended.', 'Refresh the task snapshot and reconnect.')
  }
  if (reason === 'job_event_stream_failed') {
    return failure('JOB_EVENT_STREAM_FAILED', 'Task updates were interrupted.', 'Refresh the task snapshot and reconnect.')
  }
  return failure('JOB_SERVICE_UNAVAILABLE', 'Task information is unavailable.', 'Retry the private service or restart AncestryLLM.')
}

function chatFailure<T>(cause: unknown): BridgeResult<T> {
  const reason = cause instanceof SidecarClientError ? cause.reason : null
  if (reason === 'startup_mutation_blocked') return startupMutationBlocked()
  if (reason === 'chat_session_invalid') {
    return failure('CHAT_SESSION_INVALID', 'The chat session request is invalid.', 'Review the provider, model, purpose, and privacy selections and try again.')
  }
  if (reason === 'chat_session_not_found') {
    return failure('CHAT_SESSION_NOT_FOUND', 'The selected chat session is no longer available.', 'Refresh the conversation and try again.')
  }
  if (reason === 'chat_session_limit') {
    return failure('CHAT_SESSION_LIMIT', 'The active chat session limit has been reached.', 'Close another conversation and try again.')
  }
  if (reason === 'chat_session_busy') {
    return failure('CHAT_SESSION_BUSY', 'The selected chat session is busy.', 'Stop the active response before trying again.')
  }
  if (reason === 'chat_session_service_unavailable') {
    return failure('CHAT_SESSION_SERVICE_UNAVAILABLE', 'Chat sessions are unavailable.', 'Retry the private service or restart AncestryLLM.')
  }
  if (reason === 'chat_stream_not_found') {
    return failure('CHAT_STREAM_NOT_FOUND', 'The selected chat response is no longer available.', 'Start a new response from the current conversation.')
  }
  if (reason === 'chat_stream_cursor_invalid') {
    return failure('CHAT_STREAM_CURSOR_INVALID', 'Chat output could not resume from that point.', 'Start a new response from the current conversation.')
  }
  if (reason === 'chat_stream_replay_expired') {
    return failure('CHAT_STREAM_REPLAY_EXPIRED', 'Earlier chat output is no longer available.', 'Start a new response; the original provider request was not retried.')
  }
  if (reason === 'chat_stream_limit') {
    return failure('CHAT_STREAM_LIMIT', 'The chat streaming limit has been reached.', 'Cancel or finish another response before trying again.')
  }
  return failure('CHAT_STREAM_SERVICE_UNAVAILABLE', 'Chat streaming is unavailable.', 'Retry the private service or restart AncestryLLM.')
}

/**
 * Creates the main-process application bridge and translates sidecar failures into sanitized renderer results.
 *
 * Mutating calls remain blocked until startup diagnostics prove every required component ready.
 */
export function createDesktopControlBridge(dependencies: Readonly<{
  appInfo: Readonly<AppInfo>
  supervisor: SidecarControlPort
  sidecarClient: SidecarClient
  preferences: PreferencesStore
}>): SidecarDesktopBridge {
  const appInfo = frozen({ ...dependencies.appInfo })

  const collectStartupDiagnostics = async (signal?: AbortSignal): Promise<Readonly<StartupDiagnostics>> => {
    requireActive(signal)
    const lifecycle = dependencies.supervisor.diagnostics()
    if (lifecycle.state !== 'ready') return safeDiagnostics(lifecycle, null)
    try {
      const report = await dependencies.sidecarClient.getStartupDiagnostics(signal)
      requireActive(signal)
      return safeDiagnostics(lifecycle, report)
    } catch {
      requireActive(signal)
      return frozen({
        ...safeDiagnostics(lifecycle, null),
        state: 'degraded',
        failure: 'startup_failed',
      })
    }
  }

  const mutationsAllowed = async (signal?: AbortSignal): Promise<boolean> => {
    const diagnostics = await collectStartupDiagnostics(signal)
    return diagnostics.state === 'ready'
      && diagnostics.report?.status === 'ready'
      && diagnostics.report.components.every((component) => !component.blocks_mutations)
  }

  return frozen({
    async getAppInfo(signal?: AbortSignal) {
      requireActive(signal)
      return success(appInfo)
    },
    async getStartupDiagnostics(signal?: AbortSignal) {
      return success(await collectStartupDiagnostics(signal))
    },
    async getCapabilities(signal?: AbortSignal) {
      try {
        requireActive(signal)
        const manifest = await dependencies.sidecarClient.getCapabilities(signal)
        requireActive(signal)
        return success(manifest)
      } catch {
        requireActive(signal)
        const unavailable = dependencies.supervisor.diagnostics().state === 'unavailable'
        return unavailable
          ? failure('SIDECAR_UNAVAILABLE', 'The private service is unavailable.', 'Retry the service or restart AncestryLLM.')
          : failure('SIDECAR_REQUEST_FAILED', 'The private service did not return capabilities.', 'Try again or restart AncestryLLM.')
      }
    },
    async retrySidecar(signal?: AbortSignal) {
      try {
        requireActive(signal)
        await dependencies.supervisor.retry()
        requireActive(signal)
        return success(await collectStartupDiagnostics(signal))
      } catch {
        requireActive(signal)
        return failure('SIDECAR_UNAVAILABLE', 'The private service could not be restarted.', 'Restart AncestryLLM.')
      }
    },
    async getPreferences(signal?: AbortSignal) {
      try {
        requireActive(signal)
        const preferences = await dependencies.preferences.get()
        requireActive(signal)
        return success(preferences)
      } catch {
        requireActive(signal)
        return failure('PREFERENCES_UNAVAILABLE', 'Desktop preferences are unavailable.', 'Restart AncestryLLM.')
      }
    },
    async updatePreferences(update: PreferenceUpdate, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const preferences = await dependencies.preferences.update(update)
        requireActive(signal)
        return success(preferences)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof PreferencesConflictError) {
          return failure('PREFERENCES_CONFLICT', 'Desktop preferences changed before this update.', 'Reload preferences and try again.')
        }
        return failure('PREFERENCES_UNAVAILABLE', 'Desktop preferences could not be updated.', 'Try again or restart AncestryLLM.')
      }
    },
    async getSettings(signal?: AbortSignal) {
      try {
        requireActive(signal)
        const settings = await dependencies.sidecarClient.getSettings(signal)
        requireActive(signal)
        return success(settings)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'settings_invalid') {
          return failure('SETTINGS_INVALID', 'Application settings are invalid.', 'Reload settings and try again.')
        }
        return failure('SETTINGS_UNAVAILABLE', 'Application settings are unavailable.', 'Retry the service or restart AncestryLLM.')
      }
    },
    async updateSettings(update: ApplicationSettingsPatch, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const settings = await dependencies.sidecarClient.updateSettings(update, signal)
        requireActive(signal)
        return success(settings)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'settings_conflict') {
          return failure('SETTINGS_CONFLICT', 'Application settings changed before this update.', 'Reload settings and try again.')
        }
        if (cause instanceof SidecarClientError && cause.reason === 'settings_invalid') {
          return failure('SETTINGS_INVALID', 'The application settings update was invalid.', 'Review the setting and try again.')
        }
        return failure('SETTINGS_UNAVAILABLE', 'Application settings could not be updated.', 'Try again or restart AncestryLLM.')
      }
    },
    async getSecretStatus(request: SecretReferenceRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        const status = await dependencies.sidecarClient.getSecretStatus(request, signal)
        requireActive(signal)
        return success(status)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'secret_invalid') {
          return failure('SECRET_INVALID', 'The secret reference was invalid.', 'Reload settings and try again.')
        }
        return failure('SECRET_STORE_UNAVAILABLE', 'Secret status is unavailable.', 'Unlock the operating-system credential store and try again.')
      }
    },
    async setSecret(request: SecretSetRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const status = await dependencies.sidecarClient.setSecret(request, signal)
        requireActive(signal)
        return success(status)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'secret_environment_managed') {
          return failure('SECRET_ENVIRONMENT_MANAGED', 'This secret is managed by the environment.', 'Change it in the managed environment instead.')
        }
        if (cause instanceof SidecarClientError && cause.reason === 'secret_invalid') {
          return failure('SECRET_INVALID', 'The secret update was invalid.', 'Enter a non-empty value and try again.')
        }
        return failure('SECRET_STORE_UNAVAILABLE', 'The secret could not be saved.', 'Unlock the operating-system credential store and try again.')
      }
    },
    async deleteSecret(request: SecretReferenceRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const status = await dependencies.sidecarClient.deleteSecret(request, signal)
        requireActive(signal)
        return success(status)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'secret_environment_managed') {
          return failure('SECRET_ENVIRONMENT_MANAGED', 'This secret is managed by the environment.', 'Change it in the managed environment instead.')
        }
        if (cause instanceof SidecarClientError && cause.reason === 'secret_invalid') {
          return failure('SECRET_INVALID', 'The secret reference was invalid.', 'Reload settings and try again.')
        }
        return failure('SECRET_STORE_UNAVAILABLE', 'The secret could not be removed.', 'Unlock the operating-system credential store and try again.')
      }
    },
    async getProviderConfiguration(signal?: AbortSignal) {
      try {
        requireActive(signal)
        const configuration = await dependencies.sidecarClient.getProviderConfiguration(signal)
        requireActive(signal)
        return success(configuration)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'provider_configuration_invalid') {
          return failure('PROVIDER_CONFIGURATION_INVALID', 'Provider configuration is invalid.', 'Review the provider settings and try again.')
        }
        return failure('PROVIDER_CONFIGURATION_UNAVAILABLE', 'Provider configuration is unavailable.', 'Retry the private service or restart AncestryLLM.')
      }
    },
    async createProviderProfile(request: ProviderProfileCreateRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const configuration = await dependencies.sidecarClient.createProviderProfile(request, signal)
        requireActive(signal)
        return success(configuration)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'provider_configuration_conflict') {
          return failure('PROVIDER_CONFIGURATION_CONFLICT', 'Provider configuration changed before this profile was saved.', 'Reload provider settings, review them, and try again.')
        }
        if (cause instanceof SidecarClientError && cause.reason === 'endpoint_rejected') {
          return failure('ENDPOINT_REJECTED', 'The provider endpoint is not permitted.', 'Use the reviewed provider endpoint and test it again.')
        }
        if (cause instanceof SidecarClientError && cause.reason === 'provider_configuration_invalid') {
          return failure('PROVIDER_CONFIGURATION_INVALID', 'The provider profile is invalid.', 'Review the profile name, provider, model, and endpoint.')
        }
        return failure('PROVIDER_CONFIGURATION_UNAVAILABLE', 'The provider profile could not be saved.', 'Retry the private service or restart AncestryLLM.')
      }
    },
    async validateProviderEndpoint(request: ProviderEndpointValidationRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const validation = await dependencies.sidecarClient.validateProviderEndpoint(request, signal)
        requireActive(signal)
        return success(validation)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'endpoint_rejected') {
          return failure('ENDPOINT_REJECTED', 'The provider endpoint test was rejected.', 'Use an explicit loopback local endpoint or the reviewed cloud endpoint.')
        }
        return failure('PROVIDER_CONFIGURATION_UNAVAILABLE', 'The provider endpoint could not be tested.', 'Retry the private service and test the endpoint again.')
      }
    },
    async previewConsent(request: ConsentPreviewRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        const preview = await dependencies.sidecarClient.previewConsent(request, signal)
        requireActive(signal)
        return success(preview)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'consent_invalid') {
          return failure('CONSENT_INVALID', 'The consent preview is invalid.', 'Review the selected profile, purpose, data classes, models, and limits.')
        }
        return failure('PROVIDER_CONFIGURATION_UNAVAILABLE', 'The consent preview is unavailable.', 'Reload provider settings and try again.')
      }
    },
    async createConsent(request: ConsentCreateRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const configuration = await dependencies.sidecarClient.createConsent(request, signal)
        requireActive(signal)
        return success(configuration)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'provider_configuration_conflict') {
          return failure('PROVIDER_CONFIGURATION_CONFLICT', 'Provider configuration changed before this consent was saved.', 'Reload provider settings, preview the consent again, and retry.')
        }
        if (cause instanceof SidecarClientError && cause.reason === 'consent_preview_stale') {
          return failure('CONSENT_PREVIEW_STALE', 'The consent preview is no longer current.', 'Preview the consent again before saving it.')
        }
        if (cause instanceof SidecarClientError && cause.reason === 'consent_invalid') {
          return failure('CONSENT_INVALID', 'The consent grant is invalid.', 'Review every consent field and create a fresh preview.')
        }
        return failure('PROVIDER_CONFIGURATION_UNAVAILABLE', 'The consent could not be saved.', 'Reload provider settings and try again.')
      }
    },
    async revokeConsent(request: ConsentRevokeRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const configuration = await dependencies.sidecarClient.revokeConsent(request, signal)
        requireActive(signal)
        return success(configuration)
      } catch (cause) {
        requireActive(signal)
        if (cause instanceof SidecarClientError && cause.reason === 'provider_configuration_conflict') {
          return failure('PROVIDER_CONFIGURATION_CONFLICT', 'Provider configuration changed before this consent was revoked.', 'Reload provider settings and try again.')
        }
        if (cause instanceof SidecarClientError && cause.reason === 'consent_invalid') {
          return failure('CONSENT_INVALID', 'The consent could not be revoked.', 'Reload provider settings and select an active consent.')
        }
        return failure('PROVIDER_CONFIGURATION_UNAVAILABLE', 'The consent could not be revoked.', 'Retry the private service or restart AncestryLLM.')
      }
    },
    async getChatCapability(signal?: AbortSignal) {
      try {
        requireActive(signal)
        const capability = await dependencies.sidecarClient.getChatCapability(signal)
        requireActive(signal)
        return success(capability)
      } catch (cause) {
        requireActive(signal)
        return chatFailure(cause)
      }
    },
    async createChatSession(request: ChatSessionCreateRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const session = await dependencies.sidecarClient.createChatSession(request, signal)
        requireActive(signal)
        return success(session)
      } catch (cause) {
        requireActive(signal)
        return chatFailure(cause)
      }
    },
    async closeChatSession(request: ChatSessionRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        const closure = await dependencies.sidecarClient.closeChatSession(request, signal)
        requireActive(signal)
        return success(closure)
      } catch (cause) {
        requireActive(signal)
        return chatFailure(cause)
      }
    },
    async startChatStream(request: ChatStreamStartRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        if (!await mutationsAllowed(signal)) return startupMutationBlocked()
        const run = await dependencies.sidecarClient.startChatStream(request, signal)
        requireActive(signal)
        return success(run)
      } catch (cause) {
        requireActive(signal)
        return chatFailure(cause)
      }
    },
    async cancelChatStream(request: ChatStreamCancelRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        const run = await dependencies.sidecarClient.cancelChatStream(request, signal)
        requireActive(signal)
        return success(run)
      } catch (cause) {
        requireActive(signal)
        return chatFailure(cause)
      }
    },
    async streamChatEvents(
      request: ChatEventStreamRequest,
      listener: (event: Readonly<ChatEvent>, flow: Readonly<ChatEventFlowControl>) => void,
      signal?: AbortSignal,
    ) {
      requireActive(signal)
      await dependencies.sidecarClient.streamChatEvents(request, listener, signal)
      requireActive(signal)
    },
    async listJobs(signal?: AbortSignal) {
      try {
        requireActive(signal)
        const jobs = await dependencies.sidecarClient.listJobs(signal)
        requireActive(signal)
        return success(jobs)
      } catch (cause) {
        requireActive(signal)
        return jobFailure(cause)
      }
    },
    async getJob(request: JobRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        const job = await dependencies.sidecarClient.getJob(request, signal)
        requireActive(signal)
        return success(job)
      } catch (cause) {
        requireActive(signal)
        return jobFailure(cause)
      }
    },
    async cancelJob(request: JobRequest, signal?: AbortSignal) {
      try {
        requireActive(signal)
        const job = await dependencies.sidecarClient.cancelJob(request, signal)
        requireActive(signal)
        return success(job)
      } catch (cause) {
        requireActive(signal)
        return jobFailure(cause)
      }
    },
    async streamJobEvents(
      request: JobEventSubscriptionRequest,
      listener: (event: Readonly<JobEvent>) => void,
      signal?: AbortSignal,
    ) {
      requireActive(signal)
      await dependencies.sidecarClient.streamJobEvents(request, listener, signal)
      requireActive(signal)
    },
  })
}
