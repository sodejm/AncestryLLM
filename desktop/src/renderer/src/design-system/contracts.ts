// Typed routes, navigation metadata, async states, and focus contracts.

export type AppRoute = 'home' | 'chat' | 'tasks' | 'diagnostics' | 'settings'

export interface NavigationItem {
  readonly route: AppRoute
  readonly href: '#/' | '#/chat' | '#/tasks' | '#/diagnostics' | '#/settings'
  readonly label: string
  readonly description: string
  readonly shortcut: string
}

export type AsyncStateKind =
  | 'loading'
  | 'empty'
  | 'offline'
  | 'degraded'
  | 'error'
  | 'success'
  | 'permission-denied'

export interface AsyncState {
  readonly kind: AsyncStateKind
  readonly label: string
  readonly title: string
  readonly description: string
  readonly code?: string
}

export interface DialogFocusContract {
  readonly initialFocus: 'filter'
  readonly closeOnEscape: true
  readonly restoreFocusOnDismiss: true
  readonly focusRouteHeadingOnSelection: true
}

export const commandPaletteFocusContract: DialogFocusContract = Object.freeze({
  initialFocus: 'filter',
  closeOnEscape: true,
  restoreFocusOnDismiss: true,
  focusRouteHeadingOnSelection: true,
})

export const navigationItems: readonly NavigationItem[] = Object.freeze([
  Object.freeze({
    route: 'home',
    href: '#/',
    label: 'Home',
    description: 'Review this device and its local startup state.',
    shortcut: 'H',
  }),
  Object.freeze({
    route: 'chat',
    href: '#/chat',
    label: 'Chat',
    description: 'Work in a transient conversation with an explicit provider and privacy scope.',
    shortcut: 'C',
  }),
  Object.freeze({
    route: 'tasks',
    href: '#/tasks',
    label: 'Tasks',
    description: 'Track local work and request safe cancellation.',
    shortcut: 'T',
  }),
  Object.freeze({
    route: 'diagnostics',
    href: '#/diagnostics',
    label: 'Diagnostics',
    description: 'Review bounded startup recovery guidance.',
    shortcut: 'D',
  }),
  Object.freeze({
    route: 'settings',
    href: '#/settings',
    label: 'Settings',
    description: 'Change local visual and application preferences.',
    shortcut: 'S',
  }),
])

export function routeFromHash(hash: string): AppRoute {
  if (hash === '#/chat') return 'chat'
  if (hash === '#/tasks') return 'tasks'
  if (hash === '#/diagnostics') return 'diagnostics'
  if (hash === '#/settings') return 'settings'
  return 'home'
}
