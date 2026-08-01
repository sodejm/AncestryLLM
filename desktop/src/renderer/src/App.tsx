import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Heart, Home as HomeIcon, Settings as SettingsIcon, Stethoscope } from 'lucide-react'
import { Component, useEffect, useRef, useState, type ReactNode } from 'react'
import type { AncestryBridge, BridgeResult, DesktopColorScheme, StartupDiagnostics } from '../../shared-contract/desktop'
import { Button } from './components/Button'

type Route = 'home' | 'diagnostics' | 'settings'
const routeFromHash = (): Route => window.location.hash === '#/diagnostics' ? 'diagnostics' : window.location.hash === '#/settings' ? 'settings' : 'home'
const ancestryBridge = (): AncestryBridge => (window as unknown as { ancestry: AncestryBridge }).ancestry

function Gallery() { return <section aria-labelledby="gallery"><h2 id="gallery">Component gallery</h2><div className="gallery"><Button>Primary action</Button><Button variant="quiet">Quiet action</Button><span className="badge">Ready</span></div></section> }
class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (this.state.failed) return <main><div role="alert" className="error"><AlertTriangle aria-hidden="true"/><div><strong>AncestryLLM could not open this view.</strong><p>Restart AncestryLLM.</p></div></div></main>
    return this.props.children
  }
}
function Shell() {
  const [route, setRoute] = useState<Route>(routeFromHash)
  const [preferenceUpdatePending, setPreferenceUpdatePending] = useState(false)
  const [retryPending, setRetryPending] = useState(false)
  const [retryFailed, setRetryFailed] = useState(false)
  const heading = useRef<HTMLHeadingElement>(null)
  const alert = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const startup = useQuery({ queryKey: ['startup-diagnostics'], queryFn: () => ancestryBridge().getStartupDiagnostics() })
  const preferences = useQuery({ queryKey: ['preferences'], queryFn: () => ancestryBridge().getPreferences() })
  const refetchStartup = startup.refetch
  useEffect(() => { const update = () => setRoute(routeFromHash()); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update) }, [])
  useEffect(() => { heading.current?.focus() }, [route])
  useEffect(() => { if (startup.data && !startup.data.ok) alert.current?.focus() }, [startup.data])
  useEffect(() => { if (route === 'diagnostics') void refetchStartup() }, [refetchStartup, route])
  const result: BridgeResult<StartupDiagnostics> | undefined = startup.data
  const preferenceData = preferences.data?.ok ? preferences.data.data : undefined
  useEffect(() => {
    if (preferenceData) document.documentElement.dataset.theme = preferenceData.colorScheme
  }, [preferenceData])
  const status = result?.ok
    ? ({ starting: 'Starting', ready: 'Ready', degraded: 'Degraded', stopped: 'Stopped' } as const)[result.data.state]
    : 'Unavailable'
  const updateColorScheme = async (colorScheme: DesktopColorScheme) => {
    if (!preferenceData || preferenceUpdatePending) return
    setPreferenceUpdatePending(true)
    try {
      const updated = await ancestryBridge().updatePreferences({ expectedRevision: preferenceData.revision, colorScheme })
      if (updated.ok) {
        queryClient.setQueryData(['preferences'], updated)
        await preferences.refetch()
      }
    } finally {
      setPreferenceUpdatePending(false)
    }
  }
  const retrySidecar = async () => {
    if (retryPending) return
    setRetryPending(true)
    setRetryFailed(false)
    try {
      const retried = await ancestryBridge().retrySidecar()
      queryClient.setQueryData(['startup-diagnostics'], retried)
      setRetryFailed(!retried.ok)
    } catch {
      setRetryFailed(true)
    } finally {
      setRetryPending(false)
    }
  }
  return <div className="app-shell">
    <header><div className="brand"><Heart aria-hidden="true"/> <span>AncestryLLM</span></div><span className="privacy">Private by design</span></header>
    <nav aria-label="Primary"><a href="#/"><HomeIcon aria-hidden="true"/>Home</a><a href="#/diagnostics"><Stethoscope aria-hidden="true"/>Diagnostics</a><a href="#/settings"><SettingsIcon aria-hidden="true"/>Settings</a></nav>
    <main>
      {startup.isPending && <p role="status">Preparing your workspace…</p>}
      {(startup.isError || (result && !result.ok)) && <div ref={alert} tabIndex={-1} role="alert" className="error"><AlertTriangle aria-hidden="true"/><div><strong>Desktop diagnostics are temporarily unavailable.</strong><p>Restart AncestryLLM.</p></div></div>}
      {route === 'home' && <><h1 ref={heading} tabIndex={-1}>Home</h1><p>Your private family history workspace.</p><Gallery/></>}
      {route === 'diagnostics' && <><h1 ref={heading} tabIndex={-1}>Diagnostics</h1><p>Status: <span className="badge">{status}</span></p><p>No family records leave this device.</p>{result?.ok && result.data.state === 'degraded' && <div role="alert" className="error"><AlertTriangle aria-hidden="true"/><div><strong>Desktop service needs attention.</strong><p>Retry the local service once, or restart AncestryLLM if the problem continues.</p>{result.data.manualRetriesRemaining > 0 ? <Button disabled={retryPending} onClick={() => { void retrySidecar() }}>{retryPending ? 'Retrying…' : 'Retry desktop service'}</Button> : <p>Restart AncestryLLM to try again.</p>}</div></div>}{retryFailed && <p role="alert">The desktop service could not be restarted. Restart AncestryLLM.</p>}</>}
      {route === 'settings' && <><h1 ref={heading} tabIndex={-1}>Settings</h1>{preferences.isPending && <p role="status">Loading preferences…</p>}<fieldset disabled={!preferenceData || preferenceUpdatePending}><legend>Theme</legend>{(['system','light','dark'] as DesktopColorScheme[]).map((colorScheme) => <label key={colorScheme}><input type="radio" name="theme" checked={preferenceData?.colorScheme === colorScheme} onChange={() => { void updateColorScheme(colorScheme) }}/>{colorScheme}</label>)}</fieldset></>}
    </main>
  </div>
}
export function App() {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } }))
  return <AppErrorBoundary><QueryClientProvider client={client}><Shell/></QueryClientProvider></AppErrorBoundary>
}
