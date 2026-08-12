import {
  DESKTOP_PROTOCOL_VERSION,
  type ApplicationSettingValue,
  type ApplicationSettings,
  type ApplicationSettingsPatch,
  type AncestryBridge,
  type BridgeResult,
  type ConsentCreateRequest,
  type ConsentPreview,
  type ConsentPreviewRequest,
  type ConsentRevokeRequest,
  type FileGrant,
  type FileGrantRevocation,
  type LocalPreferences,
  type LocalRuntimeApplyRequest,
  type LocalRuntimeOperation,
  type LocalRuntimePreview,
  type LocalRuntimeRequest,
  type LocalRuntimeResult,
  type LocalRuntimeState,
  type LocalRuntimeStatus,
  type PreferenceUpdate,
  type ProviderConfiguration,
  type ProviderEndpointValidation,
  type ProviderEndpointValidationRequest,
  type ProviderProfileCreateRequest,
  type SecretReference,
  type SecretReferenceRequest,
  type SecretSetRequest,
  type SecretStatus,
} from '../shared-contract/desktop'
import {
  parseConsentCreateRequest,
  parseConsentPreviewRequest,
  parseConsentRevokeRequest,
  parseLocalRuntimeApplyRequest,
  parseLocalRuntimeRequest,
  parsePreferenceUpdate,
  parseProviderEndpointValidationRequest,
  parseProviderProfileCreateRequest,
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
  let providerRevision = 0
  let providerConfiguration: ProviderConfiguration = deepFreeze({
    schema_version: 1,
    revision: '0'.repeat(64),
    profiles: [],
    consents: [],
  })
  const presentSecrets = new Set<SecretReference>()
  let localRuntimeState: LocalRuntimeState = 'not-installed'
  const localRuntimeConfirmations: Readonly<Record<LocalRuntimeOperation, string>> = {
    setup: 'SET UP LOCAL RUNTIME',
    start: 'START LOCAL RUNTIME',
    stop: 'STOP LOCAL RUNTIME',
    repair: 'REPAIR LOCAL RUNTIME',
    'uninstall-preserve': 'REMOVE LOCAL RUNTIME',
    'uninstall-delete': 'DELETE LOCAL RUNTIME DATA',
  }
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
  const providerConflict = (): BridgeResult<ProviderConfiguration> => deepFreeze({
    ok: false,
    protocolVersion: DESKTOP_PROTOCOL_VERSION,
    error: {
      code: 'PROVIDER_CONFIGURATION_CONFLICT',
      message: 'Provider configuration changed before this update.',
      remediation: 'Reload provider settings and try again.',
    },
  })
  const nextProviderConfiguration = (
    profiles: ProviderConfiguration['profiles'],
    consents: ProviderConfiguration['consents'],
  ): ProviderConfiguration => {
    providerRevision += 1
    providerConfiguration = deepFreeze({
      schema_version: 1,
      revision: providerRevision.toString(16).repeat(64).slice(0, 64),
      profiles,
      consents,
    })
    return providerConfiguration
  }
  const localRuntimeStatus = (): LocalRuntimeStatus => {
    const installed = localRuntimeState !== 'not-installed'
    const code = localRuntimeState === 'not-installed'
      ? 'RUNTIME_NOT_INSTALLED'
      : localRuntimeState === 'stopped'
        ? 'RUNTIME_STOPPED'
        : localRuntimeState === 'ready'
          ? 'RUNTIME_READY'
          : 'RUNTIME_UNHEALTHY'
    return deepFreeze({
      schema_version: 1,
      state: localRuntimeState,
      code,
      supported: true,
      host: {
        operating_system: 'macos',
        architecture: 'arm64',
        macos_major: 15,
        virtualization: 'available',
        free_space: 'sufficient',
        existing_docker_contexts: 0,
      },
      allocation: { cpus: 4, memory_gib: 8, disk_gib: 60 },
      components: [
        { name: 'colima', version: '0.10.3', installed },
        { name: 'lima', version: '2.2.0', installed },
        { name: 'docker-cli', version: '29.7.2', installed },
        { name: 'docker-buildx', version: '0.36.1', installed },
        { name: 'docker-compose', version: '5.4.0', installed },
      ],
      vm_image: { version: '0.10.4', installed },
    })
  }
  const localRuntimePreview = (request: LocalRuntimeRequest): LocalRuntimePreview => deepFreeze({
    schema_version: 1,
    operation: request.operation,
    offline: request.offline,
    actions: [{ code: `RUNTIME_${request.operation.toUpperCase().replaceAll('-', '_')}` }],
    confirmation_phrase: localRuntimeConfirmations[request.operation],
    preserves_data: request.operation !== 'uninstall-delete',
    deletes_data: request.operation === 'uninstall-delete',
    plan_revision: 'a'.repeat(64),
    status: localRuntimeStatus(),
    review: {
      artifacts: [
        {
          name: 'colima',
          version: '0.10.3',
          repository: 'abiosoft/colima',
          asset_name: 'colima-Darwin-arm64',
          source_url: 'https://github.com/abiosoft/colima/releases/download/v0.10.3/colima-Darwin-arm64',
          sha256: '1'.repeat(64),
          size_bytes: 15_656_320,
          license: 'MIT',
          license_url: 'https://raw.githubusercontent.com/abiosoft/colima/v0.10.3/LICENSE',
          license_sha256: '2'.repeat(64),
        },
        {
          name: 'lima',
          version: '2.2.0',
          repository: 'lima-vm/lima',
          asset_name: 'lima-2.2.0-Darwin-arm64.tar.gz',
          source_url: 'https://github.com/lima-vm/lima/releases/download/v2.2.0/lima-2.2.0-Darwin-arm64.tar.gz',
          sha256: '3'.repeat(64),
          size_bytes: 37_586_365,
          license: 'Apache-2.0',
          license_url: 'https://raw.githubusercontent.com/lima-vm/lima/v2.2.0/LICENSE',
          license_sha256: '4'.repeat(64),
        },
        {
          name: 'docker-cli',
          version: '29.7.2',
          repository: 'docker/cli',
          asset_name: 'docker-29.7.2.tgz',
          source_url: 'https://download.docker.com/mac/static/stable/aarch64/docker-29.7.2.tgz',
          sha256: '5'.repeat(64),
          size_bytes: 18_920_558,
          license: 'Apache-2.0',
          license_url: 'https://raw.githubusercontent.com/docker/cli/v29.7.2/LICENSE',
          license_sha256: '6'.repeat(64),
        },
        {
          name: 'docker-buildx',
          version: '0.36.1',
          repository: 'docker/buildx',
          asset_name: 'buildx-v0.36.1.darwin-arm64',
          source_url: 'https://github.com/docker/buildx/releases/download/v0.36.1/buildx-v0.36.1.darwin-arm64',
          sha256: '7'.repeat(64),
          size_bytes: 62_541_920,
          license: 'Apache-2.0',
          license_url: 'https://raw.githubusercontent.com/docker/buildx/v0.36.1/LICENSE',
          license_sha256: '8'.repeat(64),
        },
        {
          name: 'docker-compose',
          version: '5.4.0',
          repository: 'docker/compose',
          asset_name: 'docker-compose-darwin-aarch64',
          source_url: 'https://github.com/docker/compose/releases/download/v5.4.0/docker-compose-darwin-aarch64',
          sha256: '9'.repeat(64),
          size_bytes: 46_852_962,
          license: 'Apache-2.0',
          license_url: 'https://raw.githubusercontent.com/docker/compose/v5.4.0/LICENSE',
          license_sha256: 'a'.repeat(64),
        },
      ],
      vm_image: {
        version: '0.10.4',
        repository: 'abiosoft/colima-core',
        asset_name: 'ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz',
        source_url: 'https://github.com/abiosoft/colima-core/releases/download/v0.10.4/ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz',
        sha256: 'b'.repeat(64),
        size_bytes: 332_354_401,
      },
      ownership: {
        profile: 'ancestryllm-local-arm64',
        context: 'colima-ancestryllm-local-arm64',
      },
      isolation: {
        loopback_only: true,
        kubernetes: false,
        privileged_containers: false,
        renderer_socket_access: false,
        container_socket_access: false,
        cross_profile_socket_access: false,
      },
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
    async getProviderConfiguration() {
      return success(providerConfiguration)
    },
    async createProviderProfile(input: ProviderProfileCreateRequest) {
      const request = parseProviderProfileCreateRequest(input)
      if (request.expected_revision !== providerConfiguration.revision) return providerConflict()
      const endpointKind = request.provider_id === 'ollama' ? 'loopback' as const : 'remote' as const
      const secretReference = request.provider_id === 'ollama'
        ? null
        : `${request.provider_id}.api_key` as const
      return success(nextProviderConfiguration([
        ...providerConfiguration.profiles,
        {
          name: request.name,
          provider_id: request.provider_id,
          model: request.model,
          endpoint: request.endpoint,
          endpoint_kind: endpointKind,
          secret_reference: secretReference,
          enabled: true,
        },
      ], providerConfiguration.consents))
    },
    async validateProviderEndpoint(input: ProviderEndpointValidationRequest) {
      const request = parseProviderEndpointValidationRequest(input)
      const result: ProviderEndpointValidation = {
        schema_version: 1,
        status: 'reachable',
        endpoint_kind: request.provider_id === 'ollama' ? 'loopback' : 'remote',
        http_status: 200,
        destination_digest: 'a'.repeat(64),
      }
      return success(result)
    },
    async previewConsent(input: ConsentPreviewRequest) {
      const request = parseConsentPreviewRequest(input)
      const profile = providerConfiguration.profiles.find((item) => item.name === request.provider_profile_name)
      if (profile === undefined) throw new Error('Mock consent preview requires an existing profile')
      const warningCodes: ConsentPreview['warning_codes'][number][] = []
      if (request.data_classes.some((item) => [
        'living_person', 'possibly_living_person', 'government_identifier',
      ].includes(item))) warningCodes.push('LIVING_PERSON_DATA_INCLUDED')
      if (profile.endpoint_kind === 'remote') warningCodes.push('REMOTE_PROVIDER_SELECTED')
      if (profile.endpoint_kind === 'remote' && request.retain_payloads) {
        warningCodes.push('REMOTE_RETENTION_ENABLED')
      }
      const preview: ConsentPreview = {
        ...request,
        provider_id: profile.provider_id,
        warning_codes: warningCodes,
      }
      return success(preview)
    },
    async createConsent(input: ConsentCreateRequest) {
      const request = parseConsentCreateRequest(input)
      if (request.expected_revision !== providerConfiguration.revision) return providerConflict()
      return success(nextProviderConfiguration(providerConfiguration.profiles, [
        ...providerConfiguration.consents,
        {
          name: request.name,
          provider_profile_name: request.preview.provider_profile_name,
          provider_id: request.preview.provider_id,
          modules: request.preview.modules,
          purposes: request.preview.purposes,
          data_classes: request.preview.data_classes,
          models: request.preview.models,
          max_cost_usd: request.preview.max_cost_usd,
          retain_payloads: request.preview.retain_payloads,
          active: true,
        },
      ]))
    },
    async revokeConsent(input: ConsentRevokeRequest) {
      const request = parseConsentRevokeRequest(input)
      if (request.expected_revision !== providerConfiguration.revision) return providerConflict()
      return success(nextProviderConfiguration(
        providerConfiguration.profiles,
        providerConfiguration.consents.map((consent) => consent.name === request.name
          ? { ...consent, active: false }
          : consent),
      ))
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
    async getLocalRuntimeStatus() {
      return success(localRuntimeStatus())
    },
    async previewLocalRuntime(input: LocalRuntimeRequest) {
      return success(localRuntimePreview(parseLocalRuntimeRequest(input)))
    },
    async applyLocalRuntime(input: LocalRuntimeApplyRequest) {
      const request = parseLocalRuntimeApplyRequest(input)
      const preview = localRuntimePreview(request)
      if (
        request.plan_revision !== preview.plan_revision
        || request.confirmation !== preview.confirmation_phrase
      ) throw new Error('Mock local runtime operation requires the current confirmed plan')
      localRuntimeState = request.operation === 'stop'
        ? 'stopped'
        : request.operation.startsWith('uninstall-')
          ? 'not-installed'
          : 'ready'
      const result: LocalRuntimeResult = {
        schema_version: 1,
        operation: request.operation,
        state: localRuntimeState,
        code: localRuntimeStatus().code,
      }
      return success(result)
    },
  })
}
