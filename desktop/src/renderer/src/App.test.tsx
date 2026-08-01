import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AncestryBridge } from '../../shared-contract/desktop'
import { createMockAncestryBridge } from '../../mock-bridge/desktop'
import { App } from './App'

describe('accessible desktop shell', () => {
  beforeEach(() => { window.location.hash = '#/' })
  it('supports keyboard navigation across Home, Diagnostics, and Settings', async () => {
    const bridge = createMockAncestryBridge('success')
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Home' })).toBeVisible()
    const diagnostics = screen.getByRole('link', { name: 'Diagnostics' })
    diagnostics.focus()
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { name: 'Diagnostics' })).toBeVisible()
    await userEvent.click(screen.getByRole('link', { name: 'Settings' }))
    expect(await screen.findByRole('heading', { name: 'Settings' })).toBeVisible()
  })
  it('renders a focused degraded diagnostic without leaking internals', async () => {
    const base = createMockAncestryBridge('success')
    const bridge: AncestryBridge = {
      ...base,
      getStartupDiagnostics: vi.fn().mockResolvedValue({
        ok: false,
        protocolVersion: '1',
        error: { code: 'INTERNAL_ERROR', message: 'Desktop diagnostics are temporarily unavailable.', remediation: 'Restart AncestryLLM.' },
      }),
    }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveFocus()
    expect(alert).toHaveTextContent('Desktop diagnostics are temporarily unavailable.')
    expect(alert).not.toHaveTextContent(/stack|token|path/i)
  })

  it('updates preferences with the last renderer-visible revision', async () => {
    const base = createMockAncestryBridge('success')
    const update = vi.fn((request) => base.updatePreferences(request))
    const bridge: AncestryBridge = { ...base, updatePreferences: update }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    await userEvent.click(await screen.findByRole('radio', { name: 'dark' }))
    expect(update).toHaveBeenCalledWith({ expectedRevision: 0, colorScheme: 'dark' })
  })
})
