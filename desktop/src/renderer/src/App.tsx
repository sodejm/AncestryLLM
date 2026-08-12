import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { Component, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import type {
  AncestryBridge,
  ApplicationSetting,
  ApplicationSettingValue,
  BridgeErrorCode,
  BridgeResult,
  ConsentPreview,
  DesktopColorScheme,
  LocalRuntimeOperation,
  LocalRuntimePreview,
  PreferenceUpdate,
  ProviderDataClass,
  ProviderId,
  SecretReference,
  StartupDiagnostics,
  StartupDiagnosticComponentName,
  StartupFailure,
} from '../../shared-contract/desktop'
import {
  localRuntimeOperations,
  providerDataClasses,
  providerIds,
  secretReferences,
} from '../../shared-contract/desktop'
import { Button } from './components/Button'
import { AppShell } from './design-system/AppShell'
import { CodedErrorView } from './design-system/CodedErrorView'
import { navigationItems, routeFromHash, type AppRoute, type NavigationItem } from './design-system/contracts'

type PreferencePatch = Omit<PreferenceUpdate, 'expectedRevision'>

const ancestryBridge = (): AncestryBridge => (window as unknown as { ancestry: AncestryBridge }).ancestry

const startupLabels = {
  starting: 'Starting',
  ready: 'Ready',
  degraded: 'Degraded',
  stopped: 'Stopped',
} as const

const failureLabels: Record<Exclude<StartupFailure, null>, string> = {
  startup_failed: 'The desktop service did not start.',
  startup_timeout: 'The desktop service took too long to start.',
  incompatible_build: 'The desktop service is not compatible with this build.',
  crash_loop: 'The desktop service stopped repeatedly.',
}

const failureLabel = (failure: StartupFailure): string => failure
  ? failureLabels[failure]
  : 'The desktop service needs attention.'

const diagnosticComponentLabels: Readonly<Record<StartupDiagnosticComponentName, string>> = {
  configuration: 'Configuration',
  sqlcipher: 'Encrypted database support',
  keyring: 'Credential storage',
  workspace: 'Local workspace',
}

const secretLabels: Readonly<Record<SecretReference, string>> = {
  'openai.api_key': 'OpenAI API key',
  'anthropic.api_key': 'Anthropic API key',
  'gemini.api_key': 'Gemini API key',
  'openrouter.api_key': 'OpenRouter API key',
  'openrouter.management_key': 'OpenRouter management key',
  'database.master_key': 'Database master key',
}

const providerEndpoints: Readonly<Record<ProviderId, string>> = {
  ollama: 'http://127.0.0.1:11434',
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
  gemini: 'https://generativelanguage.googleapis.com',
  openrouter: 'https://openrouter.ai/api/v1',
}

const cloudProviderIds = providerIds.filter((providerId): providerId is Exclude<ProviderId, 'ollama'> => providerId !== 'ollama')

const dataClassLabels: Readonly<Record<ProviderDataClass, string>> = {
  public_genealogy: 'Public genealogy',
  deceased_person: 'Deceased person',
  living_person: 'Living person',
  possibly_living_person: 'Possibly living person',
  free_text_note: 'Free-text note',
  source_transcription: 'Source transcription',
  government_identifier: 'Government identifier',
}

const consentWarningLabels: Readonly<Record<ConsentPreview['warning_codes'][number], string>> = {
  LIVING_PERSON_DATA_INCLUDED: 'Living-person data will leave this device.',
  REMOTE_PROVIDER_SELECTED: 'This provider endpoint is remote.',
  REMOTE_RETENTION_ENABLED: 'The remote provider may retain payloads.',
}

const statusLabel = (status: 'missing' | 'present' | 'unavailable' | 'ready' | 'warning' | 'blocked'): string =>
  `${status.charAt(0).toUpperCase()}${status.slice(1)}`

function valueFromSettingInput(field: ApplicationSetting, input: HTMLInputElement | HTMLSelectElement): ApplicationSettingValue {
  return field.type === 'string' ? input.value : Number(input.value)
}

function ApplicationSettingsPanel() {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['application-settings'], queryFn: () => ancestryBridge().getSettings() })
  const [pendingKey, setPendingKey] = useState<ApplicationSetting['key'] | null>(null)
  const [failure, setFailure] = useState<BridgeErrorCode | null>(null)
  const data = settings.data?.ok ? settings.data.data : undefined
  const queryFailure = settings.data && !settings.data.ok
    ? settings.data.error.code
    : settings.isError
      ? 'INTERNAL_ERROR'
      : null

  const updateSetting = async (event: FormEvent<HTMLFormElement>, field: ApplicationSetting) => {
    event.preventDefault()
    if (!data || pendingKey) return
    const control = event.currentTarget.elements.namedItem('setting')
    if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement)) return
    setPendingKey(field.key)
    setFailure(null)
    try {
      const result = await ancestryBridge().updateSettings({
        schema_version: 1,
        expected_revision: data.revision,
        changes: { [field.key]: valueFromSettingInput(field, control) },
      })
      if (result.ok) {
        queryClient.setQueryData(['application-settings'], result)
      } else {
        setFailure(result.error.code)
      }
      await settings.refetch()
    } catch {
      setFailure('INTERNAL_ERROR')
    } finally {
      setPendingKey(null)
    }
  }

  const renderField = (field: ApplicationSetting) => <form
        className="application-setting"
        key={`${data?.revision ?? 'loading'}:${field.key}`}
        onSubmit={(event) => { void updateSetting(event, field) }}
      >
        <label htmlFor={`setting-${field.key}`}>{field.label}</label>
        <p id={`setting-help-${field.key}`} className="setting-help">{field.help}</p>
        {field.type === 'string'
          ? <select
              id={`setting-${field.key}`}
              name="setting"
              defaultValue={String(field.value)}
              aria-describedby={`setting-help-${field.key}`}
              disabled={pendingKey !== null}
            >
              {field.validation.allowed_values.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          : <input
              id={`setting-${field.key}`}
              name="setting"
              type="number"
              defaultValue={field.value}
              min={field.validation.minimum ?? undefined}
              max={field.validation.maximum ?? undefined}
              step={field.type === 'integer' ? 1 : 'any'}
              aria-describedby={`setting-help-${field.key}`}
              disabled={pendingKey !== null}
              required
            />}
        <div className="setting-meta">
          <span>Default: {field.default_value}</span>
          {field.restart_required && <span>Restart required</span>}
        </div>
        <Button type="submit" disabled={pendingKey !== null}>
          {pendingKey === field.key ? 'Saving…' : `Save ${field.label}`}
        </Button>
      </form>

  return <>
    <section className="settings-panel" aria-labelledby="provider-activation-title">
      <h2 id="provider-activation-title">Provider activation</h2>
      <p>Selecting a default coordinates application behavior; an API key never enables a provider by itself.</p>
      {settings.isPending && <p role="status">Loading application settings…</p>}
      {(failure || queryFailure) && <div role="alert" className="error settings-error">
        <AlertTriangle aria-hidden="true" />
        <div>
          <strong>{failure ? 'Application settings were not saved.' : 'Application settings are temporarily unavailable.'}</strong>
          <p className="error-code">Code: {failure ?? queryFailure}</p>
        </div>
      </div>}
      {data && <div className="application-settings-list">
        {data.fields.filter((field) => field.key === 'providers.default').map(renderField)}
      </div>}
    </section>
    <section className="settings-panel" aria-labelledby="limits-title">
      <h2 id="limits-title">Limits</h2>
      <p>Bound local and provider work before it begins. Each change is stored atomically.</p>
      {data && <div className="application-settings-list">
        {data.fields.filter((field) => field.key.startsWith('limits.')).map(renderField)}
      </div>}
    </section>
  </>
}

type ProviderProfileKind = 'local' | 'cloud'
type ProviderProfileDraft = Readonly<{
  name: string
  providerId: ProviderId
  model: string
  endpoint: string
}>
type TestedEndpoint = Readonly<{
  fingerprint: string
  endpointKind: 'loopback' | 'remote'
  destinationDigest: string
}>

const endpointFingerprint = (draft: ProviderProfileDraft): string => `${draft.providerId}\u0000${draft.endpoint}`

function ProviderConfigurationPanel() {
  const queryClient = useQueryClient()
  const configuration = useQuery({
    queryKey: ['provider-configuration'],
    queryFn: () => ancestryBridge().getProviderConfiguration(),
  })
  const data = configuration.data?.ok ? configuration.data.data : undefined
  const queryFailure = configuration.data && !configuration.data.ok
    ? configuration.data.error.code
    : configuration.isError
      ? 'INTERNAL_ERROR'
      : null
  const [localDraft, setLocalDraft] = useState<ProviderProfileDraft>({
    name: '',
    providerId: 'ollama',
    model: '',
    endpoint: providerEndpoints.ollama,
  })
  const [cloudDraft, setCloudDraft] = useState<ProviderProfileDraft>({
    name: '',
    providerId: 'openai',
    model: '',
    endpoint: providerEndpoints.openai,
  })
  const [testedEndpoints, setTestedEndpoints] = useState<Partial<Record<ProviderProfileKind, TestedEndpoint>>>({})
  const [profilePending, setProfilePending] = useState<`${'test' | 'save'}-${ProviderProfileKind}` | null>(null)
  const [profileFailure, setProfileFailure] = useState<BridgeErrorCode | null>(null)
  const [consentName, setConsentName] = useState('')
  const [profileName, setProfileName] = useState('')
  const [dataClasses, setDataClasses] = useState<ProviderDataClass[]>([])
  const [maxCost, setMaxCost] = useState('')
  const [retainPayloads, setRetainPayloads] = useState(false)
  const [consentPreview, setConsentPreview] = useState<ConsentPreview | null>(null)
  const [consentPending, setConsentPending] = useState<'preview' | 'save' | 'revoke' | null>(null)
  const [consentFailure, setConsentFailure] = useState<BridgeErrorCode | null>(null)

  useEffect(() => {
    if (!data || data.profiles.length === 0) return
    if (!data.profiles.some((profile) => profile.name === profileName)) {
      setProfileName(data.profiles[0]!.name)
      setConsentPreview(null)
    }
  }, [data, profileName])

  const updateDraft = (kind: ProviderProfileKind, patch: Partial<ProviderProfileDraft>) => {
    const update = (draft: ProviderProfileDraft): ProviderProfileDraft => ({ ...draft, ...patch })
    if (kind === 'local') setLocalDraft(update)
    else setCloudDraft(update)
    setTestedEndpoints((current) => ({ ...current, [kind]: undefined }))
    setProfileFailure(null)
  }

  const testEndpoint = async (kind: ProviderProfileKind, draft: ProviderProfileDraft) => {
    if (profilePending) return
    setProfilePending(`test-${kind}`)
    setProfileFailure(null)
    try {
      const result = await ancestryBridge().validateProviderEndpoint({
        schema_version: 1,
        provider_id: draft.providerId,
        endpoint: draft.endpoint,
      })
      if (result.ok) {
        setTestedEndpoints((current) => ({
          ...current,
          [kind]: {
            fingerprint: endpointFingerprint(draft),
            endpointKind: result.data.endpoint_kind,
            destinationDigest: result.data.destination_digest,
          },
        }))
      } else {
        setProfileFailure(result.error.code)
      }
    } catch {
      setProfileFailure('INTERNAL_ERROR')
    } finally {
      setProfilePending(null)
    }
  }

  const saveProfile = async (kind: ProviderProfileKind, draft: ProviderProfileDraft) => {
    const testedEndpoint = testedEndpoints[kind]
    if (!data || profilePending || testedEndpoint?.fingerprint !== endpointFingerprint(draft)) return
    setProfilePending(`save-${kind}`)
    setProfileFailure(null)
    try {
      const result = await ancestryBridge().createProviderProfile({
        schema_version: 1,
        expected_revision: data.revision,
        name: draft.name,
        provider_id: draft.providerId,
        model: draft.model,
        endpoint: draft.endpoint,
        endpoint_identity_sha256: testedEndpoint.destinationDigest,
      })
      if (result.ok) {
        queryClient.setQueryData(['provider-configuration'], result)
        setTestedEndpoints((current) => ({ ...current, [kind]: undefined }))
      } else {
        setProfileFailure(result.error.code)
      }
      await configuration.refetch()
    } catch {
      setProfileFailure('INTERNAL_ERROR')
    } finally {
      setProfilePending(null)
    }
  }

  const selectedProfile = data?.profiles.find((profile) => profile.name === profileName)
  const resetConsentPreview = () => {
    setConsentPreview(null)
    setConsentFailure(null)
  }

  const reviewConsent = async () => {
    if (!selectedProfile || dataClasses.length === 0 || consentPending) return
    setConsentPending('preview')
    setConsentFailure(null)
    try {
      const result = await ancestryBridge().previewConsent({
        schema_version: 1,
        provider_profile_name: selectedProfile.name,
        modules: ['summary'],
        purposes: ['genealogy-analysis'],
        data_classes: dataClasses,
        models: [selectedProfile.model],
        max_cost_usd: maxCost === '' ? null : Number(maxCost),
        retain_payloads: retainPayloads,
      })
      if (result.ok) setConsentPreview(result.data)
      else setConsentFailure(result.error.code)
    } catch {
      setConsentFailure('INTERNAL_ERROR')
    } finally {
      setConsentPending(null)
    }
  }

  const saveConsent = async () => {
    if (!data || !consentPreview || consentPending) return
    setConsentPending('save')
    setConsentFailure(null)
    try {
      const result = await ancestryBridge().createConsent({
        schema_version: 1,
        expected_revision: data.revision,
        name: consentName,
        preview: consentPreview,
      })
      if (result.ok) {
        queryClient.setQueryData(['provider-configuration'], result)
        setConsentPreview(null)
        setConsentName('')
        setDataClasses([])
        setMaxCost('')
        setRetainPayloads(false)
      } else {
        setConsentFailure(result.error.code)
      }
      await configuration.refetch()
    } catch {
      setConsentFailure('INTERNAL_ERROR')
    } finally {
      setConsentPending(null)
    }
  }

  const revokeConsent = async (name: string) => {
    if (!data || consentPending) return
    setConsentPending('revoke')
    setConsentFailure(null)
    try {
      const result = await ancestryBridge().revokeConsent({
        schema_version: 1,
        expected_revision: data.revision,
        name,
      })
      if (result.ok) queryClient.setQueryData(['provider-configuration'], result)
      else setConsentFailure(result.error.code)
      await configuration.refetch()
    } catch {
      setConsentFailure('INTERNAL_ERROR')
    } finally {
      setConsentPending(null)
    }
  }

  const profileSection = (
    kind: ProviderProfileKind,
    title: string,
    draft: ProviderProfileDraft,
  ) => {
    const testedEndpoint = testedEndpoints[kind]
    const tested = testedEndpoint?.fingerprint === endpointFingerprint(draft)
    const prefix = kind === 'local' ? 'local' : 'cloud'
    return <section className="settings-panel" aria-labelledby={`${prefix}-providers-title`}>
      <h2 id={`${prefix}-providers-title`}>{title}</h2>
      <p>{kind === 'local'
        ? 'Local profiles may use HTTP only on an explicit loopback address.'
        : 'Cloud profiles use reviewed HTTPS provider destinations. Custom remote endpoints are not accepted.'}</p>
      <form className="provider-form" onSubmit={(event) => { event.preventDefault(); void saveProfile(kind, draft) }}>
        {kind === 'cloud' && <>
          <label htmlFor="cloud-provider">Provider</label>
          <select
            id="cloud-provider"
            value={draft.providerId}
            disabled={profilePending !== null}
            onChange={(event) => {
              const providerId = event.currentTarget.value as Exclude<ProviderId, 'ollama'>
              updateDraft(kind, { providerId, endpoint: providerEndpoints[providerId] })
            }}
          >
            {cloudProviderIds.map((providerId) => <option key={providerId} value={providerId}>{providerId}</option>)}
          </select>
        </>}
        <label htmlFor={`${prefix}-profile-name`}>Profile name</label>
        <input
          id={`${prefix}-profile-name`}
          value={draft.name}
          maxLength={200}
          disabled={profilePending !== null}
          onChange={(event) => updateDraft(kind, { name: event.currentTarget.value })}
          required
        />
        <label htmlFor={`${prefix}-model`}>Model</label>
        <input
          id={`${prefix}-model`}
          value={draft.model}
          maxLength={200}
          disabled={profilePending !== null}
          onChange={(event) => updateDraft(kind, { model: event.currentTarget.value })}
          required
        />
        <label htmlFor={`${prefix}-endpoint`}>Endpoint</label>
        <input
          id={`${prefix}-endpoint`}
          type="url"
          value={draft.endpoint}
          maxLength={2048}
          readOnly={kind === 'cloud'}
          disabled={profilePending !== null}
          onChange={(event) => updateDraft(kind, { endpoint: event.currentTarget.value })}
          required
        />
        {tested && <p role="status">{testedEndpoint?.endpointKind === 'loopback'
          ? 'Endpoint tested: reachable on this device.'
          : 'Endpoint tested: reviewed remote destination is reachable.'}</p>}
        <div className="credential-actions">
          <Button
            type="button"
            variant="quiet"
            disabled={profilePending !== null || draft.endpoint.length === 0}
            onClick={() => { void testEndpoint(kind, draft) }}
          >
            {profilePending === `test-${kind}` ? 'Testing…' : `Test ${prefix} provider endpoint`}
          </Button>
          <Button
            type="submit"
            disabled={profilePending !== null || !tested || draft.name.length === 0 || draft.model.length === 0}
          >
            {profilePending === `save-${kind}` ? 'Saving…' : `Save ${prefix} provider profile`}
          </Button>
        </div>
      </form>
      {data && <div className="provider-profile-list">
        {data.profiles.filter((profile) => (kind === 'local') === (profile.endpoint_kind === 'loopback')).map((profile) => <article key={profile.name}>
          <h3>{profile.name}</h3>
          <p>{`${profile.provider_id} · ${profile.model} · ${profile.endpoint}`}</p>
          <p><span className="badge">{profile.enabled ? 'Configured' : 'Disabled'}</span></p>
        </article>)}
      </div>}
    </section>
  }

  return <>
    {configuration.isPending && <p role="status">Loading provider configuration…</p>}
    {(queryFailure || profileFailure) && <p role="alert" className="error-code">Code: {profileFailure ?? queryFailure}</p>}
    {profileSection('local', 'Local providers', localDraft)}
    {profileSection('cloud', 'Cloud providers', cloudDraft)}
    <section className="settings-panel" aria-labelledby="consent-title">
      <h2 id="consent-title">Consent</h2>
      <p>Cloud use requires a reviewed, named consent. Saving is atomic and stale reviews fail closed.</p>
      {consentFailure && <p role="alert" className="error-code">Code: {consentFailure}</p>}
      {data && <form className="provider-form" onSubmit={(event) => { event.preventDefault(); void saveConsent() }}>
        <label htmlFor="consent-name">Consent name</label>
        <input
          id="consent-name"
          value={consentName}
          maxLength={200}
          disabled={consentPending !== null}
          onChange={(event) => { setConsentName(event.currentTarget.value); resetConsentPreview() }}
          required
        />
        <label htmlFor="consent-profile">Provider profile</label>
        <select
          id="consent-profile"
          value={profileName}
          disabled={consentPending !== null || data.profiles.length === 0}
          onChange={(event) => { setProfileName(event.currentTarget.value); resetConsentPreview() }}
        >
          {data.profiles.length === 0 && <option value="">Create and test a provider profile first</option>}
          {data.profiles.map((profile) => <option key={profile.name} value={profile.name}>{profile.name}</option>)}
        </select>
        <p><strong>Module:</strong> summary</p>
        <p><strong>Purpose:</strong> genealogy-analysis</p>
        <fieldset>
          <legend>Data classes</legend>
          {providerDataClasses.map((dataClass) => <label key={dataClass}>
            <input
              type="checkbox"
              checked={dataClasses.includes(dataClass)}
              disabled={consentPending !== null}
              onChange={(event) => {
                const checked = event.currentTarget.checked
                setDataClasses((current) => checked
                  ? [...current, dataClass]
                  : current.filter((item) => item !== dataClass))
                resetConsentPreview()
              }}
            />
            <span>{dataClassLabels[dataClass]}</span>
          </label>)}
        </fieldset>
        <label htmlFor="consent-max-cost">Maximum cost in US dollars</label>
        <input
          id="consent-max-cost"
          type="number"
          value={maxCost}
          min="0"
          step="0.01"
          disabled={consentPending !== null}
          onChange={(event) => { setMaxCost(event.currentTarget.value); resetConsentPreview() }}
        />
        <label>
          <input
            type="checkbox"
            checked={retainPayloads}
            disabled={consentPending !== null}
            onChange={(event) => { setRetainPayloads(event.currentTarget.checked); resetConsentPreview() }}
          />
          <span>Allow provider retention</span>
        </label>
        <div className="credential-actions">
          <Button
            type="button"
            variant="quiet"
            disabled={consentPending !== null || !selectedProfile || dataClasses.length === 0}
            onClick={() => { void reviewConsent() }}
          >
            {consentPending === 'preview' ? 'Reviewing…' : 'Review consent'}
          </Button>
          <Button type="submit" disabled={consentPending !== null || consentName.length === 0 || !consentPreview}>
            {consentPending === 'save' ? 'Saving…' : 'Save consent'}
          </Button>
        </div>
      </form>}
      {consentPreview && <section className="consent-review" aria-labelledby="consent-review-title">
        <h3 id="consent-review-title">Consent review</h3>
        <p><strong>Provider:</strong> {consentPreview.provider_id}</p>
        <p><strong>Profile:</strong> {consentPreview.provider_profile_name}</p>
        <p><strong>Model:</strong> {consentPreview.models.join(', ')}</p>
        <p><strong>Purpose:</strong> {consentPreview.purposes.join(', ')}</p>
        <p><strong>Data classes:</strong> {consentPreview.data_classes.map((item) => dataClassLabels[item]).join(', ')}</p>
        <p><strong>Retention:</strong> {consentPreview.retain_payloads ? 'Allowed' : 'Not allowed'}</p>
        <p><strong>Budget:</strong> {consentPreview.max_cost_usd === null
          ? 'No explicit limit'
          : `$${consentPreview.max_cost_usd.toFixed(2)} USD`}</p>
        {consentPreview.warning_codes.map((warning) => <p className="consent-warning" key={warning}>
          <AlertTriangle aria-hidden="true" /> {consentWarningLabels[warning]}
        </p>)}
      </section>}
      {data && <div className="consent-list">
        {data.consents.map((consent) => <article key={consent.name}>
          <h3>{consent.name}</h3>
          <p>{`${consent.provider_id} · ${consent.provider_profile_name} · ${consent.models.join(', ')}`}</p>
          <p><span className="badge">{consent.active ? 'Active' : 'Revoked'}</span></p>
          {consent.active && <Button
            type="button"
            variant="quiet"
            disabled={consentPending !== null}
            onClick={() => { void revokeConsent(consent.name) }}
          >Revoke {consent.name}</Button>}
        </article>)}
      </div>}
    </section>
  </>
}

function DeploymentSettingsPanel() {
  return <section className="settings-panel" aria-labelledby="deployment-mode-title">
    <h2 id="deployment-mode-title">Deployment mode</h2>
    <div className="deployment-grid">
      <article>
        <h3>Local Desktop</h3>
        <p><span className="badge">Active</span></p>
        <p>The sidecar binds only to this device.</p>
      </article>
      <article>
        <h3>Connect Remote</h3>
        <p><span className="badge">Not available in this release</span></p>
        <p>Remote client connections remain disabled.</p>
      </article>
      <article>
        <h3>Host Remote</h3>
        <p><span className="badge">Not available in this release</span></p>
        <p>Non-loopback hosting remains disabled until the dedicated remote-host security boundary is complete.</p>
      </article>
    </div>
  </section>
}

const localRuntimeOperationLabels: Readonly<Record<LocalRuntimeOperation, string>> = {
  setup: 'Set up',
  start: 'Start',
  stop: 'Stop',
  repair: 'Repair',
  'uninstall-preserve': 'Uninstall and preserve data',
  'uninstall-delete': 'Uninstall and delete data',
}

const localRuntimeStateLabel = (state: string): string => state
  .split('-')
  .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
  .join(' ')

function LocalRuntimeSettingsPanel({ mutationsAllowed }: Readonly<{ mutationsAllowed: boolean }>) {
  const status = useQuery({
    queryKey: ['local-runtime-status'],
    queryFn: () => ancestryBridge().getLocalRuntimeStatus(),
  })
  const [operation, setOperation] = useState<LocalRuntimeOperation>('setup')
  const [offline, setOffline] = useState(false)
  const [preview, setPreview] = useState<LocalRuntimePreview | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [pending, setPending] = useState<'preview' | 'apply' | null>(null)
  const [failure, setFailure] = useState<BridgeErrorCode | null>(null)
  const data = status.data?.ok ? status.data.data : undefined
  const queryFailure = status.data && !status.data.ok
    ? status.data.error.code
    : status.isError
      ? 'INTERNAL_ERROR'
      : null

  const resetReview = () => {
    setPreview(null)
    setConfirmation('')
    setFailure(null)
  }

  const review = async () => {
    if (!mutationsAllowed || pending) return
    setPending('preview')
    setFailure(null)
    setPreview(null)
    setConfirmation('')
    try {
      const result = await ancestryBridge().previewLocalRuntime({
        schema_version: 1,
        operation,
        offline,
      })
      if (result.ok) setPreview(result.data)
      else setFailure(result.error.code)
    } catch {
      setFailure('INTERNAL_ERROR')
    } finally {
      setPending(null)
    }
  }

  const apply = async () => {
    if (
      !mutationsAllowed
      || !preview
      || pending
      || confirmation !== preview.confirmation_phrase
    ) return
    setPending('apply')
    setFailure(null)
    try {
      const result = await ancestryBridge().applyLocalRuntime({
        schema_version: 1,
        operation: preview.operation,
        offline: preview.offline,
        plan_revision: preview.plan_revision,
        confirmation,
      })
      if (result.ok) {
        setPreview(null)
        setConfirmation('')
        await status.refetch()
      } else {
        setFailure(result.error.code)
      }
    } catch {
      setFailure('INTERNAL_ERROR')
    } finally {
      setPending(null)
    }
  }

  return <section className="settings-panel" aria-labelledby="local-runtime-title">
    <h2 id="local-runtime-title">Local container runtime</h2>
    <p>AncestryLLM can manage an app-owned Colima runtime on supported Apple silicon Macs. Docker Desktop remains compatible but is not required.</p>
    {status.isPending && <p role="status">Inspecting the local runtime…</p>}
    {(failure || queryFailure) && <p role="alert" className="error-code">Code: {failure ?? queryFailure}</p>}
    {data && <>
      <p>{`State: ${localRuntimeStateLabel(data.state)}`} <span className="error-code">({data.code})</span></p>
      <p><strong>Host:</strong> {data.host.operating_system} {data.host.architecture}, macOS {data.host.macos_major}</p>
      <p><strong>Virtualization:</strong> {localRuntimeStateLabel(data.host.virtualization)} · <strong>Free space:</strong> {localRuntimeStateLabel(data.host.free_space)} · <strong>Existing Docker contexts:</strong> {data.host.existing_docker_contexts}</p>
      <p><strong>Measured allocation:</strong> {data.allocation.cpus} CPUs, {data.allocation.memory_gib} GiB memory, {data.allocation.disk_gib} GiB disk</p>
      <details>
        <summary>Detected components</summary>
        <ul>
          {data.components.map((component) => <li key={component.name}>
            {component.name} {component.version}: {component.installed ? 'Installed' : 'Not installed'}
          </li>)}
          <li>VM image {data.vm_image.version}: {data.vm_image.installed ? 'Installed' : 'Not installed'}</li>
        </ul>
      </details>
    </>}
    <div className="application-setting">
      <label htmlFor="local-runtime-operation">Operation</label>
      <select
        id="local-runtime-operation"
        value={operation}
        disabled={!mutationsAllowed || pending !== null}
        onChange={(event) => {
          setOperation(event.currentTarget.value as LocalRuntimeOperation)
          resetReview()
        }}
      >
        {localRuntimeOperations.map((item) => <option key={item} value={item}>{localRuntimeOperationLabels[item]}</option>)}
      </select>
      <label>
        <input
          type="checkbox"
          checked={offline}
          disabled={!mutationsAllowed || pending !== null}
          onChange={(event) => {
            setOffline(event.currentTarget.checked)
            resetReview()
          }}
        />
        <span>Use downloaded files only</span>
      </label>
      <p className="setting-help">Offline mode fails closed unless every reviewed artifact is already cached and still matches its digest.</p>
      <Button
        type="button"
        variant="quiet"
        disabled={!mutationsAllowed || pending !== null}
        onClick={() => { void review() }}
      >
        {pending === 'preview' ? 'Reviewing…' : `Review ${operation}`}
      </Button>
    </div>
    {preview && <section className="consent-review" aria-labelledby="local-runtime-review-title">
      <h3 id="local-runtime-review-title">Reviewed runtime plan</h3>
      <p><strong>Operation:</strong> {localRuntimeOperationLabels[preview.operation]}</p>
      <p><strong>Data:</strong> {preview.deletes_data ? 'Will delete app-owned runtime data' : preview.preserves_data ? 'Will preserve app-owned runtime data' : 'No data disposition change'}</p>
      <p><strong>Actions:</strong> {preview.actions.map((action) => action.code).join(', ')}</p>
      <h4>Reviewed downloads and licenses</h4>
      {preview.review.artifacts.map((artifact) => <article key={artifact.name}>
        <p><strong>{artifact.name} {artifact.version}</strong> ({artifact.repository})</p>
        <p>Asset: <code>{artifact.asset_name}</code></p>
        <p>SHA-256: <code>{artifact.sha256}</code></p>
        <p>Source: <code>{artifact.source_url}</code></p>
        <p>License: {artifact.license} · SHA-256: <code>{artifact.license_sha256}</code></p>
        <p>License source: <code>{artifact.license_url}</code></p>
      </article>)}
      <article>
        <p><strong>VM image {preview.review.vm_image.version}</strong> ({preview.review.vm_image.repository})</p>
        <p>Asset: <code>{preview.review.vm_image.asset_name}</code></p>
        <p>SHA-256: <code>{preview.review.vm_image.sha256}</code></p>
        <p>Source: <code>{preview.review.vm_image.source_url}</code></p>
      </article>
      <h4>Ownership and isolation</h4>
      <p><strong>Profile:</strong> {preview.review.ownership.profile}</p>
      <p><strong>Docker context:</strong> {preview.review.ownership.context}</p>
      <ul>
        <li>Loopback only: {preview.review.isolation.loopback_only ? 'Yes' : 'No'}</li>
        <li>Kubernetes enabled: {preview.review.isolation.kubernetes ? 'Yes' : 'No'}</li>
        <li>Privileged containers allowed: {preview.review.isolation.privileged_containers ? 'Yes' : 'No'}</li>
        <li>Renderer socket access: {preview.review.isolation.renderer_socket_access ? 'Yes' : 'No'}</li>
        <li>Container socket access: {preview.review.isolation.container_socket_access ? 'Yes' : 'No'}</li>
        <li>Cross-profile socket access: {preview.review.isolation.cross_profile_socket_access ? 'Yes' : 'No'}</li>
      </ul>
      <p>To authorize this exact plan, type <strong>{preview.confirmation_phrase}</strong>.</p>
      <label htmlFor="local-runtime-confirmation">Type the exact confirmation phrase</label>
      <input
        id="local-runtime-confirmation"
        type="text"
        value={confirmation}
        autoComplete="off"
        spellCheck={false}
        disabled={!mutationsAllowed || pending !== null}
        onChange={(event) => { setConfirmation(event.currentTarget.value) }}
      />
      <Button
        type="button"
        disabled={
          !mutationsAllowed
          || pending !== null
          || confirmation !== preview.confirmation_phrase
        }
        onClick={() => { void apply() }}
      >
        {pending === 'apply' ? 'Applying…' : `Apply ${preview.operation}`}
      </Button>
    </section>}
  </section>
}

function SecretControl({ reference }: Readonly<{ reference: SecretReference }>) {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const label = secretLabels[reference]
  const queryKey = ['secret-status', reference] as const
  const status = useQuery({ queryKey, queryFn: () => ancestryBridge().getSecretStatus({ reference }) })
  const [pending, setPending] = useState<'set' | 'delete' | null>(null)
  const [failure, setFailure] = useState<BridgeErrorCode | null>(null)
  const data = status.data?.ok ? status.data.data : undefined
  const queryFailure = status.data && !status.data.ok
    ? status.data.error.code
    : status.isError
      ? 'INTERNAL_ERROR'
      : null

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (pending) return
    const input = event.currentTarget.elements.namedItem('secret')
    if (!(input instanceof HTMLInputElement) || input.value.length === 0) return
    const value = input.value
    input.value = ''
    setPending('set')
    setFailure(null)
    try {
      const result = await ancestryBridge().setSecret({ reference, value })
      if (result.ok) {
        queryClient.setQueryData(queryKey, result)
      } else {
        setFailure(result.error.code)
      }
      await status.refetch()
    } catch {
      setFailure('INTERNAL_ERROR')
    } finally {
      input.value = ''
      setPending(null)
    }
  }

  const remove = async () => {
    if (pending) return
    if (inputRef.current) inputRef.current.value = ''
    setPending('delete')
    setFailure(null)
    try {
      const result = await ancestryBridge().deleteSecret({ reference })
      if (result.ok) {
        queryClient.setQueryData(queryKey, result)
      } else {
        setFailure(result.error.code)
      }
      await status.refetch()
    } catch {
      setFailure('INTERNAL_ERROR')
    } finally {
      if (inputRef.current) inputRef.current.value = ''
      setPending(null)
    }
  }

  return <section className="credential-control" aria-label={`${label} credential settings`}>
    <h3>{label}</h3>
    {status.isPending
      ? <p role="status">Checking status…</p>
      : data
        ? <p>{`Status: ${statusLabel(data.status)}`}</p>
        : <p>Status: Unavailable</p>}
    {(failure || queryFailure) && <p role="alert" className="error-code">Code: {failure ?? queryFailure}</p>}
    <form onSubmit={(event) => { void save(event) }}>
      <label htmlFor={`secret-${reference}`}>{label}</label>
      <input
        ref={inputRef}
        id={`secret-${reference}`}
        name="secret"
        type="password"
        autoComplete="new-password"
        spellCheck={false}
        disabled={pending !== null}
        required
      />
      <div className="credential-actions">
        <Button type="submit" disabled={pending !== null}>{pending === 'set' ? 'Saving…' : `Save ${label}`}</Button>
        <Button type="button" variant="quiet" disabled={pending !== null} onClick={() => { void remove() }}>
          {pending === 'delete' ? 'Deleting…' : `Delete ${label}`}
        </Button>
      </div>
    </form>
  </section>
}

function CredentialSettingsPanel() {
  return <section className="settings-panel" aria-labelledby="secrets-title">
    <h2 id="secrets-title">Secrets</h2>
    <p>Credential values are write-only and stored in the operating system keyring. Existing values are never displayed.</p>
    <div className="credential-grid">
      {secretReferences.map((reference) => <SecretControl key={reference} reference={reference} />)}
    </div>
  </section>
}

class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (this.state.failed) {
      return <main>
        <div role="alert" className="error">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>AncestryLLM could not open this view.</strong>
            <p>Restart AncestryLLM.</p>
          </div>
        </div>
      </main>
    }
    return this.props.children
  }
}

function Shell() {
  const [route, setRoute] = useState<AppRoute>(() => routeFromHash(window.location.hash))
  const [reviewingWelcome, setReviewingWelcome] = useState(false)
  const [onboardingFailure, setOnboardingFailure] = useState<BridgeErrorCode | null>(null)
  const [preferenceUpdatePending, setPreferenceUpdatePending] = useState(false)
  const [preferenceFailure, setPreferenceFailure] = useState<BridgeErrorCode | null>(null)
  const [retryPending, setRetryPending] = useState(false)
  const [retryFailure, setRetryFailure] = useState<BridgeErrorCode | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const lastFocusedHeadingKey = useRef<string | null>(null)
  const startupAlert = useRef<HTMLDivElement>(null)
  const onboardingAlert = useRef<HTMLDivElement>(null)
  const preferenceAlert = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const appInfo = useQuery({ queryKey: ['app-info'], queryFn: () => ancestryBridge().getAppInfo() })
  const startup = useQuery({ queryKey: ['startup-diagnostics'], queryFn: () => ancestryBridge().getStartupDiagnostics() })
  const startupResult: BridgeResult<StartupDiagnostics> | undefined = startup.data
  const startupData = startupResult?.ok ? startupResult.data : undefined
  const startupAllowsMutations = startupData?.state === 'ready'
    && startupData.report?.status === 'ready'
    && startupData.report.components.every((component) => !component.blocks_mutations)
  const capabilities = useQuery({
    queryKey: ['capabilities'],
    queryFn: () => ancestryBridge().getCapabilities(),
    enabled: startupAllowsMutations,
  })
  const preferences = useQuery({ queryKey: ['preferences'], queryFn: () => ancestryBridge().getPreferences() })
  const refetchStartup = startup.refetch

  useEffect(() => {
    const update = () => setRoute(routeFromHash(window.location.hash))
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])

  useEffect(() => {
    if (startup.isError || (startup.data && !startup.data.ok)) startupAlert.current?.focus()
  }, [startup.data, startup.isError])

  useEffect(() => {
    if (preferenceFailure) preferenceAlert.current?.focus()
  }, [preferenceFailure])

  useEffect(() => {
    if (route === 'diagnostics') void refetchStartup()
  }, [refetchStartup, route])

  const appData = appInfo.data?.ok ? appInfo.data.data : undefined
  const capabilityData = capabilities.data?.ok ? capabilities.data.data : undefined
  const preferenceData = preferences.data?.ok ? preferences.data.data : undefined
  const showWelcome = route === 'home'
    && !preferences.isPending
    && (!preferenceData?.onboardingCompleted || reviewingWelcome)
  const headingFocusKey = `${route}:${showWelcome ? 'welcome' : 'workspace'}`

  useEffect(() => {
    if (startup.isError || (startup.data && !startup.data.ok)) return
    if (lastFocusedHeadingKey.current === headingFocusKey) return
    lastFocusedHeadingKey.current = headingFocusKey
    heading.current?.focus()
  }, [headingFocusKey, startup.data, startup.isError])

  useEffect(() => {
    if (onboardingFailure) onboardingAlert.current?.focus()
  }, [onboardingFailure])

  useEffect(() => {
    if (!preferenceData) return
    document.documentElement.dataset.theme = preferenceData.colorScheme
    document.documentElement.dataset.reducedMotion = String(preferenceData.reducedMotion)
  }, [preferenceData])

  const startupStatus = startupData ? startupLabels[startupData.state] : 'Unavailable'

  const updatePreferences = async (patch: PreferencePatch) => {
    if (!preferenceData || preferenceUpdatePending || !startupAllowsMutations) return
    setPreferenceUpdatePending(true)
    setPreferenceFailure(null)
    try {
      const updated = await ancestryBridge().updatePreferences({
        expectedRevision: preferenceData.revision,
        ...patch,
      })
      if (updated.ok) {
        queryClient.setQueryData(['preferences'], updated)
        await preferences.refetch()
      } else {
        setPreferenceFailure(updated.error.code)
        await preferences.refetch()
      }
    } catch {
      setPreferenceFailure('INTERNAL_ERROR')
    } finally {
      setPreferenceUpdatePending(false)
    }
  }

  const completeOnboarding = async () => {
    if (!preferenceData || preferenceUpdatePending || !startupAllowsMutations) return
    setPreferenceUpdatePending(true)
    setOnboardingFailure(null)
    try {
      const updated = await ancestryBridge().updatePreferences({
        expectedRevision: preferenceData.revision,
        onboardingCompleted: true,
      })
      const refreshed = await preferences.refetch()
      const refreshedData = refreshed.data?.ok ? refreshed.data.data : undefined
      if (refreshedData?.onboardingCompleted === true) {
        setReviewingWelcome(false)
      } else {
        setOnboardingFailure(updated.ok ? 'PREFERENCES_UNAVAILABLE' : updated.error.code)
      }
    } catch {
      setOnboardingFailure('INTERNAL_ERROR')
    } finally {
      setPreferenceUpdatePending(false)
    }
  }

  const retrySidecar = async () => {
    if (retryPending) return
    setRetryPending(true)
    setRetryFailure(null)
    try {
      const retried = await ancestryBridge().retrySidecar()
      queryClient.setQueryData(['startup-diagnostics'], retried)
      if (!retried.ok) setRetryFailure(retried.error.code)
    } catch {
      setRetryFailure('INTERNAL_ERROR')
    } finally {
      setRetryPending(false)
    }
  }

  const startupFailed = startup.isError || (startupResult && !startupResult.ok)
  const startupFailureCode = startupResult && !startupResult.ok
    ? startupResult.error.code
    : startup.isError
      ? 'INTERNAL_ERROR'
      : null
  const preferenceQueryCode = preferences.data && !preferences.data.ok
    ? preferences.data.error.code
    : preferences.isError
      ? 'INTERNAL_ERROR'
      : null

  const workspaceCopy: Readonly<Record<AppRoute, { title: string, description: string }>> = {
    home: {
      title: showWelcome ? 'Welcome to AncestryLLM' : 'Home',
      description: showWelcome
        ? 'Your desktop control shell stays local to this device.'
        : 'A calm overview of this desktop shell.',
    },
    diagnostics: {
      title: 'Diagnostics',
      description: 'Review local startup state and bounded recovery guidance.',
    },
    settings: {
      title: 'Settings',
      description: 'Choose local preferences, application behavior, and write-only credentials.',
    },
  }

  const navigate = (item: NavigationItem) => {
    window.location.hash = item.href
    setRoute(item.route)
  }

  return <AppShell
    route={route}
    title={workspaceCopy[route].title}
    description={workspaceCopy[route].description}
    headingRef={heading}
    onNavigate={navigate}
  >
      {startupFailed && startupFailureCode && <CodedErrorView
        focusRef={startupAlert}
        code={startupFailureCode}
        title="Desktop diagnostics are temporarily unavailable."
        recovery="Restart AncestryLLM."
      />}

      {route === 'home' && preferences.isPending && <p role="status">Loading welcome…</p>}

      {showWelcome && <section className="welcome" aria-labelledby="workspace-title">
        <div className="welcome-grid">
          <section className="summary-card" aria-labelledby="welcome-local-desktop">
            <h2 id="welcome-local-desktop">Local Desktop</h2>
            <p><span className="badge">Recommended</span></p>
            <p>Work on this device with a private loopback service and offline-first defaults.</p>
          </section>
          <section className="summary-card" aria-labelledby="welcome-connect-remote">
            <h2 id="welcome-connect-remote">Connect Remote</h2>
            <p><span className="badge">Not available in this release</span></p>
            <p>Connecting to another host will always require explicit setup and consent.</p>
          </section>
          <section className="summary-card" aria-labelledby="welcome-host-remote">
            <h2 id="welcome-host-remote">Host Remote</h2>
            <p><span className="badge">Not available in this release</span></p>
            <p>Advanced hosting remains disabled; this release does not bind publicly or alter firewall rules.</p>
          </section>
          <section className="summary-card" aria-labelledby="welcome-private">
            <h2 id="welcome-private">Private and offline</h2>
            <p>No account, provider, API key, genealogy data, or cloud consent is requested here.</p>
          </section>
          <section className="summary-card" aria-labelledby="welcome-scope">
            <h2 id="welcome-scope">What this shell supports</h2>
            <p>Use Home for a local status overview and Diagnostics for startup recovery.</p>
          </section>
          <section className="summary-card" aria-labelledby="welcome-recovery">
            <h2 id="welcome-recovery">Recovery and updates</h2>
            <p>Updates are installed manually. Diagnostics remains available if the desktop service cannot start.</p>
            <a href="#/diagnostics">Open Diagnostics</a>
          </section>
        </div>
        {(onboardingFailure || (!preferenceData && preferenceQueryCode)) && <div
          ref={onboardingAlert}
          tabIndex={-1}
          role="alert"
          className="error welcome-error"
        >
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>{onboardingFailure ? 'Welcome progress was not saved.' : 'Welcome progress is temporarily unavailable.'}</strong>
            <p className="error-code">Code: {onboardingFailure ?? preferenceQueryCode}</p>
            <p>Open Diagnostics or restart AncestryLLM.</p>
          </div>
        </div>}
        <div className="welcome-actions">
          {reviewingWelcome
            ? <Button variant="quiet" onClick={() => setReviewingWelcome(false)}>Back to Home</Button>
            : startupAllowsMutations ? <Button
                disabled={!preferenceData || preferenceUpdatePending}
                onClick={() => { void completeOnboarding() }}
              >
                {preferenceUpdatePending ? 'Saving…' : onboardingFailure ? 'Try again' : 'Continue to Home'}
              </Button>
              : <Button
                  variant="quiet"
                  onClick={() => navigate(navigationItems[1]!)}
                >
                  Open read-only diagnostics
                </Button>}
        </div>
      </section>}

      {route === 'home' && !preferences.isPending && !showWelcome && <>
        <div className="summary-grid">
          <section className="summary-card" aria-labelledby="application-summary">
            <h2 id="application-summary">Application</h2>
            {appInfo.isPending && <p role="status">Loading application details…</p>}
            {appData && <>
              <p className="summary-value">{appData.applicationName}</p>
              <p>{appData.appVersion}</p>
              <p>{appData.buildChannel === 'packaged' ? 'Packaged build' : 'Development build'}</p>
            </>}
            {(appInfo.isError || (appInfo.data && !appInfo.data.ok)) && <p>Application details are unavailable.</p>}
          </section>
          <section className="summary-card" aria-labelledby="offline-summary">
            <h2 id="offline-summary">Offline posture</h2>
            <p className="summary-value">Local desktop shell</p>
            <p>The control channel stays on this device.</p>
          </section>
          <section className="summary-card" aria-labelledby="startup-summary">
            <h2 id="startup-summary">Startup state</h2>
            {startup.isPending ? <p role="status">Checking startup state…</p> : <p><span className="badge">{startupStatus}</span></p>}
          </section>
          <section className="summary-card" aria-labelledby="capabilities-summary">
            <h2 id="capabilities-summary">Capabilities</h2>
            {!startupAllowsMutations && <p>Capabilities stay unavailable until startup diagnostics pass.</p>}
            {startupAllowsMutations && capabilities.isPending && <p role="status">Checking capabilities…</p>}
            {capabilityData && <p>{capabilityData.modules.length === 0
              ? 'No control capabilities are currently available.'
              : `${capabilityData.modules.length} local control ${capabilityData.modules.length === 1 ? 'module is' : 'modules are'} available.`}</p>}
            {(capabilities.isError || (capabilities.data && !capabilities.data.ok)) && <p>Capabilities are unavailable while the desktop service recovers.</p>}
          </section>
        </div>
        <div className="home-actions">
          <Button variant="quiet" onClick={() => setReviewingWelcome(true)}>Review welcome</Button>
        </div>
      </>}

      {route === 'diagnostics' && <>
        <section className="summary-card diagnostics-summary" aria-labelledby="service-status">
          <h2 id="service-status">Desktop service</h2>
          {startup.isPending ? <p role="status">Checking startup state…</p> : <p>Status: <span className="badge">{startupStatus}</span></p>}
          <p>Diagnostic details stay within this shell.</p>
        </section>
        {startupData?.report && <section className="settings-panel" aria-labelledby="startup-checks-title">
          <h2 id="startup-checks-title">Startup checks</h2>
          <p>{`Platform: ${startupData.report.platform.operating_system} ${startupData.report.platform.architecture}`}</p>
          <div className="diagnostic-list">
            {startupData.report.components.map((component) => <section
              className="diagnostic-item"
              key={component.component}
              aria-labelledby={`diagnostic-${component.component}`}
            >
              <h3 id={`diagnostic-${component.component}`}>{diagnosticComponentLabels[component.component]}</h3>
              <p><span className="badge">{statusLabel(component.status)}</span></p>
              <p className="error-code">{component.code}</p>
              <p>{component.message}</p>
              {component.remediation && <p>{component.remediation}</p>}
              {component.restart_required && <p>Restart required after remediation.</p>}
            </section>)}
          </div>
        </section>}
        {startupData && (startupData.state === 'degraded' || startupData.state === 'stopped') && <div role="alert" className="error">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>{failureLabel(startupData.failure)}</strong>
            {startupData.manualRetriesRemaining > 0
              ? <>
                <p>Retry the desktop service once, or restart AncestryLLM if the problem continues.</p>
                <Button disabled={retryPending} onClick={() => { void retrySidecar() }}>
                  {retryPending ? 'Retrying…' : 'Retry desktop service'}
                </Button>
              </>
              : <p>Restart AncestryLLM to try again.</p>}
          </div>
        </div>}
        {retryFailure && <div role="alert" className="error">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>The desktop service could not be restarted.</strong>
            <p className="error-code">Code: {retryFailure}</p>
            <p>Restart AncestryLLM.</p>
          </div>
        </div>}
      </>}

      {route === 'settings' && <>
        {preferences.isPending && <p role="status">Loading preferences…</p>}
        {(preferenceFailure || preferenceQueryCode) && <div ref={preferenceAlert} tabIndex={-1} role="alert" className="error">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>{preferenceFailure ? 'Preferences were not saved.' : 'Preferences are temporarily unavailable.'}</strong>
            <p className="error-code">Code: {preferenceFailure ?? preferenceQueryCode}</p>
            <p>{preferenceFailure ? 'Review the current settings and try again.' : 'Restart AncestryLLM.'}</p>
          </div>
        </div>}
        {!startupAllowsMutations && !startup.isPending && <p className="context-note">Settings are read-only while startup diagnostics are degraded.</p>}
        <LocalRuntimeSettingsPanel mutationsAllowed={startupAllowsMutations} />
        {startupAllowsMutations && <div className="settings-stack">
          <section className="settings-panel" aria-labelledby="general-settings-title">
            <h2 id="general-settings-title">General</h2>
            <p>Choose how this desktop application appears and moves. These preferences remain local.</p>
            <fieldset disabled={!preferenceData || preferenceUpdatePending}>
              <legend>Theme</legend>
              {(['system', 'light', 'dark'] as DesktopColorScheme[]).map((colorScheme) => <label key={colorScheme}>
                <input
                  type="radio"
                  name="theme"
                  checked={preferenceData?.colorScheme === colorScheme}
                  onChange={() => { void updatePreferences({ colorScheme }) }}
                />
                <span className="option-label">{colorScheme}</span>
              </label>)}
            </fieldset>
            <fieldset disabled={!preferenceData || preferenceUpdatePending}>
              <legend>Motion</legend>
              <label>
                <input
                  type="checkbox"
                  checked={preferenceData?.reducedMotion ?? false}
                  onChange={(event) => { void updatePreferences({ reducedMotion: event.currentTarget.checked }) }}
                />
                <span>Reduce motion</span>
              </label>
            </fieldset>
          </section>
          <section className="settings-panel" aria-labelledby="storage-settings-title">
            <h2 id="storage-settings-title">Storage</h2>
            <p>Application data remains in the encrypted local workspace. RootsMagic databases are immutable inputs and are never modified.</p>
          </section>
          <DeploymentSettingsPanel />
          <ProviderConfigurationPanel />
          <section className="settings-panel" aria-labelledby="privacy-settings-title">
            <h2 id="privacy-settings-title">Privacy</h2>
            <p>Provider none remains network-free. Cloud requests require an enabled profile and an active consent that names the purpose, model, data classes, retention choice, and budget.</p>
          </section>
          <ApplicationSettingsPanel />
          <CredentialSettingsPanel />
        </div>}
      </>}
  </AppShell>
}

export function App() {
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  }))
  return <AppErrorBoundary>
    <QueryClientProvider client={client}><Shell /></QueryClientProvider>
  </AppErrorBoundary>
}
