import { contextBridge, ipcRenderer } from 'electron'
import {
  desktopChannels,
  desktopEventChannels,
  type AncestryBridge,
  type ApplicationSettingsPatch,
  type ConsentCreateRequest,
  type ConsentPreviewRequest,
  type ConsentRevokeRequest,
  type FileGrantId,
  type JobEventDelivery,
  type JobEventSubscriptionRequest,
  type JobEventUnsubscriptionRequest,
  type JobRequest,
  type LocalRuntimeApplyRequest,
  type LocalRuntimeRequest,
  type OpenFileGrantRequest,
  type PreferenceUpdate,
  type ProviderEndpointValidationRequest,
  type ProviderProfileCreateRequest,
  type SaveFileGrantRequest,
  type SecretReferenceRequest,
  type SecretSetRequest,
} from '../shared-contract/desktop'
import {
  parseAppInfoResult,
  parseCapabilitiesResult,
  parseConsentCreateRequest,
  parseConsentPreviewRequest,
  parseConsentPreviewResult,
  parseConsentRevokeRequest,
  parseFileGrantId,
  parseFileGrantResult,
  parseFileGrantRevocationResult,
  parseJobEventDelivery,
  parseJobEventSubscriptionRequest,
  parseJobEventSubscriptionResult,
  parseJobEventUnsubscriptionRequest,
  parseJobEventUnsubscriptionResult,
  parseJobListResult,
  parseJobRequest,
  parseJobSnapshotResult,
  parseLocalRuntimeApplyRequest,
  parseLocalRuntimePreviewResult,
  parseLocalRuntimeRequest,
  parseLocalRuntimeResult,
  parseLocalRuntimeStatusResult,
  parseOpenFileGrantRequest,
  parsePreferenceUpdate,
  parsePreferencesResult,
  parseProviderConfigurationResult,
  parseProviderEndpointValidationRequest,
  parseProviderEndpointValidationResult,
  parseProviderProfileCreateRequest,
  parseSaveFileGrantRequest,
  parseSecretReferenceRequest,
  parseSecretSetRequest,
  parseSecretStatusResult,
  parseSettingsPatch,
  parseSettingsResult,
  parseStartupDiagnosticsResult,
} from '../shared-contract/runtime'

const ancestry: AncestryBridge = Object.freeze({
  getAppInfo: async () => parseAppInfoResult(await ipcRenderer.invoke(desktopChannels.getAppInfo)),
  getStartupDiagnostics: async () => parseStartupDiagnosticsResult(await ipcRenderer.invoke(desktopChannels.getStartupDiagnostics)),
  getCapabilities: async () => parseCapabilitiesResult(await ipcRenderer.invoke(desktopChannels.getCapabilities)),
  retrySidecar: async () => parseStartupDiagnosticsResult(await ipcRenderer.invoke(desktopChannels.retrySidecar)),
  getPreferences: async () => parsePreferencesResult(await ipcRenderer.invoke(desktopChannels.getPreferences)),
  updatePreferences: async (update: PreferenceUpdate) => parsePreferencesResult(
    await ipcRenderer.invoke(desktopChannels.updatePreferences, parsePreferenceUpdate(update)),
  ),
  getSettings: async () => parseSettingsResult(await ipcRenderer.invoke(desktopChannels.getSettings)),
  updateSettings: async (update: ApplicationSettingsPatch) => parseSettingsResult(
    await ipcRenderer.invoke(desktopChannels.updateSettings, parseSettingsPatch(update)),
  ),
  getSecretStatus: async (request: SecretReferenceRequest) => parseSecretStatusResult(
    await ipcRenderer.invoke(desktopChannels.getSecretStatus, parseSecretReferenceRequest(request)),
  ),
  setSecret: async (request: SecretSetRequest) => parseSecretStatusResult(
    await ipcRenderer.invoke(desktopChannels.setSecret, parseSecretSetRequest(request)),
  ),
  deleteSecret: async (request: SecretReferenceRequest) => parseSecretStatusResult(
    await ipcRenderer.invoke(desktopChannels.deleteSecret, parseSecretReferenceRequest(request)),
  ),
  getProviderConfiguration: async () => parseProviderConfigurationResult(
    await ipcRenderer.invoke(desktopChannels.getProviderConfiguration),
  ),
  createProviderProfile: async (request: ProviderProfileCreateRequest) => parseProviderConfigurationResult(
    await ipcRenderer.invoke(desktopChannels.createProviderProfile, parseProviderProfileCreateRequest(request)),
  ),
  validateProviderEndpoint: async (request: ProviderEndpointValidationRequest) => parseProviderEndpointValidationResult(
    await ipcRenderer.invoke(
      desktopChannels.validateProviderEndpoint,
      parseProviderEndpointValidationRequest(request),
    ),
  ),
  previewConsent: async (request: ConsentPreviewRequest) => parseConsentPreviewResult(
    await ipcRenderer.invoke(desktopChannels.previewConsent, parseConsentPreviewRequest(request)),
  ),
  createConsent: async (request: ConsentCreateRequest) => parseProviderConfigurationResult(
    await ipcRenderer.invoke(desktopChannels.createConsent, parseConsentCreateRequest(request)),
  ),
  revokeConsent: async (request: ConsentRevokeRequest) => parseProviderConfigurationResult(
    await ipcRenderer.invoke(desktopChannels.revokeConsent, parseConsentRevokeRequest(request)),
  ),
  requestOpenFileGrant: async (request: OpenFileGrantRequest) => parseFileGrantResult(
    await ipcRenderer.invoke(desktopChannels.requestOpenFileGrant, parseOpenFileGrantRequest(request)),
  ),
  requestSaveFileGrant: async (request: SaveFileGrantRequest) => parseFileGrantResult(
    await ipcRenderer.invoke(desktopChannels.requestSaveFileGrant, parseSaveFileGrantRequest(request)),
  ),
  revokeFileGrant: async (grantId: FileGrantId) => parseFileGrantRevocationResult(
    await ipcRenderer.invoke(desktopChannels.revokeFileGrant, parseFileGrantId(grantId)),
  ),
  getLocalRuntimeStatus: async () => parseLocalRuntimeStatusResult(
    await ipcRenderer.invoke(desktopChannels.getLocalRuntimeStatus),
  ),
  previewLocalRuntime: async (request: LocalRuntimeRequest) => parseLocalRuntimePreviewResult(
    await ipcRenderer.invoke(desktopChannels.previewLocalRuntime, parseLocalRuntimeRequest(request)),
  ),
  applyLocalRuntime: async (request: LocalRuntimeApplyRequest) => parseLocalRuntimeResult(
    await ipcRenderer.invoke(desktopChannels.applyLocalRuntime, parseLocalRuntimeApplyRequest(request)),
  ),
  listJobs: async () => parseJobListResult(await ipcRenderer.invoke(desktopChannels.listJobs)),
  getJob: async (request: JobRequest) => parseJobSnapshotResult(
    await ipcRenderer.invoke(desktopChannels.getJob, parseJobRequest(request)),
  ),
  cancelJob: async (request: JobRequest) => parseJobSnapshotResult(
    await ipcRenderer.invoke(desktopChannels.cancelJob, parseJobRequest(request)),
  ),
  subscribeJobEvents: async (request: JobEventSubscriptionRequest) => parseJobEventSubscriptionResult(
    await ipcRenderer.invoke(
      desktopChannels.subscribeJobEvents,
      parseJobEventSubscriptionRequest(request),
    ),
  ),
  unsubscribeJobEvents: async (request: JobEventUnsubscriptionRequest) => parseJobEventUnsubscriptionResult(
    await ipcRenderer.invoke(
      desktopChannels.unsubscribeJobEvents,
      parseJobEventUnsubscriptionRequest(request),
    ),
  ),
  onJobEvent(listener: (delivery: Readonly<JobEventDelivery>) => void) {
    let active = true
    const ipcListener = (_event: unknown, value: unknown) => {
      if (!active) return
      try {
        listener(parseJobEventDelivery(value))
      } catch {
        // Main-process event payloads are untrusted until this boundary validates them.
      }
    }
    ipcRenderer.on(desktopEventChannels.jobEvent, ipcListener)
    return () => {
      if (!active) return
      active = false
      ipcRenderer.removeListener(desktopEventChannels.jobEvent, ipcListener)
    }
  },
})

contextBridge.exposeInMainWorld('ancestry', ancestry)
