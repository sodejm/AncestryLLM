import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { settingsFixture } from '../mock-bridge/fixtures'
import {
  parseAppInfoResult,
  parseArtifactRef,
  parseCapabilitiesResult,
  parseFileGrantId,
  parseFileGrantResult,
  parseFileGrantRevocationResult,
  parseLocalRuntimePreviewResult,
  parseOpenFileGrantRequest,
  parseProviderProfileCreateRequest,
  parseSecretReferenceRequest,
  parseSecretSetRequest,
  parseSecretStatusResult,
  parseSettingsPatch,
  parseSettingsResult,
  parsePreferenceUpdate,
  parsePreferencesResult,
  parseSaveFileGrantRequest,
  parseStartupDiagnosticsResult,
} from './runtime'

type ContractProperty = {
  const?: string
  pattern?: string
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  minItems?: number
  maxItems?: number
}

type MutableAction = { dispatch_key: string; name: string; summary: string }
type MutableModule = { module_id: string; name: string; summary: string; actions: MutableAction[] }
type MutableManifest = {
  api: { namespace: string; contract: string; application_contract: string }
  modules: MutableModule[]
  request_policy: {
    max_body_bytes: number
    max_json_depth: number
    max_collection_items: number
    max_string_characters: number
  }
  pagination: { default_limit: number; maximum_limit: number; maximum_cursor_characters: number }
}

const openApi = JSON.parse(readFileSync(
  resolve(process.cwd(), '../docs/api/openapi-v1.json'),
  'utf8',
)) as { components: { schemas: Record<string, { properties: Record<string, ContractProperty> }> } }

const contractProperty = (schema: string, property: string): ContractProperty => {
  const definition = openApi.components.schemas[schema]?.properties[property]
  if (!definition) throw new Error(`Missing OpenAPI property ${schema}.${property}`)
  return definition
}

const capabilityManifest = {
  api: {
    namespace: '/api/v1',
    contract: 'ancestryllm.internal-api/1',
    application_contract: 'ancestryllm.application/0.3',
  },
  modules: [{
    module_id: 'tree',
    name: 'Tree',
    summary: 'Fictional local tree tools.',
    actions: [{ dispatch_key: 'tree.summary', name: 'summary', summary: 'Summarize a fictional tree.' }],
  }],
  request_policy: {
    max_body_bytes: 1_048_576,
    max_json_depth: 16,
    max_collection_items: 1_000,
    max_string_characters: 65_536,
  },
  pagination: { default_limit: 25, maximum_limit: 100, maximum_cursor_characters: 256 },
} as const

const mutableManifest = (): MutableManifest =>
  structuredClone(capabilityManifest) as unknown as MutableManifest

const firstModule = (manifest: MutableManifest): MutableModule => {
  const module = manifest.modules[0]
  if (!module) throw new Error('Capability fixture must contain a module')
  return module
}

const firstAction = (manifest: MutableManifest): MutableAction => {
  const action = firstModule(manifest).actions[0]
  if (!action) throw new Error('Capability fixture must contain an action')
  return action
}

const capabilityResult = (data: MutableManifest) => ({ ok: true, protocolVersion: '1', data })
const grantId = `grt_${'a'.repeat(64)}`
const fileGrantResult = {
  ok: true,
  protocolVersion: '1',
  data: {
    grantId,
    purpose: 'gedcom-read',
    access: 'read',
    scope: {
      originatingWindow: 'requesting-window',
      lifetime: 'app-session',
      redemption: 'single-use',
    },
    metadata: {
      displayName: 'fictional.ged',
      format: 'gedcom',
      sizeBytes: 26,
      validation: 'validated-input',
    },
  },
} as const

const startupReport = {
  schema_version: 1,
  status: 'ready',
  platform: { operating_system: 'macos', architecture: 'arm64' },
  components: [
    {
      component: 'configuration',
      status: 'ready',
      code: 'CONFIGURATION_READY',
      message: 'The desktop configuration is ready.',
      remediation: null,
      restart_required: false,
      blocks_mutations: false,
    },
    {
      component: 'sqlcipher',
      status: 'ready',
      code: 'SQLCIPHER_READY',
      message: 'SQLCipher encryption support is available.',
      remediation: null,
      restart_required: false,
      blocks_mutations: false,
    },
    {
      component: 'keyring',
      status: 'ready',
      code: 'KEYRING_READY',
      message: 'The configured credential-store backend can be queried without writing a secret.',
      remediation: null,
      restart_required: false,
      blocks_mutations: false,
    },
    {
      component: 'workspace',
      status: 'ready',
      code: 'DATABASE_DIRECTORY_READY',
      message: 'Workspace directory is writable.',
      remediation: null,
      restart_required: false,
      blocks_mutations: false,
    },
  ],
} as const

const localRuntimeStatus = {
  schema_version: 1,
  state: 'not-installed',
  code: 'RUNTIME_NOT_INSTALLED',
  supported: true,
  host: {
    operating_system: 'macos',
    architecture: 'arm64',
    macos_major: 15,
    virtualization: 'available',
    free_space: 'sufficient',
    existing_docker_contexts: 1,
  },
  allocation: { cpus: 4, memory_gib: 8, disk_gib: 20 },
  components: [
    { name: 'colima', version: '0.10.3', installed: false },
    { name: 'lima', version: '2.2.0', installed: false },
    { name: 'docker-cli', version: '29.7.2', installed: false },
    { name: 'docker-buildx', version: '0.36.1', installed: false },
    { name: 'docker-compose', version: '5.4.0', installed: false },
  ],
  vm_image: { version: '0.10.4', installed: false },
} as const

const localRuntimePreview = {
  schema_version: 1,
  operation: 'setup',
  offline: false,
  actions: [{ code: 'VERIFY_HOST' }, { code: 'DOWNLOAD_PINNED_COMPONENTS' }],
  confirmation_phrase: 'SET UP LOCAL RUNTIME',
  preserves_data: true,
  deletes_data: false,
  plan_revision: 'a'.repeat(64),
  status: localRuntimeStatus,
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
} as const

describe('runtime bridge validation', () => {
  it('accepts an exact local-runtime artifact, ownership, and isolation review', () => {
    expect(parseLocalRuntimePreviewResult({
      ok: true,
      protocolVersion: '1',
      data: localRuntimePreview,
    })).toMatchObject({ ok: true, data: { review: { ownership: localRuntimePreview.review.ownership } } })
  })

  it('rejects local-runtime review drift and weakened isolation', () => {
    expect(() => parseLocalRuntimePreviewResult({
      ok: true,
      protocolVersion: '1',
      data: {
        ...localRuntimePreview,
        review: {
          ...localRuntimePreview.review,
          artifacts: localRuntimePreview.review.artifacts.slice(0, -1),
        },
      },
    })).toThrow('Invalid bridge response')
    expect(() => parseLocalRuntimePreviewResult({
      ok: true,
      protocolVersion: '1',
      data: {
        ...localRuntimePreview,
        review: {
          ...localRuntimePreview.review,
          isolation: { ...localRuntimePreview.review.isolation, loopback_only: false },
        },
      },
    })).toThrow('Invalid bridge response')
  })

  it('accepts exact settings and write-only secret contracts', () => {
    expect(parseSettingsPatch({
      schema_version: 1,
      expected_revision: 3,
      changes: { 'providers.default': 'openai' },
    })).toMatchObject({ expected_revision: 3 })
    expect(parseSettingsResult(settingsFixture)).toMatchObject({
      ok: true,
      data: { revision: 0 },
    })
    expect(parseSecretReferenceRequest({ reference: 'openai.api_key' })).toEqual({ reference: 'openai.api_key' })
    expect(parseSecretSetRequest({ reference: 'openai.api_key', value: 'private-test-value' })).toEqual({
      reference: 'openai.api_key',
      value: 'private-test-value',
    })
    expect(parseSecretStatusResult({
      ok: true,
      protocolVersion: '1',
      data: { reference: 'openai.api_key', status: 'present' },
    })).toMatchObject({ ok: true, data: { status: 'present' } })
  })

  it('rejects unknown settings, secret references, readback fields, and unbounded secret input', () => {
    expect(() => parseSettingsPatch({
      schema_version: 1,
      expected_revision: 0,
      changes: { 'providers.api_key': 'secret' },
    })).toThrow('Invalid settings patch')
    expect(() => parseSecretReferenceRequest({ reference: 'attacker.controlled' })).toThrow('Invalid secret reference request')
    expect(() => parseSecretSetRequest({ reference: 'openai.api_key', value: '' })).toThrow('Invalid secret set request')
    expect(() => parseSecretSetRequest({ reference: 'openai.api_key', value: 'x'.repeat(65_537) })).toThrow('Invalid secret set request')
    expect(() => parseSecretStatusResult({
      ok: true,
      protocolVersion: '1',
      data: { reference: 'openai.api_key', status: 'present', value: 'must-not-cross-bridge' },
    })).toThrow('Invalid bridge response')
    expect(() => parseSettingsResult({
      ...settingsFixture,
      data: { ...settingsFixture.data, fields: settingsFixture.data.fields.slice(0, -1) },
    })).toThrow('Invalid bridge response')
    const providerField = settingsFixture.data.fields[0]!
    expect(() => parseSettingsResult({
      ...settingsFixture,
      data: {
        ...settingsFixture.data,
        fields: [
          {
            ...providerField,
            validation: {
              ...providerField.validation,
              allowed_values: providerField.validation.allowed_values.slice(0, -1),
            },
          },
          ...settingsFixture.data.fields.slice(1),
        ],
      },
    })).toThrow('Invalid bridge response')
  })

  it('requires an exact tested endpoint identity for provider profile creation', () => {
    const request = {
      schema_version: 1,
      expected_revision: '0'.repeat(64),
      name: 'local-default',
      provider_id: 'ollama',
      model: 'llama3.2',
      endpoint: 'http://127.0.0.1:11434',
      endpoint_identity_sha256: 'a'.repeat(64),
    }

    expect(parseProviderProfileCreateRequest(request)).toEqual(request)
    expect(() => parseProviderProfileCreateRequest({
      ...request,
      endpoint_identity_sha256: 'not-a-digest',
    })).toThrow('Invalid provider profile request')
    const withoutIdentity = {
      schema_version: request.schema_version,
      expected_revision: request.expected_revision,
      name: request.name,
      provider_id: request.provider_id,
      model: request.model,
      endpoint: request.endpoint,
    }
    expect(() => parseProviderProfileCreateRequest(withoutIdentity)).toThrow('Invalid provider profile request')
    expect(() => parseProviderProfileCreateRequest({ ...request, destination_address: '127.0.0.1' }))
      .toThrow('Invalid provider profile request')
  })

  it('accepts exact versioned results for each renderer-safe response', () => {
    expect(parseAppInfoResult({ ok: true, protocolVersion: '1', data: { applicationName: 'AncestryLLM', appVersion: '0.5.0-dev', buildChannel: 'development' } }).ok).toBe(true)
    expect(parseStartupDiagnosticsResult({ ok: true, protocolVersion: '1', data: { state: 'ready', failure: null, automaticRestartsRemaining: 0, manualRetriesRemaining: 1, report: startupReport } }).ok).toBe(true)
    expect(parseCapabilitiesResult({ ok: true, protocolVersion: '1', data: capabilityManifest }).ok).toBe(true)
    expect(parsePreferencesResult({ ok: true, protocolVersion: '1', data: { colorScheme: 'system', reducedMotion: false, onboardingCompleted: false, schemaVersion: 1, revision: 0 } }).ok).toBe(true)
  })

  it('rejects startup diagnostic fields that could disclose local paths', () => {
    const unsafe = {
      ...startupReport,
      components: startupReport.components.map((component, index) => index === 0
        ? { ...component, message: '/Users/example/config.toml could not be read' }
        : component),
    }
    expect(() => parseStartupDiagnosticsResult({
      ok: true,
      protocolVersion: '1',
      data: {
        state: 'degraded',
        failure: null,
        automaticRestartsRemaining: 0,
        manualRetriesRemaining: 1,
        report: unsafe,
      },
    })).toThrow('Invalid bridge response')
  })

  it('rejects invalid requests, unknown fields, and unbounded errors', () => {
    expect(parsePreferenceUpdate({ expectedRevision: 0, colorScheme: 'dark' })).toEqual({ expectedRevision: 0, colorScheme: 'dark' })
    expect(() => parsePreferenceUpdate({ expectedRevision: 0, colorScheme: 'sepia' })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({ expectedRevision: 0, reducedMotion: true, surprise: true })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({ colorScheme: 'dark' })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({ expectedRevision: -1, colorScheme: 'dark' })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({ expectedRevision: 0 })).toThrow('Invalid preference update')
    expect(() => parsePreferenceUpdate({})).toThrow('Invalid preference update')
    expect(() => parseAppInfoResult({ ok: true, protocolVersion: '1', data: {}, surprise: true })).toThrow('Invalid bridge response')
    expect(() => parseAppInfoResult({ ok: false, protocolVersion: '1', error: { code: 'INTERNAL_ERROR', message: 'x'.repeat(241), remediation: 'Restart AncestryLLM.' } })).toThrow('Invalid bridge response')
  })

  it('accepts exact path-free file-grant requests, results, revocations, and artifact references', () => {
    expect(parseOpenFileGrantRequest({ purpose: 'gedcom-read' })).toEqual({ purpose: 'gedcom-read' })
    expect(parseSaveFileGrantRequest({ purpose: 'markdown-write', suggestedName: 'family-summary.md' })).toEqual({
      purpose: 'markdown-write',
      suggestedName: 'family-summary.md',
    })
    expect(parseFileGrantId(grantId)).toBe(grantId)
    expect(parseFileGrantResult(fileGrantResult)).toEqual(fileGrantResult)
    expect(parseFileGrantResult({ ok: true, protocolVersion: '1', data: null })).toEqual({
      ok: true,
      protocolVersion: '1',
      data: null,
    })
    expect(parseFileGrantRevocationResult({
      ok: true,
      protocolVersion: '1',
      data: { revoked: true },
    }).ok).toBe(true)
    expect(parseArtifactRef({
      artifact_id: `art_${'b'.repeat(32)}`,
      artifact_type: 'gedcom_export',
      media_type: 'text/vnd.gedcom',
      sha256: 'c'.repeat(64),
      size_bytes: 42,
      status: 'staged',
    })).toMatchObject({ artifact_type: 'gedcom_export', status: 'staged' })
  })

  it('rejects renderer paths, malformed IDs, unknown fields, and incoherent grant metadata', () => {
    expect(() => parseOpenFileGrantRequest({ purpose: 'gedcom-read', path: '/private/tree.ged' })).toThrow('Invalid open-file grant request')
    expect(() => parseSaveFileGrantRequest({ purpose: 'gedcom-write', suggestedName: '../tree.ged' })).toThrow('Invalid save-file grant request')
    expect(() => parseFileGrantId('grt_predictable')).toThrow('Invalid file-grant ID')
    expect(() => parseFileGrantResult({
      ...fileGrantResult,
      data: { ...fileGrantResult.data, path: '/private/tree.ged' },
    })).toThrow('Invalid bridge response')
    expect(() => parseFileGrantResult({
      ...fileGrantResult,
      data: { ...fileGrantResult.data, access: 'write' },
    })).toThrow('Invalid bridge response')
    expect(() => parseFileGrantResult({
      ...fileGrantResult,
      data: { ...fileGrantResult.data, scope: { ...fileGrantResult.data.scope, lifetime: 'forever' } },
    })).toThrow('Invalid bridge response')
    expect(() => parseFileGrantResult({
      ...fileGrantResult,
      data: {
        ...fileGrantResult.data,
        metadata: { ...fileGrantResult.data.metadata, validation: 'replacement-confirmed' },
      },
    })).toThrow('Invalid bridge response')
    expect(() => parseArtifactRef({
      artifact_id: `art_${'b'.repeat(32)}`,
      artifact_type: 'gedcom_export',
      media_type: 'text/vnd.gedcom',
      sha256: 'c'.repeat(64),
      size_bytes: 42,
      status: 'staged',
      path: '/private/tree.ged',
    })).toThrow('Invalid bridge response')
  })

  it('rejects OpenAPI capability drift and unsafe extra fields', () => {
    expect(() => parseCapabilitiesResult({ ok: true, protocolVersion: '1', data: { ...capabilityManifest, token: 'secret' } })).toThrow('Invalid bridge response')
    expect(() => parseCapabilitiesResult({ ok: true, protocolVersion: '1', data: { ...capabilityManifest, api: { ...capabilityManifest.api, contract: 'ancestryllm.internal-api/2' } } })).toThrow('Invalid bridge response')
  })

  it('derives runtime capability boundaries from the checked-in OpenAPI contract', () => {
    const strings = [
      { schema: 'CapabilityModule', property: 'module_id', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstModule(manifest).module_id = value } },
      { schema: 'CapabilityModule', property: 'name', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstModule(manifest).name = value } },
      { schema: 'CapabilityModule', property: 'summary', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstModule(manifest).summary = value } },
      { schema: 'CapabilityAction', property: 'dispatch_key', value: (length: number) => `a.${'x'.repeat(length - 2)}`, set: (manifest: MutableManifest, value: string) => { firstAction(manifest).dispatch_key = value } },
      { schema: 'CapabilityAction', property: 'name', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstAction(manifest).name = value } },
      { schema: 'CapabilityAction', property: 'summary', value: (length: number) => 'x'.repeat(length), set: (manifest: MutableManifest, value: string) => { firstAction(manifest).summary = value } },
    ]
    for (const item of strings) {
      const { minLength, maxLength } = contractProperty(item.schema, item.property)
      expect(minLength).toBeTypeOf('number')
      expect(maxLength).toBeTypeOf('number')
      for (const length of [minLength as number, maxLength as number]) {
        const manifest = mutableManifest()
        item.set(manifest, item.value(length))
        expect(() => parseCapabilitiesResult(capabilityResult(manifest))).not.toThrow()
      }
      for (const length of [(minLength as number) - 1, (maxLength as number) + 1]) {
        const manifest = mutableManifest()
        item.set(manifest, item.value(length))
        expect(() => parseCapabilitiesResult(capabilityResult(manifest))).toThrow('Invalid bridge response')
      }
    }

    const patterns = [
      {
        schema: 'CapabilityModule',
        property: 'module_id',
        candidates: ['tree', 'tree.summary', 'tree:summary-1', 'tree_summary', 'tree/summary', 'tree summary', 'árbol'],
        set: (manifest: MutableManifest, value: string) => { firstModule(manifest).module_id = value },
      },
      {
        schema: 'CapabilityAction',
        property: 'name',
        candidates: ['summary', 'tree.summary', 'tree:summary-1', 'tree_summary', 'tree/summary', 'tree summary', 'árbol'],
        set: (manifest: MutableManifest, value: string) => { firstAction(manifest).name = value },
      },
      {
        schema: 'CapabilityAction',
        property: 'dispatch_key',
        candidates: ['tree.summary', 'tree-1.summary_2', 'tree', 'tree.summary.detail', 'tree:summary', 'tree/summary', 'tree summary'],
        set: (manifest: MutableManifest, value: string) => { firstAction(manifest).dispatch_key = value },
      },
    ]
    for (const item of patterns) {
      const { pattern } = contractProperty(item.schema, item.property)
      expect(pattern).toBeTypeOf('string')
      const contractPattern = new RegExp(pattern as string)
      expect(item.candidates.some((candidate) => contractPattern.test(candidate))).toBe(true)
      expect(item.candidates.some((candidate) => !contractPattern.test(candidate))).toBe(true)
      for (const candidate of item.candidates) {
        const manifest = mutableManifest()
        item.set(manifest, candidate)
        const assertion = expect(() => parseCapabilitiesResult(capabilityResult(manifest)))
        if (contractPattern.test(candidate)) assertion.not.toThrow()
        else assertion.toThrow('Invalid bridge response')
      }
    }

    const numbers = [
      { schema: 'RequestSizePolicy', property: 'max_body_bytes', set: (manifest: MutableManifest, value: number) => { manifest.request_policy.max_body_bytes = value } },
      { schema: 'RequestSizePolicy', property: 'max_json_depth', set: (manifest: MutableManifest, value: number) => { manifest.request_policy.max_json_depth = value } },
      { schema: 'RequestSizePolicy', property: 'max_collection_items', set: (manifest: MutableManifest, value: number) => { manifest.request_policy.max_collection_items = value } },
      { schema: 'RequestSizePolicy', property: 'max_string_characters', set: (manifest: MutableManifest, value: number) => { manifest.request_policy.max_string_characters = value } },
      { schema: 'PaginationPolicy', property: 'default_limit', set: (manifest: MutableManifest, value: number) => { manifest.pagination.default_limit = value } },
      { schema: 'PaginationPolicy', property: 'maximum_limit', set: (manifest: MutableManifest, value: number) => { manifest.pagination.maximum_limit = value; manifest.pagination.default_limit = Math.min(value, manifest.pagination.default_limit) } },
      { schema: 'PaginationPolicy', property: 'maximum_cursor_characters', set: (manifest: MutableManifest, value: number) => { manifest.pagination.maximum_cursor_characters = value } },
    ]
    for (const item of numbers) {
      const { minimum, maximum } = contractProperty(item.schema, item.property)
      expect(minimum).toBeTypeOf('number')
      expect(maximum).toBeTypeOf('number')
      for (const value of [minimum as number, maximum as number]) {
        const manifest = mutableManifest()
        item.set(manifest, value)
        expect(() => parseCapabilitiesResult(capabilityResult(manifest))).not.toThrow()
      }
      for (const value of [(minimum as number) - 1, (maximum as number) + 1]) {
        const manifest = mutableManifest()
        item.set(manifest, value)
        expect(() => parseCapabilitiesResult(capabilityResult(manifest))).toThrow('Invalid bridge response')
      }
    }

    const modulesMaximum = contractProperty('CapabilityManifest', 'modules').maxItems as number
    const moduleTemplate = firstModule(mutableManifest())
    for (const [count, accepted] of [[modulesMaximum, true], [modulesMaximum + 1, false]] as const) {
      const manifest = mutableManifest()
      manifest.modules = Array.from({ length: count }, () => structuredClone(moduleTemplate))
      const assertion = expect(() => parseCapabilitiesResult(capabilityResult(manifest)))
      if (accepted) assertion.not.toThrow()
      else assertion.toThrow('Invalid bridge response')
    }

    const actions = contractProperty('CapabilityModule', 'actions')
    const actionTemplate = firstAction(mutableManifest())
    for (const [count, accepted] of [[actions.minItems as number, true], [actions.maxItems as number, true], [(actions.minItems as number) - 1, false], [(actions.maxItems as number) + 1, false]] as const) {
      const manifest = mutableManifest()
      firstModule(manifest).actions = Array.from({ length: count }, () => structuredClone(actionTemplate))
      const assertion = expect(() => parseCapabilitiesResult(capabilityResult(manifest)))
      if (accepted) assertion.not.toThrow()
      else assertion.toThrow('Invalid bridge response')
    }

    for (const property of ['namespace', 'contract', 'application_contract'] as const) {
      const expected = contractProperty('ApiVersion', property).const as string
      const accepted = mutableManifest()
      accepted.api[property] = expected
      expect(() => parseCapabilitiesResult(capabilityResult(accepted))).not.toThrow()
      const rejected = mutableManifest()
      rejected.api[property] = `${expected}.drift`
      expect(() => parseCapabilitiesResult(capabilityResult(rejected))).toThrow('Invalid bridge response')
    }
  })
})
