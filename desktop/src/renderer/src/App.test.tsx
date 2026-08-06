/** Tests the accessible desktop shell flows for onboarding, diagnostics, settings, and degraded startup states. */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AncestryBridge } from '../../shared-contract/desktop'
import { createMockAncestryBridge } from '../../mock-bridge/desktop'
import { App } from './App'

async function createCompletedBridge(mode: Parameters<typeof createMockAncestryBridge>[0] = 'success') {
  const bridge = createMockAncestryBridge(mode)
  await bridge.updatePreferences({ expectedRevision: 0, onboardingCompleted: true })
  return bridge
}

describe('accessible desktop shell', () => {
  beforeEach(() => {
    window.location.hash = '#/'
    delete document.documentElement.dataset.theme
    delete document.documentElement.dataset.reducedMotion
  })

  it('presents a focused, bounded offline welcome on first launch', async () => {
    const bridge = createMockAncestryBridge('success')
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)

    const heading = await screen.findByRole('heading', { name: 'Welcome to AncestryLLM' })
    expect(heading).toHaveFocus()
    expect(screen.getByText('Your desktop control shell stays local to this device.')).toBeVisible()
    expect(screen.getByText(/No account, provider, API key, genealogy data, or cloud consent is requested here/i)).toBeVisible()
    expect(screen.getByText(/Updates are installed manually/i)).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open Diagnostics' })).toHaveAttribute('href', '#/diagnostics')
    expect(screen.getByRole('button', { name: 'Continue to Home' })).toBeEnabled()
    expect(screen.queryByRole('heading', { name: 'Application' })).not.toBeInTheDocument()
  })

  it('completes onboarding with the visible revision and reveals Home', async () => {
    const base = createMockAncestryBridge('success')
    const updatePreferences = vi.fn((request) => base.updatePreferences(request))
    const bridge: AncestryBridge = { ...base, updatePreferences }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Continue to Home' }))

    expect(updatePreferences).toHaveBeenCalledWith({ expectedRevision: 0, onboardingCompleted: true })
    expect(await screen.findByRole('heading', { name: 'Home' })).toHaveFocus()
    expect(screen.getByRole('heading', { name: 'Application' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Welcome to AncestryLLM' })).not.toBeInTheDocument()
  })

  it('keeps the welcome usable and renders only fixed recovery guidance when completion fails', async () => {
    const base = createMockAncestryBridge('success')
    const updatePreferences = vi.fn().mockResolvedValue({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'PREFERENCES_UNAVAILABLE',
        message: 'token=super-secret at /Users/example/preferences.json',
        remediation: 'Connect to port 43117 and inspect stderr.',
      },
    })
    const bridge: AncestryBridge = { ...base, updatePreferences }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Continue to Home' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveFocus()
    expect(alert).toHaveTextContent('Welcome progress was not saved.')
    expect(alert).toHaveTextContent('Code: PREFERENCES_UNAVAILABLE')
    expect(alert).toHaveTextContent('Open Diagnostics or restart AncestryLLM.')
    expect(alert).not.toHaveTextContent(/super-secret|preferences\.json|43117|stderr/i)
    expect(screen.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled()
  })

  it('converges after a completion conflict only when refreshed preferences are complete', async () => {
    const base = createMockAncestryBridge('success')
    const getPreferences = vi.fn()
      .mockImplementationOnce(() => base.getPreferences())
      .mockResolvedValue({
        ok: true,
        protocolVersion: '1',
        data: {
          colorScheme: 'system',
          reducedMotion: false,
          onboardingCompleted: true,
          schemaVersion: 1,
          revision: 1,
        },
      })
    const updatePreferences = vi.fn().mockResolvedValue({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'PREFERENCES_CONFLICT',
        message: 'stale details that must not render',
        remediation: 'raw remediation that must not render',
      },
    })
    const bridge: AncestryBridge = { ...base, getPreferences, updatePreferences }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Continue to Home' }))

    expect(await screen.findByRole('heading', { name: 'Home' })).toBeVisible()
    expect(getPreferences).toHaveBeenCalledTimes(2)
    expect(screen.queryByText(/stale details|raw remediation/i)).not.toBeInTheDocument()
  })

  it('stays on the welcome when a conflict refresh remains incomplete', async () => {
    const base = createMockAncestryBridge('success')
    const updatePreferences = vi.fn().mockResolvedValue({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'PREFERENCES_CONFLICT',
        message: 'stale details that must not render',
        remediation: 'raw remediation that must not render',
      },
    })
    const bridge: AncestryBridge = { ...base, updatePreferences }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Continue to Home' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Code: PREFERENCES_CONFLICT')
    expect(screen.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Home' })).not.toBeInTheDocument()
  })

  it('stays on the welcome when a conflict refresh is unavailable', async () => {
    const base = createMockAncestryBridge('success')
    const getPreferences = vi.fn()
      .mockImplementationOnce(() => base.getPreferences())
      .mockResolvedValue({
        ok: false,
        protocolVersion: '1',
        error: {
          code: 'PREFERENCES_UNAVAILABLE',
          message: 'raw unavailable details',
          remediation: 'raw unavailable remediation',
        },
      })
    const updatePreferences = vi.fn().mockResolvedValue({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'PREFERENCES_CONFLICT',
        message: 'stale details that must not render',
        remediation: 'raw remediation that must not render',
      },
    })
    const bridge: AncestryBridge = { ...base, getPreferences, updatePreferences }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Continue to Home' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Code: PREFERENCES_CONFLICT')
    expect(screen.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Home' })).not.toBeInTheDocument()
  })

  it('does not unlock from a malformed successful update when refresh is incomplete', async () => {
    const base = createMockAncestryBridge('success')
    const malformed = {
      ok: true,
      protocolVersion: '1',
      data: { onboardingCompleted: true },
    } as unknown as Awaited<ReturnType<AncestryBridge['updatePreferences']>>
    const updatePreferences = vi.fn().mockResolvedValue(malformed)
    const bridge: AncestryBridge = { ...base, updatePreferences }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Continue to Home' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Code: PREFERENCES_UNAVAILABLE')
    expect(screen.getByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Home' })).not.toBeInTheDocument()
  })

  it('lets a completed user revisit the welcome without writing preferences or changing routes', async () => {
    const base = createMockAncestryBridge('success')
    await base.updatePreferences({ expectedRevision: 0, onboardingCompleted: true })
    const updatePreferences = vi.fn((request) => base.updatePreferences(request))
    const bridge: AncestryBridge = { ...base, updatePreferences }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Review welcome' }))

    expect(await screen.findByRole('heading', { name: 'Welcome to AncestryLLM' })).toHaveFocus()
    expect(window.location.hash).toBe('#/')
    expect(screen.getByRole('button', { name: 'Back to Home' })).toBeVisible()
    expect(updatePreferences).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Back to Home' }))
    expect(await screen.findByRole('heading', { name: 'Home' })).toHaveFocus()
    expect(updatePreferences).not.toHaveBeenCalled()
  })
  it('supports keyboard navigation across Home, Diagnostics, and Settings', async () => {
    const bridge = await createCompletedBridge()
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

  it('renders the bounded production Home summary without development or domain surfaces', async () => {
    const base = await createCompletedBridge()
    const getAppInfo = vi.fn(() => base.getAppInfo())
    const getCapabilities = vi.fn(() => base.getCapabilities())
    const bridge: AncestryBridge = { ...base, getAppInfo, getCapabilities }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Home' })).toBeVisible()
    expect(await screen.findByRole('heading', { name: 'Application' })).toBeVisible()
    expect(screen.getByText('0.5.0-dev')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Offline posture' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Startup state' })).toBeVisible()
    expect(screen.getByText('Ready')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Capabilities' })).toBeVisible()
    expect(screen.getByText('No control capabilities are currently available.')).toBeVisible()
    expect(screen.queryByText('Component gallery')).not.toBeInTheDocument()
    expect(screen.queryByText(/genealogy|provider|cloud|account|job|chat|updater/i)).not.toBeInTheDocument()
    expect(getAppInfo).toHaveBeenCalledTimes(1)
    expect(getCapabilities).toHaveBeenCalledTimes(1)
  })
  it('renders a focused degraded diagnostic without leaking internals', async () => {
    const base = await createCompletedBridge()
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

  it('maps bridge failures to stable coded guidance without rendering bridge detail', async () => {
    const base = await createCompletedBridge()
    const bridge: AncestryBridge = {
      ...base,
      getStartupDiagnostics: vi.fn().mockResolvedValue({
        ok: false,
        protocolVersion: '1',
        error: {
          code: 'SIDECAR_UNAVAILABLE',
          message: 'token=super-secret at /Users/example/private.sock',
          remediation: 'Connect to port 43117 and inspect stderr.',
        },
      }),
    }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Desktop diagnostics are temporarily unavailable.')
    expect(alert).toHaveTextContent('Code: SIDECAR_UNAVAILABLE')
    expect(alert).toHaveTextContent('Restart AncestryLLM.')
    expect(alert).not.toHaveTextContent(/super-secret|private\.sock|43117|stderr/i)
  })

  it('updates preferences with the last renderer-visible revision', async () => {
    const base = await createCompletedBridge()
    const update = vi.fn((request) => base.updatePreferences(request))
    const bridge: AncestryBridge = { ...base, updatePreferences: update }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    await userEvent.click(await screen.findByRole('radio', { name: 'dark' }))
    expect(update).toHaveBeenCalledWith({ expectedRevision: 1, colorScheme: 'dark' })
  })

  it('applies the persisted color scheme when a renderer opens', async () => {
    const bridge = createMockAncestryBridge('success')
    await bridge.updatePreferences({ expectedRevision: 0, colorScheme: 'dark', onboardingCompleted: true })
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    expect(await screen.findByRole('radio', { name: 'dark' })).toBeChecked()
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('persists reduced motion and applies it to the document root', async () => {
    const base = await createCompletedBridge()
    const update = vi.fn((request) => base.updatePreferences(request))
    const bridge: AncestryBridge = { ...base, updatePreferences: update }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Reduce motion' }))

    expect(update).toHaveBeenCalledWith({ expectedRevision: 1, reducedMotion: true })
    await waitFor(() => expect(document.documentElement.dataset.reducedMotion).toBe('true'))
  })

  it('shows fixed preference recovery guidance without rendering bridge detail', async () => {
    const base = await createCompletedBridge()
    const updatePreferences = vi.fn().mockResolvedValue({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'PREFERENCES_CONFLICT',
        message: 'Preferences at /Users/example/settings.json contain token=secret.',
        remediation: 'Inspect the private path.',
      },
    })
    const bridge: AncestryBridge = { ...base, updatePreferences }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    await userEvent.click(await screen.findByRole('radio', { name: 'dark' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Preferences were not saved.')
    expect(alert).toHaveTextContent('Code: PREFERENCES_CONFLICT')
    expect(alert).toHaveTextContent('Review the current settings and try again.')
    expect(alert).not.toHaveTextContent(/settings\.json|token=secret|private path/i)
  })

  it('does not reuse a stale revision while a preference update is pending', async () => {
    const base = await createCompletedBridge()
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

    const saved = await base.updatePreferences({ expectedRevision: 1, colorScheme: 'dark' })
    resolveFirst?.(saved)
    await waitFor(() => expect(screen.getByRole('group', { name: 'Theme' })).not.toBeDisabled())
    await userEvent.click(screen.getByRole('radio', { name: 'light' }))
    expect(update).toHaveBeenLastCalledWith({ expectedRevision: 2, colorScheme: 'light' })
  })

  it('offers one bounded retry for degraded diagnostics and renders the result', async () => {
    const base = await createCompletedBridge('degraded')
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

  it('gives restart guidance instead of another retry when recovery is exhausted', async () => {
    const base = await createCompletedBridge('degraded')
    const bridge: AncestryBridge = {
      ...base,
      getStartupDiagnostics: vi.fn().mockResolvedValue({
        ok: true,
        protocolVersion: '1',
        data: {
          state: 'degraded',
          failure: 'crash_loop',
          automaticRestartsRemaining: 0,
          manualRetriesRemaining: 0,
        },
      }),
    }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Diagnostics' }))

    expect(await screen.findByText('The desktop service stopped repeatedly.')).toBeVisible()
    expect(screen.getByText('Restart AncestryLLM to try again.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Retry desktop service' })).not.toBeInTheDocument()
  })

  it('refreshes diagnostics when the diagnostics route opens', async () => {
    const base = await createCompletedBridge()
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
