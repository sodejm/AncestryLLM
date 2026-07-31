import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AncestryBridge } from '../../shared-contract/desktop'
import { successFixture, failureFixture } from '../../mock-bridge/fixtures'
import { App } from './App'

describe('accessible desktop shell', () => {
  beforeEach(() => { window.location.hash = '#/' })
  it('supports keyboard navigation across Home, Diagnostics, and Settings', async () => {
    const bridge: AncestryBridge = { startup: vi.fn().mockResolvedValue(successFixture), setTheme: vi.fn().mockResolvedValue(successFixture) }
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
    const bridge: AncestryBridge = { startup: vi.fn().mockResolvedValue(failureFixture), setTheme: vi.fn().mockResolvedValue(failureFixture) }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveFocus()
    expect(alert).toHaveTextContent('Desktop diagnostics are temporarily unavailable.')
    expect(alert).not.toHaveTextContent(/stack|token|path/i)
  })
})
