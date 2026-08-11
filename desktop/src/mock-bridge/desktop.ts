import {
  DESKTOP_PROTOCOL_VERSION,
  type ApplicationSettingValue,
  type ApplicationSettings,
  type ApplicationSettingsPatch,
  type AncestryBridge,
  type BridgeResult,
  type FileGrant,
  type FileGrantRevocation,
  type LocalPreferences,
  type PreferenceUpdate,
  type SecretReference,
  type SecretReferenceRequest,
  type SecretSetRequest,
  type SecretStatus,
} from '../shared-contract/desktop'
import {
  parsePreferenceUpdate,
  parseSecretReferenceRequest,
  parseSecretSetRequest,
  parseSettingsPatch,
} from '../shared-contract/runtime'
import {
  appInfoFixture,
  capabilitiesFixture,
  deepFreeze,
  degradedDiagnosticsFixture,
  preferencesFixture,
  readyDiagnosticsFixture,
  settingsFixture,
  unavailableFixture,
} from './fixtures'

export type DesktopFixtureMode = 'success' | 'degraded' | 'unavailable'

export function createMockAncestryBridge(initialMode: DesktopFixtureMode = 'success'): AncestryBridge {
  let mode = initialMode
  let preferences = preferencesFixture.data
  let settings = settingsFixture.data
  const presentSecrets = new Set<SecretReference>()
  const success = <T extends object>(data: Readonly<T>): BridgeResult<T> => deepFreeze({
    ok: true,
    protocolVersion: DESKTOP_PROTOCOL_VERSION,
    data,
  }) as BridgeResult<T>
  const failure = <T>(code: 'SETTINGS_CONFLICT' | 'SETTINGS_UNAVAILABLE' | 'SECRET_STORE_UNAVAILABLE'): BridgeResult<T> => deepFreeze({
    ok: false,
    protocolVersion: DESKTOP_PROTOCOL_VERSION,
    error: {
      code,
      message: code === 'SETTINGS_CONFLICT'
        ? 'Application settings changed before this update.'
        : code === 'SETTINGS_UNAVAILABLE'
          ? 'Application settings are unavailable.'
          : 'The operating-system credential store is unavailable.',
      remediation: code === 'SETTINGS_CONFLICT'
        ? 'Reload settings and try again.'
        : 'Retry the service or restart AncestryLLM.',
    },
  })
  const preferenceConflict = (): BridgeResult<LocalPreferences> => deepFreeze({
    ok: false,
    protocolVersion: DESKTOP_PROTOCOL_VERSION,
    error: {
      code: 'PREFERENCES_CONFLICT',
      message: 'Desktop preferences changed before this update.',
      remediation: 'Reload preferences and try again.',
    },
  })
  return Object.freeze({
    async getAppInfo() { return appInfoFixture },
    async getStartupDiagnostics() {
      return mode === 'success' ? readyDiagnosticsFixture : degradedDiagnosticsFixture
    },
    async getCapabilities() {
      return mode === 'success' ? capabilitiesFixture : unavailableFixture
    },
    async retrySidecar() {
      mode = 'success'
      return readyDiagnosticsFixture
    },
    async getPreferences() {
      return deepFreeze({ ok: true, protocolVersion: DESKTOP_PROTOCOL_VERSION, data: preferences }) as BridgeResult<LocalPreferences>
    },
    async updatePreferences(input: PreferenceUpdate) {
      const update = parsePreferenceUpdate(input)
      if (update.expectedRevision !== preferences.revision) return preferenceConflict()
      preferences = deepFreeze({
        colorScheme: update.colorScheme ?? preferences.colorScheme,
        reducedMotion: update.reducedMotion ?? preferences.reducedMotion,
        onboardingCompleted: update.onboardingCompleted ?? preferences.onboardingCompleted,
        schemaVersion: 1,
        revision: preferences.revision + 1,
      })
      return deepFreeze({ ok: true, protocolVersion: DESKTOP_PROTOCOL_VERSION, data: preferences }) as BridgeResult<LocalPreferences>
    },
    async getSettings() {
      return mode === 'unavailable' ? failure<ApplicationSettings>('SETTINGS_UNAVAILABLE') : success(settings)
    },
    async updateSettings(input: ApplicationSettingsPatch) {
      const update = parseSettingsPatch(input)
      if (mode === 'unavailable') return failure<ApplicationSettings>('SETTINGS_UNAVAILABLE')
      if (update.expected_revision !== settings.revision) return failure<ApplicationSettings>('SETTINGS_CONFLICT')
      const fields = settings.fields.map((field) => Object.prototype.hasOwnProperty.call(update.changes, field.key)
        ? { ...field, value: update.changes[field.key] as ApplicationSettingValue }
        : field)
      settings = deepFreeze({ schema_version: 1, revision: settings.revision + 1, fields })
      return success(settings)
    },
    async getSecretStatus(input: SecretReferenceRequest) {
      const { reference } = parseSecretReferenceRequest(input)
      const status: SecretStatus = {
        reference,
        status: mode === 'unavailable' ? 'unavailable' : presentSecrets.has(reference) ? 'present' : 'missing',
      }
      return success(status)
    },
    async setSecret(input: SecretSetRequest) {
      const { reference } = parseSecretSetRequest(input)
      if (mode === 'unavailable') return failure<SecretStatus>('SECRET_STORE_UNAVAILABLE')
      presentSecrets.add(reference)
      return success<SecretStatus>({ reference, status: 'present' })
    },
    async deleteSecret(input: SecretReferenceRequest) {
      const { reference } = parseSecretReferenceRequest(input)
      if (mode === 'unavailable') return failure<SecretStatus>('SECRET_STORE_UNAVAILABLE')
      presentSecrets.delete(reference)
      return success<SecretStatus>({ reference, status: 'missing' })
    },
    async requestOpenFileGrant() {
      return deepFreeze({ ok: true, protocolVersion: DESKTOP_PROTOCOL_VERSION, data: null }) as BridgeResult<FileGrant | null>
    },
    async requestSaveFileGrant() {
      return deepFreeze({ ok: true, protocolVersion: DESKTOP_PROTOCOL_VERSION, data: null }) as BridgeResult<FileGrant | null>
    },
    async revokeFileGrant() {
      return deepFreeze({
        ok: true,
        protocolVersion: DESKTOP_PROTOCOL_VERSION,
        data: { revoked: true as const },
      }) as BridgeResult<FileGrantRevocation>
    },
  })
}
