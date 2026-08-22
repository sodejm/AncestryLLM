/** Exposes the narrow versioned Electron bridge through the isolated preload world. */
import { contextBridge, ipcRenderer } from 'electron'
import {
  desktopChannels,
  desktopEventChannels,
  type AncestryBridge,
  type ApplicationSettingsPatch,
  type ChatEventDelivery,
  type ChatSessionCreateRequest,
  type ChatSessionRequest,
  type ChatStreamAckRequest,
  type ChatStreamCancelRequest,
  type ChatStreamStartRequest,
  type ConsentCreateRequest,
  type ConsentPreviewRequest,
  type ConsentRevokeRequest,
  type CopyTextRequest,
  type FileGrantId,
  type JobEventDelivery,
  type JobEventSubscriptionRequest,
  type JobEventUnsubscriptionRequest,
  type JobRequest,
  type LocalRuntimeApplyRequest,
  type LocalRuntimeRequest,
  type OpenExternalLinkRequest,
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
  parseChatCapabilityResult,
  parseChatEventDelivery,
  parseChatSessionClosureResult,
  parseChatSessionCreateRequest,
  parseChatSessionRequest,
  parseChatSessionResult,
  parseChatStreamAcknowledgementResult,
  parseChatStreamAckRequest,
  parseChatStreamCancelRequest,
  parseChatStreamRunResult,
  parseChatStreamStartRequest,
  parseConsentCreateRequest,
  parseConsentPreviewRequest,
  parseConsentPreviewResult,
  parseConsentRevokeRequest,
  parseCopyTextRequest,
  parseCopyTextResult,
  parseClearDiagnosticsResult,
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
  parseOpenExternalLinkRequest,
  parseOpenExternalLinkResult,
  parseOpenDiagnosticsDirectoryResult,
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
  openExternalLink: async (request: OpenExternalLinkRequest) => parseOpenExternalLinkResult(
    await ipcRenderer.invoke(
      desktopChannels.openExternalLink,
      parseOpenExternalLinkRequest(request),
    ),
  ),
  copyText: async (request: CopyTextRequest) => parseCopyTextResult(
    await ipcRenderer.invoke(desktopChannels.copyText, parseCopyTextRequest(request)),
  ),
  openDiagnosticsDirectory: async () => parseOpenDiagnosticsDirectoryResult(
    await ipcRenderer.invoke(desktopChannels.openDiagnosticsDirectory),
  ),
  clearDiagnostics: async () => parseClearDiagnosticsResult(
    await ipcRenderer.invoke(desktopChannels.clearDiagnostics),
  ),
  listJobs: async () => parseJobListResult(await ipcRenderer.invoke(desktopChannels.listJobs)),
  getJob: async (request: JobRequest) => parseJobSnapshotResult(
    await ipcRenderer.invoke(desktopChannels.getJob, parseJobRequest(request)),
  ),
  cancelJob: async (request: JobRequest) => parseJobSnapshotResult(
    await ipcRenderer.invoke(desktopChannels.cancelJob, parseJobRequest(request)),
  ),
  getChatCapability: async () => parseChatCapabilityResult(
    await ipcRenderer.invoke(desktopChannels.getChatCapability),
  ),
  createChatSession: async (request: ChatSessionCreateRequest) => parseChatSessionResult(
    await ipcRenderer.invoke(
      desktopChannels.createChatSession,
      parseChatSessionCreateRequest(request),
    ),
  ),
  closeChatSession: async (request: ChatSessionRequest) => parseChatSessionClosureResult(
    await ipcRenderer.invoke(
      desktopChannels.closeChatSession,
      parseChatSessionRequest(request),
    ),
  ),
  startChatStream: async (request: ChatStreamStartRequest) => parseChatStreamRunResult(
    await ipcRenderer.invoke(
      desktopChannels.startChatStream,
      parseChatStreamStartRequest(request),
    ),
  ),
  cancelChatStream: async (request: ChatStreamCancelRequest) => parseChatStreamRunResult(
    await ipcRenderer.invoke(
      desktopChannels.cancelChatStream,
      parseChatStreamCancelRequest(request),
    ),
  ),
  acknowledgeChatStream: async (request: ChatStreamAckRequest) => parseChatStreamAcknowledgementResult(
    await ipcRenderer.invoke(
      desktopChannels.acknowledgeChatStream,
      parseChatStreamAckRequest(request),
    ),
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
  onChatEventBatch(listener: (delivery: Readonly<ChatEventDelivery>) => void) {
    let active = true
    const ipcListener = (_event: unknown, value: unknown) => {
      if (!active) return
      try {
        listener(parseChatEventDelivery(value))
      } catch {
        // Main-process event payloads are untrusted until this boundary validates them.
      }
    }
    ipcRenderer.on(desktopEventChannels.chatEventBatch, ipcListener)
    return () => {
      if (!active) return
      active = false
      ipcRenderer.removeListener(desktopEventChannels.chatEventBatch, ipcListener)
    }
  },
})

contextBridge.exposeInMainWorld('ancestry', ancestry)
