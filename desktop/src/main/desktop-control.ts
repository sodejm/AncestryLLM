/** Builds the main-process desktop control bridge that exposes safe app, sidecar, and preferences operations. */
import {
  DESKTOP_PROTOCOL_VERSION,
  type AncestryBridge,
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

export { MemoryPreferencesStore, PreferencesConflictError } from './preferences-store'
export type { PreferencesStore } from './preferences-store'

export interface SidecarControlPort {
  diagnostics(): Readonly<SidecarDiagnostics>
  retry(): Promise<boolean>
}

export interface CapabilitiesClient {
  getCapabilities(): Promise<CapabilityManifest>
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
}>): AncestryBridge {
  const appInfo = frozen({ ...dependencies.appInfo })
  return frozen({
    async getAppInfo() {
      return success(appInfo)
    },
    async getStartupDiagnostics() {
      return success(safeDiagnostics(dependencies.supervisor.diagnostics()))
    },
    async getCapabilities() {
      try {
        return success(await dependencies.capabilitiesClient.getCapabilities())
      } catch {
        const unavailable = dependencies.supervisor.diagnostics().state === 'unavailable'
        return unavailable
          ? failure('SIDECAR_UNAVAILABLE', 'The private service is unavailable.', 'Retry the service or restart AncestryLLM.')
          : failure('SIDECAR_REQUEST_FAILED', 'The private service did not return capabilities.', 'Try again or restart AncestryLLM.')
      }
    },
    async retrySidecar() {
      try {
        await dependencies.supervisor.retry()
        return success(safeDiagnostics(dependencies.supervisor.diagnostics()))
      } catch {
        return failure('SIDECAR_UNAVAILABLE', 'The private service could not be restarted.', 'Restart AncestryLLM.')
      }
    },
    async getPreferences() {
      try {
        return success(await dependencies.preferences.get())
      } catch {
        return failure('PREFERENCES_UNAVAILABLE', 'Desktop preferences are unavailable.', 'Restart AncestryLLM.')
      }
    },
    async updatePreferences(update: PreferenceUpdate) {
      try {
        return success(await dependencies.preferences.update(update))
      } catch (cause) {
        if (cause instanceof PreferencesConflictError) {
          return failure('PREFERENCES_CONFLICT', 'Desktop preferences changed before this update.', 'Reload preferences and try again.')
        }
        return failure('PREFERENCES_UNAVAILABLE', 'Desktop preferences could not be updated.', 'Try again or restart AncestryLLM.')
      }
    },
  })
}
