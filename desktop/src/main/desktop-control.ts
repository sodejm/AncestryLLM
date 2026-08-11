/** Builds the main-process bridge for safe app, sidecar, and preferences operations. */
import {
  DESKTOP_PROTOCOL_VERSION,
  type AppInfo,
  type BridgeErrorCode,
  type BridgeResult,
  type CapabilityManifest,
  type PreferenceUpdate,
  type StartupDiagnostics,
  type StartupFailure,
  type StartupState,
} from '../shared-contract/desktop'
import type { SidecarDiagnostics, SidecarLifecycleState } from './sidecar-supervisor'
import { PreferencesConflictError, type PreferencesStore } from './preferences-store'
import type { MainDesktopBridge } from './ipc-handlers'

export { MemoryPreferencesStore, PreferencesConflictError } from './preferences-store'
export type { PreferencesStore } from './preferences-store'

export interface SidecarControlPort {
  diagnostics(): Readonly<SidecarDiagnostics>
  retry(): Promise<boolean>
}

export interface CapabilitiesClient {
  getCapabilities(signal?: AbortSignal): Promise<CapabilityManifest>
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

function safeDiagnostics(value: Readonly<SidecarDiagnostics>): Readonly<StartupDiagnostics> {
  return frozen({
    state: rendererState(value.state),
    failure: value.failure as StartupFailure,
    automaticRestartsRemaining: value.automaticRestartsRemaining,
    manualRetriesRemaining: value.manualRetriesRemaining,
  })
}

export function createDesktopControlBridge(dependencies: Readonly<{
  appInfo: Readonly<AppInfo>
  supervisor: SidecarControlPort
  capabilitiesClient: CapabilitiesClient
  preferences: PreferencesStore
}>): MainDesktopBridge {
  const appInfo = frozen({ ...dependencies.appInfo })
  return frozen({
    async getAppInfo(signal?: AbortSignal) {
      requireActive(signal)
      return success(appInfo)
    },
    async getStartupDiagnostics(signal?: AbortSignal) {
      requireActive(signal)
      return success(safeDiagnostics(dependencies.supervisor.diagnostics()))
    },
    async getCapabilities(signal?: AbortSignal) {
      try {
        requireActive(signal)
        const manifest = await dependencies.capabilitiesClient.getCapabilities(signal)
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
        return success(safeDiagnostics(dependencies.supervisor.diagnostics()))
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
  })
}
