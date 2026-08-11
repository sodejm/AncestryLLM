import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const openApiUrl = new URL('../../docs/api/openapi-v1.json', import.meta.url)

test('OpenAPI bridge surfaces stay aligned with the TypeScript runtime contract', async () => {
  const document = JSON.parse(await readFile(openApiUrl, 'utf8'))
  const schemas = document.components.schemas
  assert.deepEqual(Object.keys(document.paths).sort(), [
    '/api/v1/capabilities',
    '/api/v1/health',
    '/api/v1/secrets/{reference}/delete',
    '/api/v1/secrets/{reference}/set',
    '/api/v1/secrets/{reference}/status',
    '/api/v1/settings',
  ])
  assert.equal(document.paths['/api/v1/capabilities'].get.responses['200'].content['application/json'].schema.$ref, '#/components/schemas/CapabilityManifest')

  const settingsPath = document.paths['/api/v1/settings']
  assert.equal(settingsPath.get.responses['200'].content['application/json'].schema.$ref, '#/components/schemas/SettingsResponse')
  assert.equal(settingsPath.patch.requestBody.content['application/json'].schema.$ref, '#/components/schemas/SettingsPatchRequest')
  assert.equal(settingsPath.patch.responses['200'].content['application/json'].schema.$ref, '#/components/schemas/SettingsResponse')

  const secretStatusRef = '#/components/schemas/SecretStatusResponse'
  assert.equal(document.paths['/api/v1/secrets/{reference}/status'].get.responses['200'].content['application/json'].schema.$ref, secretStatusRef)
  assert.equal(document.paths['/api/v1/secrets/{reference}/set'].post.requestBody.content['application/json'].schema.$ref, '#/components/schemas/SecretSetRequest')
  assert.equal(document.paths['/api/v1/secrets/{reference}/set'].post.responses['200'].content['application/json'].schema.$ref, secretStatusRef)
  assert.equal(document.paths['/api/v1/secrets/{reference}/delete'].post.responses['200'].content['application/json'].schema.$ref, secretStatusRef)

  assert.deepEqual({
    additionalProperties: schemas.SecretSetRequest.additionalProperties,
    required: schemas.SecretSetRequest.required,
    properties: schemas.SecretSetRequest.properties,
  }, {
    additionalProperties: false,
    required: ['value'],
    properties: {
      value: { maxLength: 65_536, minLength: 1, title: 'Value', type: 'string', writeOnly: true },
    },
  })
  assert.deepEqual({
    additionalProperties: schemas.SecretStatusResponse.additionalProperties,
    required: schemas.SecretStatusResponse.required,
    properties: schemas.SecretStatusResponse.properties,
  }, {
    additionalProperties: false,
    required: ['reference', 'status'],
    properties: {
      reference: { maxLength: 96, minLength: 1, pattern: '^[a-z0-9_.-]+$', title: 'Reference', type: 'string' },
      status: { enum: ['present', 'missing', 'unavailable'], title: 'Status', type: 'string' },
    },
  })
  assert.equal(Object.hasOwn(schemas.SecretStatusResponse.properties, 'value'), false)

  assert.deepEqual({
    additionalProperties: schemas.SettingsResponse.additionalProperties,
    required: schemas.SettingsResponse.required,
    properties: Object.keys(schemas.SettingsResponse.properties).sort(),
  }, {
    additionalProperties: false,
    required: ['schema_version', 'revision', 'fields'],
    properties: ['fields', 'revision', 'schema_version'],
  })
  assert.equal(schemas.SettingsResponse.properties.schema_version.const, 1)
  assert.equal(schemas.SettingsPatchRequest.properties.schema_version.const, 1)
  assert.equal(schemas.SettingFieldResponse.properties.sensitive.const, false)
  assert.equal(schemas.SettingFieldResponse.additionalProperties, false)
  assert.equal(schemas.SettingsPatchRequest.additionalProperties, false)

  assert.deepEqual({
    additionalProperties: schemas.CapabilityManifest.additionalProperties,
    required: schemas.CapabilityManifest.required ?? [],
    properties: schemas.CapabilityManifest.properties,
  }, {
    additionalProperties: false,
    required: [],
    properties: {
      api: { $ref: '#/components/schemas/ApiVersion' },
      modules: { default: [], items: { $ref: '#/components/schemas/CapabilityModule' }, maxItems: 128, title: 'Modules', type: 'array' },
      pagination: { $ref: '#/components/schemas/PaginationPolicy' },
      request_policy: { $ref: '#/components/schemas/RequestSizePolicy' },
    },
  })

  assert.deepEqual({
    additionalProperties: schemas.ApiVersion.additionalProperties,
    required: schemas.ApiVersion.required ?? [],
    properties: schemas.ApiVersion.properties,
  }, {
    additionalProperties: false,
    required: [],
    properties: {
      application_contract: { const: 'ancestryllm.application/0.3', default: 'ancestryllm.application/0.3', title: 'Application Contract', type: 'string' },
      contract: { const: 'ancestryllm.internal-api/1', default: 'ancestryllm.internal-api/1', title: 'Contract', type: 'string' },
      namespace: { const: '/api/v1', default: '/api/v1', title: 'Namespace', type: 'string' },
    },
  })

  assert.deepEqual({
    additionalProperties: schemas.CapabilityModule.additionalProperties,
    required: schemas.CapabilityModule.required,
    properties: schemas.CapabilityModule.properties,
  }, {
    additionalProperties: false,
    required: ['module_id', 'name', 'summary', 'actions'],
    properties: {
      actions: { items: { $ref: '#/components/schemas/CapabilityAction' }, maxItems: 128, minItems: 1, title: 'Actions', type: 'array' },
      module_id: { maxLength: 96, minLength: 1, pattern: '^[A-Za-z0-9._:-]+$', title: 'Module Id', type: 'string' },
      name: { maxLength: 128, minLength: 1, title: 'Name', type: 'string' },
      summary: { maxLength: 512, minLength: 1, title: 'Summary', type: 'string' },
    },
  })

  assert.deepEqual({
    additionalProperties: schemas.CapabilityAction.additionalProperties,
    required: schemas.CapabilityAction.required,
    properties: schemas.CapabilityAction.properties,
  }, {
    additionalProperties: false,
    required: ['dispatch_key', 'name', 'summary'],
    properties: {
      dispatch_key: { maxLength: 193, minLength: 3, pattern: '^[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+$', title: 'Dispatch Key', type: 'string' },
      name: { maxLength: 96, minLength: 1, pattern: '^[A-Za-z0-9._:-]+$', title: 'Name', type: 'string' },
      summary: { maxLength: 512, minLength: 1, title: 'Summary', type: 'string' },
    },
  })

  assert.deepEqual({
    additionalProperties: schemas.RequestSizePolicy.additionalProperties,
    required: schemas.RequestSizePolicy.required ?? [],
    properties: schemas.RequestSizePolicy.properties,
  }, {
    additionalProperties: false,
    required: [],
    properties: {
      max_body_bytes: { default: 1_048_576, maximum: 1_048_576, minimum: 1, title: 'Max Body Bytes', type: 'integer' },
      max_collection_items: { default: 1_000, maximum: 10_000, minimum: 1, title: 'Max Collection Items', type: 'integer' },
      max_json_depth: { default: 16, maximum: 64, minimum: 1, title: 'Max Json Depth', type: 'integer' },
      max_string_characters: { default: 65_536, maximum: 1_048_576, minimum: 1, title: 'Max String Characters', type: 'integer' },
    },
  })

  assert.deepEqual({
    additionalProperties: schemas.PaginationPolicy.additionalProperties,
    required: schemas.PaginationPolicy.required ?? [],
    properties: schemas.PaginationPolicy.properties,
  }, {
    additionalProperties: false,
    required: [],
    properties: {
      default_limit: { default: 25, maximum: 100, minimum: 1, title: 'Default Limit', type: 'integer' },
      maximum_cursor_characters: { default: 256, maximum: 1_024, minimum: 32, title: 'Maximum Cursor Characters', type: 'integer' },
      maximum_limit: { default: 100, maximum: 100, minimum: 1, title: 'Maximum Limit', type: 'integer' },
    },
  })
})
