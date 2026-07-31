import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { AlertTriangle, Heart, Home as HomeIcon, Settings as SettingsIcon, Stethoscope } from 'lucide-react'
import { Component, useEffect, useRef, useState, type ReactNode } from 'react'
import type { AncestryBridge, BridgeResult, DesktopTheme, StartupData } from '../../shared-contract/desktop'
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
  const heading = useRef<HTMLHeadingElement>(null)
  const alert = useRef<HTMLDivElement>(null)
  const startup = useQuery({ queryKey: ['startup'], queryFn: () => ancestryBridge().startup() })
  useEffect(() => { const update = () => setRoute(routeFromHash()); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update) }, [])
  useEffect(() => { heading.current?.focus() }, [route])
  useEffect(() => { if (startup.data && !startup.data.ok) alert.current?.focus() }, [startup.data])
  const result: BridgeResult<StartupData> | undefined = startup.data
  return <div className="app-shell">
    <header><div className="brand"><Heart aria-hidden="true"/> <span>AncestryLLM</span></div><span className="privacy">Private by design</span></header>
    <nav aria-label="Primary"><a href="#/"><HomeIcon aria-hidden="true"/>Home</a><a href="#/diagnostics"><Stethoscope aria-hidden="true"/>Diagnostics</a><a href="#/settings"><SettingsIcon aria-hidden="true"/>Settings</a></nav>
    <main>
      {startup.isPending && <p role="status">Preparing your workspace…</p>}
      {(startup.isError || (result && !result.ok)) && <div ref={alert} tabIndex={-1} role="alert" className="error"><AlertTriangle aria-hidden="true"/><div><strong>Desktop diagnostics are temporarily unavailable.</strong><p>Restart AncestryLLM.</p></div></div>}
      {route === 'home' && <><h1 ref={heading} tabIndex={-1}>Home</h1><p>{result?.ok ? result.data.welcomeMessage : 'Your private family history workspace.'}</p><Gallery/></>}
      {route === 'diagnostics' && <><h1 ref={heading} tabIndex={-1}>Diagnostics</h1><p>Status: <span className="badge">{result?.ok ? result.data.diagnosticSummary : 'Unavailable'}</span></p><p>No family records leave this device.</p></>}
      {route === 'settings' && <><h1 ref={heading} tabIndex={-1}>Settings</h1><fieldset><legend>Theme</legend>{(['system','light','dark'] as DesktopTheme[]).map((theme) => <label key={theme}><input type="radio" name="theme" defaultChecked={theme === 'system'} onChange={() => { document.documentElement.dataset.theme = theme; void ancestryBridge().setTheme(theme) }}/>{theme}</label>)}</fieldset></>}
    </main>
  </div>
}
export function App() {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } }))
  return <AppErrorBoundary><QueryClientProvider client={client}><Shell/></QueryClientProvider></AppErrorBoundary>
}
