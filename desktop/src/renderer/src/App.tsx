import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Heart, Home as HomeIcon, Settings as SettingsIcon, Stethoscope } from 'lucide-react'
import { Component, useEffect, useRef, useState, type ReactNode } from 'react'
import type {
  AncestryBridge,
  BridgeErrorCode,
  BridgeResult,
  DesktopColorScheme,
  PreferenceUpdate,
  StartupDiagnostics,
  StartupFailure,
} from '../../shared-contract/desktop'
import { Button } from './components/Button'

type Route = 'home' | 'diagnostics' | 'settings'
type PreferencePatch = Omit<PreferenceUpdate, 'expectedRevision'>

const routeFromHash = (): Route => window.location.hash === '#/diagnostics'
  ? 'diagnostics'
  : window.location.hash === '#/settings'
    ? 'settings'
    : 'home'

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
  const [route, setRoute] = useState<Route>(routeFromHash)
  const [preferenceUpdatePending, setPreferenceUpdatePending] = useState(false)
  const [preferenceFailure, setPreferenceFailure] = useState<BridgeErrorCode | null>(null)
  const [retryPending, setRetryPending] = useState(false)
  const [retryFailure, setRetryFailure] = useState<BridgeErrorCode | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const startupAlert = useRef<HTMLDivElement>(null)
  const preferenceAlert = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const appInfo = useQuery({ queryKey: ['app-info'], queryFn: () => ancestryBridge().getAppInfo() })
  const startup = useQuery({ queryKey: ['startup-diagnostics'], queryFn: () => ancestryBridge().getStartupDiagnostics() })
  const capabilities = useQuery({ queryKey: ['capabilities'], queryFn: () => ancestryBridge().getCapabilities() })
  const preferences = useQuery({ queryKey: ['preferences'], queryFn: () => ancestryBridge().getPreferences() })
  const refetchStartup = startup.refetch

  useEffect(() => {
    const update = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])

  useEffect(() => {
    heading.current?.focus()
  }, [route])

  useEffect(() => {
    if (startup.isError || (startup.data && !startup.data.ok)) startupAlert.current?.focus()
  }, [startup.data, startup.isError])

  useEffect(() => {
    if (preferenceFailure) preferenceAlert.current?.focus()
  }, [preferenceFailure])

  useEffect(() => {
    if (route === 'diagnostics') void refetchStartup()
  }, [refetchStartup, route])

  const startupResult: BridgeResult<StartupDiagnostics> | undefined = startup.data
  const startupData = startupResult?.ok ? startupResult.data : undefined
  const appData = appInfo.data?.ok ? appInfo.data.data : undefined
  const capabilityData = capabilities.data?.ok ? capabilities.data.data : undefined
  const preferenceData = preferences.data?.ok ? preferences.data.data : undefined

  useEffect(() => {
    if (!preferenceData) return
    document.documentElement.dataset.theme = preferenceData.colorScheme
    document.documentElement.dataset.reducedMotion = String(preferenceData.reducedMotion)
  }, [preferenceData])

  const startupStatus = startupData ? startupLabels[startupData.state] : 'Unavailable'

  const updatePreferences = async (patch: PreferencePatch) => {
    if (!preferenceData || preferenceUpdatePending) return
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

  return <div className="app-shell">
    <header>
      <div className="brand"><Heart aria-hidden="true" /> <span>AncestryLLM</span></div>
      <span className="privacy">Private by design</span>
    </header>
    <nav aria-label="Primary">
      <a href="#/" aria-current={route === 'home' ? 'page' : undefined}><HomeIcon aria-hidden="true" />Home</a>
      <a href="#/diagnostics" aria-current={route === 'diagnostics' ? 'page' : undefined}><Stethoscope aria-hidden="true" />Diagnostics</a>
      <a href="#/settings" aria-current={route === 'settings' ? 'page' : undefined}><SettingsIcon aria-hidden="true" />Settings</a>
    </nav>
    <main>
      {startupFailed && <div ref={startupAlert} tabIndex={-1} role="alert" className="error">
        <AlertTriangle aria-hidden="true" />
        <div>
          <strong>Desktop diagnostics are temporarily unavailable.</strong>
          {startupFailureCode && <p className="error-code">Code: {startupFailureCode}</p>}
          <p>Restart AncestryLLM.</p>
        </div>
      </div>}

      {route === 'home' && <>
        <h1 ref={heading} tabIndex={-1}>Home</h1>
        <p className="lead">A calm overview of this desktop shell.</p>
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
            {capabilities.isPending && <p role="status">Checking capabilities…</p>}
            {capabilityData && <p>{capabilityData.modules.length === 0
              ? 'No control capabilities are currently available.'
              : `${capabilityData.modules.length} local control ${capabilityData.modules.length === 1 ? 'module is' : 'modules are'} available.`}</p>}
            {(capabilities.isError || (capabilities.data && !capabilities.data.ok)) && <p>Capabilities are unavailable while the desktop service recovers.</p>}
          </section>
        </div>
      </>}

      {route === 'diagnostics' && <>
        <h1 ref={heading} tabIndex={-1}>Diagnostics</h1>
        <section className="summary-card diagnostics-summary" aria-labelledby="service-status">
          <h2 id="service-status">Desktop service</h2>
          {startup.isPending ? <p role="status">Checking startup state…</p> : <p>Status: <span className="badge">{startupStatus}</span></p>}
          <p>Diagnostic details stay within this shell.</p>
        </section>
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
        <h1 ref={heading} tabIndex={-1}>Settings</h1>
        <p className="lead">Choose how the desktop shell looks and moves.</p>
        {preferences.isPending && <p role="status">Loading preferences…</p>}
        {(preferenceFailure || preferenceQueryCode) && <div ref={preferenceAlert} tabIndex={-1} role="alert" className="error">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>{preferenceFailure ? 'Preferences were not saved.' : 'Preferences are temporarily unavailable.'}</strong>
            <p className="error-code">Code: {preferenceFailure ?? preferenceQueryCode}</p>
            <p>{preferenceFailure ? 'Review the current settings and try again.' : 'Restart AncestryLLM.'}</p>
          </div>
        </div>}
        <div className="settings-stack">
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
        </div>
      </>}
    </main>
  </div>
}

export function App() {
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  }))
  return <AppErrorBoundary>
    <QueryClientProvider client={client}><Shell /></QueryClientProvider>
  </AppErrorBoundary>
}
