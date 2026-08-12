// Reusable accessible application layout and navigation shell.

import { CircleCheck, Command, Heart, Home, Settings, Stethoscope } from 'lucide-react'
import { useEffect, useRef, useState, type MouseEvent, type ReactNode, type RefObject } from 'react'
import { CommandPalette } from './CommandPalette'
import { navigationItems, type AppRoute, type NavigationItem } from './contracts'

const routeIcons = {
  home: Home,
  diagnostics: Stethoscope,
  settings: Settings,
} as const

interface AppShellProps {
  readonly route: AppRoute
  readonly title: string
  readonly description: string
  readonly headingRef: RefObject<HTMLHeadingElement | null>
  readonly onNavigate: (item: NavigationItem) => void
  readonly children: ReactNode
}

export function AppShell({ route, title, description, headingRef, onNavigate, children }: AppShellProps) {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const paletteButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const openPalette = (event: KeyboardEvent) => {
      if (event.key.toLocaleLowerCase() !== 'k' || (!event.ctrlKey && !event.metaKey)) return
      event.preventDefault()
      setPaletteOpen(true)
    }
    window.addEventListener('keydown', openPalette)
    return () => window.removeEventListener('keydown', openPalette)
  }, [])

  const focusWorkspace = (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()
    document.querySelector<HTMLElement>('#workspace-content')?.focus()
  }

  const navigate = (event: MouseEvent<HTMLAnchorElement>, item: NavigationItem) => {
    event.preventDefault()
    onNavigate(item)
  }

  return <div className="app-shell">
    <a className="skip-link" href="#workspace-content" onClick={focusWorkspace}>Skip to workspace</a>
    <header className="app-header">
      <div className="brand"><Heart aria-hidden="true" /> <span>AncestryLLM</span></div>
      <div className="header-actions">
        <span className="local-state"><CircleCheck aria-hidden="true" />Local and offline</span>
        <button
          ref={paletteButton}
          type="button"
          className="command-button"
          aria-haspopup="dialog"
          aria-expanded={paletteOpen}
          onClick={() => setPaletteOpen(true)}
        >
          <Command aria-hidden="true" />
          <span>Navigate</span>
          <kbd>Ctrl K</kbd>
        </button>
      </div>
    </header>
    <nav className="primary-navigation" aria-label="Primary">
      <p className="navigation-label">Workspaces</p>
      {navigationItems.map((item) => {
        const Icon = routeIcons[item.route]
        return <a
          key={item.route}
          href={item.href}
          aria-current={route === item.route ? 'page' : undefined}
          onClick={(event) => navigate(event, item)}
        >
          <Icon aria-hidden="true" />
          <span>{item.label}</span>
        </a>
      })}
      <p className="navigation-note">This shell presents local state only.</p>
    </nav>
    <main id="workspace-content" tabIndex={-1} aria-labelledby="workspace-title">
      <div className="workspace-header">
        <p className="eyebrow">Current workspace</p>
        <h1 id="workspace-title" ref={headingRef} tabIndex={-1}>{title}</h1>
        <p className="lead">{description}</p>
      </div>
      {children}
    </main>
    <aside className="context-panel" aria-labelledby="context-help-title">
      <p className="eyebrow">Context and help</p>
      <h2 id="context-help-title">About this screen</h2>
      <p>{navigationItems.find((item) => item.route === route)?.description}</p>
      <p className="context-note"><strong>Local boundary:</strong> what appears here cannot grant access or change service policy.</p>
      <p><kbd>Ctrl K</kbd> opens keyboard navigation.</p>
    </aside>
    <CommandPalette
      open={paletteOpen}
      items={navigationItems}
      restoreFocusTo={paletteButton}
      onOpenChange={setPaletteOpen}
      onNavigate={onNavigate}
    />
  </div>
}
