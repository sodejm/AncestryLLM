/** Builds the main-process bridge for safe app, sidecar, and preferences operations. */
import {
  DESKTOP_PROTOCOL_VERSION,
  type AppInfo,
  type ApplicationSettingsPatch,
  type BridgeErrorCode,
  type BridgeResult,
  type PreferenceUpdate,
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
import { SidecarClientError, type SidecarClient } from './sidecar-client'

export { MemoryPreferencesStore, PreferencesConflictError } from './preferences-store'
export type { PreferencesStore } from './preferences-store'

export interface SidecarControlPort {
  diagnostics(): Readonly<SidecarDiagnostics>
  retry(): Promise<boolean>
}

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

export function createDesktopControlBridge(dependencies: Readonly<{
  appInfo: Readonly<AppInfo>
  supervisor: SidecarControlPort
  sidecarClient: SidecarClient
  preferences: PreferencesStore
}>): MainDesktopBridge {
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
  })
}
