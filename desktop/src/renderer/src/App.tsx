import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { Component, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import type {
  AncestryBridge,
  ApplicationSetting,
  ApplicationSettingValue,
  BridgeErrorCode,
  BridgeResult,
  DesktopColorScheme,
  PreferenceUpdate,
  SecretReference,
  StartupDiagnostics,
  StartupDiagnosticComponentName,
  StartupFailure,
} from '../../shared-contract/desktop'
import { secretReferences } from '../../shared-contract/desktop'
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

  return <section className="settings-panel" aria-labelledby="application-settings-title">
    <h2 id="application-settings-title">Application settings</h2>
    <p>These values are stored atomically. Credential values are managed separately below.</p>
    {settings.isPending && <p role="status">Loading application settings…</p>}
    {(failure || queryFailure) && <div role="alert" className="error settings-error">
      <AlertTriangle aria-hidden="true" />
      <div>
        <strong>{failure ? 'Application settings were not saved.' : 'Application settings are temporarily unavailable.'}</strong>
        <p className="error-code">Code: {failure ?? queryFailure}</p>
      </div>
    </div>}
    {data && <div className="application-settings-list">
      {data.fields.map((field) => <form
        className="application-setting"
        key={`${data.revision}:${field.key}`}
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
      </form>)}
    </div>}
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
  return <section className="settings-panel" aria-labelledby="credentials-title">
    <h2 id="credentials-title">Credentials</h2>
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
        {startupAllowsMutations && <div className="settings-stack">
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
