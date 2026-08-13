// Behavioral contracts for navigation, states, gates, errors, and dialog focus.

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { componentStateFixtures } from '../../../mock-bridge/shell-fixtures'
import { AsyncState } from './AsyncState'
import { CapabilityGate } from './CapabilityGate'
import { CodedErrorView } from './CodedErrorView'
import { navigationItems } from './contracts'

describe('desktop design-system contracts', () => {
  it('defines the complete bounded navigation contract', () => {
    expect(navigationItems.map(({ route, href, label }) => ({ route, href, label }))).toEqual([
      { route: 'home', href: '#/', label: 'Home' },
      { route: 'tasks', href: '#/tasks', label: 'Tasks' },
      { route: 'diagnostics', href: '#/diagnostics', label: 'Diagnostics' },
      { route: 'settings', href: '#/settings', label: 'Settings' },
    ])
    expect(Object.isFrozen(navigationItems)).toBe(true)
  })

  it('renders every shared asynchronous state with text that does not rely on color', () => {
    const { rerender } = render(<AsyncState state={componentStateFixtures.loading} />)

    for (const state of Object.values(componentStateFixtures)) {
      rerender(<AsyncState state={state} />)
      expect(screen.getByRole('heading', { level: 2, name: state.title })).toBeVisible()
      expect(screen.getByText(state.description)).toBeVisible()
      expect(screen.getByText(`State: ${state.label}`)).toBeVisible()
      if ('code' in state) expect(screen.getByText(`Code: ${state.code}`)).toBeVisible()
    }

    expect(document.body).not.toHaveTextContent(/token=|\/Users\/|private\.sock|stderr|api[_ -]?key/i)
  })

  it('keeps capability availability presentation-only', () => {
    const { rerender } = render(
      <CapabilityGate available={false} unavailable={<p>Unavailable in this workspace.</p>}>
        <button>Restricted action</button>
      </CapabilityGate>,
    )

    expect(screen.getByText('Unavailable in this workspace.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Restricted action' })).not.toBeInTheDocument()

    rerender(
      <CapabilityGate available unavailable={<p>Unavailable in this workspace.</p>}>
        <button>Restricted action</button>
      </CapabilityGate>,
    )
    expect(screen.getByRole('button', { name: 'Restricted action' })).toBeVisible()
  })

  it('renders only stable coded errors and bounded recovery guidance', async () => {
    const retry = vi.fn()
    const { rerender } = render(
      <CodedErrorView
        code="SIDECAR_UNAVAILABLE"
        title="The local service is unavailable."
        recovery="Restart AncestryLLM."
        actionLabel="Try again"
        onAction={retry}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Code: SIDECAR_UNAVAILABLE')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalledOnce()

    rerender(
      <CodedErrorView
        code="token=secret at /Users/example/private.sock"
        title="The local service is unavailable."
        recovery="Restart AncestryLLM."
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Code: UNEXPECTED_ERROR')
    expect(screen.getByRole('alert')).not.toHaveTextContent(/token=|\/Users\/|private\.sock/i)
  })
})
