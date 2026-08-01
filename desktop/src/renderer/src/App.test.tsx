import { render, screen, waitFor } from '@testing-library/react'
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

  it('does not reuse a stale revision while a preference update is pending', async () => {
    const base = createMockAncestryBridge('success')
    let resolveFirst: ((value: Awaited<ReturnType<AncestryBridge['updatePreferences']>>) => void) | undefined
    const firstUpdate = new Promise<Awaited<ReturnType<AncestryBridge['updatePreferences']>>>((resolve) => { resolveFirst = resolve })
    const update = vi.fn()
      .mockImplementationOnce(() => firstUpdate)
      .mockImplementation((request) => base.updatePreferences(request))
    const bridge: AncestryBridge = { ...base, updatePreferences: update }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    await userEvent.click(await screen.findByRole('radio', { name: 'dark' }))
    await userEvent.click(screen.getByRole('radio', { name: 'light' }))
    expect(update).toHaveBeenCalledTimes(1)

    const saved = await base.updatePreferences({ expectedRevision: 0, colorScheme: 'dark' })
    resolveFirst?.(saved)
    await waitFor(() => expect(screen.getByRole('group', { name: 'Theme' })).not.toBeDisabled())
    await userEvent.click(screen.getByRole('radio', { name: 'light' }))
    expect(update).toHaveBeenLastCalledWith({ expectedRevision: 1, colorScheme: 'light' })
  })

  it('offers one bounded retry for degraded diagnostics and renders the result', async () => {
    const base = createMockAncestryBridge('degraded')
    let resolveRetry: ((value: Awaited<ReturnType<AncestryBridge['retrySidecar']>>) => void) | undefined
    const retryResult = new Promise<Awaited<ReturnType<AncestryBridge['retrySidecar']>>>((resolve) => { resolveRetry = resolve })
    const retrySidecar = vi.fn(() => retryResult)
    const bridge: AncestryBridge = { ...base, retrySidecar }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Diagnostics' }))
    expect(await screen.findByText('Degraded')).toBeVisible()
    const retry = screen.getByRole('button', { name: 'Retry desktop service' })
    await userEvent.click(retry)
    await userEvent.click(retry)
    expect(retrySidecar).toHaveBeenCalledTimes(1)

    resolveRetry?.(await base.retrySidecar())
    expect(await screen.findByText('Ready')).toBeVisible()
  })

  it('refreshes diagnostics when the diagnostics route opens', async () => {
    const base = createMockAncestryBridge('success')
    const degraded = await createMockAncestryBridge('degraded').getStartupDiagnostics()
    const getStartupDiagnostics = vi.fn()
      .mockImplementationOnce(() => base.getStartupDiagnostics())
      .mockResolvedValue(degraded)
    const bridge: AncestryBridge = { ...base, getStartupDiagnostics }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Home' })).toBeVisible()
    await userEvent.click(screen.getByRole('link', { name: 'Diagnostics' }))
    expect(await screen.findByText('Degraded')).toBeVisible()
    expect(getStartupDiagnostics).toHaveBeenCalledTimes(2)
  })
})
