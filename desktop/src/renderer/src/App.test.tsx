/** Verifies desktop shell startup, navigation, settings, chat, and failure states. */
import { render, screen, waitFor, within } from '@testing-library/react'
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
    expect(screen.getByRole('heading', { name: 'Local Desktop' })).toBeVisible()
    expect(screen.getByText('Recommended')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Connect Remote' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Host Remote' })).toBeVisible()
    expect(screen.getAllByText('Not available in this release')).toHaveLength(2)
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
  it('supports keyboard navigation across Home, Chat, Tasks, Diagnostics, and Settings', async () => {
    const bridge = await createCompletedBridge()
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Home' })).toBeVisible()
    const chat = screen.getByRole('link', { name: 'Chat' })
    chat.focus()
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { level: 1, name: 'Chat' })).toHaveFocus()
    const tasks = screen.getByRole('link', { name: 'Tasks' })
    tasks.focus()
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { level: 1, name: 'Tasks' })).toHaveFocus()
    const diagnostics = screen.getByRole('link', { name: 'Diagnostics' })
    diagnostics.focus()
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { name: 'Diagnostics' })).toHaveFocus()
    await userEvent.click(screen.getByRole('link', { name: 'Settings' }))
    expect(await screen.findByRole('heading', { name: 'Settings' })).toHaveFocus()
  })

  it('skips directly to the workspace without changing the current route', async () => {
    const bridge = await createCompletedBridge()
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    await screen.findByRole('heading', { name: 'Home' })

    const skip = screen.getByRole('link', { name: 'Skip to workspace' })
    skip.focus()
    await userEvent.keyboard('{Enter}')

    expect(screen.getByRole('main')).toHaveFocus()
    expect(window.location.hash).toBe('#/')
  })

  it('opens, filters, dismisses, and selects from keyboard navigation with deterministic focus', async () => {
    const bridge = await createCompletedBridge()
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })
    render(<App />)
    await screen.findByRole('heading', { name: 'Home' })

    await userEvent.keyboard('{Control>}k{/Control}')
    const palette = screen.getByRole('dialog', { name: 'Go to a workspace' })
    const filter = within(palette).getByRole('searchbox', { name: 'Filter destinations' })
    expect(filter).toHaveFocus()
    await userEvent.type(filter, 'settings')
    expect(within(palette).getByRole('link', { name: /Settings/ })).toBeVisible()
    expect(within(palette).queryByRole('link', { name: /Diagnostics/ })).not.toBeInTheDocument()

    await userEvent.click(within(palette).getByRole('button', { name: 'Close command palette' }))
    expect(screen.getByRole('button', { name: /Navigate/ })).toHaveFocus()

    await userEvent.keyboard('{Control>}k{/Control}')
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('link', { name: /Diagnostics/ }))
    expect(await screen.findByRole('heading', { name: 'Diagnostics' })).toHaveFocus()
    expect(window.location.hash).toBe('#/diagnostics')
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
    expect(await screen.findByText('No control capabilities are currently available.')).toBeVisible()
    expect(screen.queryByText('Component gallery')).not.toBeInTheDocument()
    expect(within(screen.getByRole('main')).queryByText(/genealogy|provider|cloud|account|job|chat|updater/i)).not.toBeInTheDocument()
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

  it('updates one application setting with the last renderer-visible revision', async () => {
    const base = await createCompletedBridge()
    const updateSettings = vi.fn((request) => base.updateSettings(request))
    const bridge: AncestryBridge = { ...base, updateSettings }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    const rows = await screen.findByRole('spinbutton', { name: 'Maximum query rows' })
    await userEvent.clear(rows)
    await userEvent.type(rows, '250')
    await userEvent.click(screen.getByRole('button', { name: 'Save Maximum query rows' }))

    expect(updateSettings).toHaveBeenCalledWith({
      schema_version: 1,
      expected_revision: 0,
      changes: { 'limits.max_query_rows': 250 },
    })
    await waitFor(() => expect(rows).toHaveValue(250))
  })

  it('reloads the visible setting value after an optimistic revision conflict', async () => {
    const base = await createCompletedBridge()
    const initial = await base.getSettings()
    if (!initial.ok) throw new Error('Expected the settings fixture to be available')
    const refreshed = {
      ok: true as const,
      protocolVersion: '1' as const,
      data: {
        ...initial.data,
        revision: 1,
        fields: initial.data.fields.map((field) => field.key === 'limits.max_query_rows'
          ? { ...field, value: 500 }
          : field),
      },
    }
    const getSettings = vi.fn()
      .mockImplementationOnce(() => base.getSettings())
      .mockResolvedValue(refreshed)
    const updateSettings = vi.fn().mockResolvedValue({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'SETTINGS_CONFLICT',
        message: 'raw stale revision details',
        remediation: 'raw conflict remediation',
      },
    })
    const bridge: AncestryBridge = { ...base, getSettings, updateSettings }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    const rows = await screen.findByRole('spinbutton', { name: 'Maximum query rows' })
    await userEvent.clear(rows)
    await userEvent.type(rows, '250')
    await userEvent.click(screen.getByRole('button', { name: 'Save Maximum query rows' }))

    expect(updateSettings).toHaveBeenCalledWith({
      schema_version: 1,
      expected_revision: 0,
      changes: { 'limits.max_query_rows': 250 },
    })
    expect(await screen.findByText('Code: SETTINGS_CONFLICT')).toBeVisible()
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Maximum query rows' })).toHaveValue(500))
    expect(document.body).not.toHaveTextContent(/raw stale revision details|raw conflict remediation/i)
  })

  it('presents the complete local-first settings and deployment boundaries', async () => {
    const bridge = await createCompletedBridge()
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))

    for (const name of [
      'General',
      'Storage',
      'Local providers',
      'Cloud providers',
      'Consent',
      'Privacy',
      'Limits',
      'Secrets',
    ]) {
      expect(await screen.findByRole('region', { name })).toBeVisible()
    }
    const deployment = screen.getByRole('region', { name: 'Deployment mode' })
    expect(within(deployment).getByRole('heading', { name: 'Local Desktop' })).toBeVisible()
    expect(within(deployment).getByRole('heading', { name: 'Connect Remote' })).toBeVisible()
    expect(within(deployment).getByRole('heading', { name: 'Host Remote' })).toBeVisible()
    expect(within(deployment).getAllByText('Not available in this release')).toHaveLength(2)
    expect(within(deployment).getByText(/non-loopback hosting remains disabled/i)).toBeVisible()
    expect(screen.getByText(/an API key never enables a provider by itself/i)).toBeVisible()
  })

  it('reviews and explicitly confirms the app-owned local runtime plan before applying it', async () => {
    const base = await createCompletedBridge()
    const previewLocalRuntime = vi.fn((request) => base.previewLocalRuntime(request))
    const applyLocalRuntime = vi.fn((request) => base.applyLocalRuntime(request))
    const bridge: AncestryBridge = { ...base, previewLocalRuntime, applyLocalRuntime }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    const runtime = await screen.findByRole('region', { name: 'Local container runtime' })

    expect(within(runtime).getByText(/Docker Desktop remains compatible but is not required/i)).toBeVisible()
    expect(within(runtime).getByText(/State: Not installed/i)).toBeVisible()
    await userEvent.click(within(runtime).getByRole('checkbox', { name: 'Use downloaded files only' }))
    await userEvent.click(within(runtime).getByRole('button', { name: 'Review setup' }))

    expect(previewLocalRuntime).toHaveBeenCalledWith({
      schema_version: 1,
      operation: 'setup',
      offline: true,
    })
    const review = await within(runtime).findByRole('region', { name: 'Reviewed runtime plan' })
    expect(review).toHaveTextContent('SET UP LOCAL RUNTIME')
    expect(review).toHaveTextContent('colima 0.10.3')
    expect(review).toHaveTextContent('1'.repeat(64))
    expect(review).toHaveTextContent('MIT')
    expect(review).toHaveTextContent('ancestryllm-local-arm64')
    expect(review).toHaveTextContent('colima-ancestryllm-local-arm64')
    expect(review).toHaveTextContent(/Loopback only: Yes/i)
    expect(within(runtime).getByRole('button', { name: 'Apply setup' })).toBeDisabled()

    await userEvent.type(
      within(runtime).getByLabelText('Type the exact confirmation phrase'),
      'SET UP LOCAL RUNTIME',
    )
    await userEvent.click(within(runtime).getByRole('button', { name: 'Apply setup' }))

    expect(applyLocalRuntime).toHaveBeenCalledWith({
      schema_version: 1,
      operation: 'setup',
      offline: true,
      plan_revision: 'a'.repeat(64),
      confirmation: 'SET UP LOCAL RUNTIME',
    })
    expect(await within(runtime).findByText(/State: Ready/i)).toBeVisible()
  })

  it('requires a successful explicit endpoint test for the exact profile before save', async () => {
    const base = await createCompletedBridge()
    const validateProviderEndpoint = vi.fn((request) => base.validateProviderEndpoint(request))
    const createProviderProfile = vi.fn((request) => base.createProviderProfile(request))
    const bridge: AncestryBridge = { ...base, validateProviderEndpoint, createProviderProfile }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    const local = await screen.findByRole('region', { name: 'Local providers' })
    await userEvent.type(within(local).getByLabelText('Profile name'), 'private-local')
    await userEvent.type(within(local).getByLabelText('Model'), 'fictional-model')

    const save = within(local).getByRole('button', { name: 'Save local provider profile' })
    expect(save).toBeDisabled()
    await userEvent.click(within(local).getByRole('button', { name: 'Test local provider endpoint' }))

    expect(validateProviderEndpoint).toHaveBeenCalledWith({
      schema_version: 1,
      provider_id: 'ollama',
      endpoint: 'http://127.0.0.1:11434',
    })
    expect(await within(local).findByText('Endpoint tested: reachable on this device.')).toBeVisible()
    expect(save).toBeEnabled()

    await userEvent.clear(within(local).getByLabelText('Endpoint'))
    await userEvent.type(within(local).getByLabelText('Endpoint'), 'http://localhost:11434')
    expect(save).toBeDisabled()
    expect(within(local).queryByText(/Endpoint tested:/)).not.toBeInTheDocument()

    await userEvent.click(within(local).getByRole('button', { name: 'Test local provider endpoint' }))
    await waitFor(() => expect(save).toBeEnabled())
    await userEvent.click(save)
    expect(createProviderProfile).toHaveBeenCalledWith({
      schema_version: 1,
      expected_revision: '0'.repeat(64),
      name: 'private-local',
      provider_id: 'ollama',
      model: 'fictional-model',
      endpoint: 'http://localhost:11434',
      endpoint_identity_sha256: 'a'.repeat(64),
    })
  })

  it('shows the complete consent disclosure and warnings before an atomic save', async () => {
    const base = await createCompletedBridge()
    const empty = await base.getProviderConfiguration()
    if (!empty.ok) throw new Error('Expected provider configuration fixture')
    const configured = await base.createProviderProfile({
      schema_version: 1,
      expected_revision: empty.data.revision,
      name: 'reviewed-cloud',
      provider_id: 'openai',
      model: 'fictional-model',
      endpoint: 'https://api.openai.com/v1',
      endpoint_identity_sha256: 'a'.repeat(64),
    })
    if (!configured.ok) throw new Error('Expected provider profile fixture')
    const previewConsent = vi.fn((request) => base.previewConsent(request))
    const createConsent = vi.fn((request) => base.createConsent(request))
    const bridge: AncestryBridge = { ...base, previewConsent, createConsent }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    const consent = await screen.findByRole('region', { name: 'Consent' })
    await userEvent.type(within(consent).getByLabelText('Consent name'), 'reviewed-consent')
    await userEvent.click(within(consent).getByRole('checkbox', { name: 'Living person' }))
    await userEvent.type(within(consent).getByLabelText('Maximum cost in US dollars'), '1.25')
    await userEvent.click(within(consent).getByRole('checkbox', { name: 'Allow provider retention' }))

    const save = within(consent).getByRole('button', { name: 'Save consent' })
    expect(save).toBeDisabled()
    await userEvent.click(within(consent).getByRole('button', { name: 'Review consent' }))

    expect(previewConsent).toHaveBeenCalledWith({
      schema_version: 1,
      provider_profile_name: 'reviewed-cloud',
      modules: ['summary'],
      purposes: ['genealogy-analysis'],
      data_classes: ['living_person'],
      models: ['fictional-model'],
      max_cost_usd: 1.25,
      retain_payloads: true,
    })
    const review = await within(consent).findByRole('region', { name: 'Consent review' })
    expect(review).toHaveTextContent('Provider: openai')
    expect(review).toHaveTextContent('Profile: reviewed-cloud')
    expect(review).toHaveTextContent('Model: fictional-model')
    expect(review).toHaveTextContent('Purpose: genealogy-analysis')
    expect(review).toHaveTextContent('Data classes: Living person')
    expect(review).toHaveTextContent('Retention: Allowed')
    expect(review).toHaveTextContent('Budget: $1.25 USD')
    expect(review).toHaveTextContent('Living-person data will leave this device.')
    expect(review).toHaveTextContent('This provider endpoint is remote.')
    expect(review).toHaveTextContent('The remote provider may retain payloads.')
    expect(save).toBeEnabled()

    await userEvent.click(save)
    expect(createConsent).toHaveBeenCalledWith({
      schema_version: 1,
      expected_revision: configured.data.revision,
      name: 'reviewed-consent',
      preview: expect.objectContaining({
        provider_profile_name: 'reviewed-cloud',
        provider_id: 'openai',
        warning_codes: [
          'LIVING_PERSON_DATA_INCLUDED',
          'REMOTE_PROVIDER_SELECTED',
          'REMOTE_RETENTION_ENABLED',
        ],
      }),
    })
    expect(await within(consent).findByText('reviewed-consent')).toBeVisible()
    expect(within(consent).getByText('Active')).toBeVisible()
  })

  it('clears credential form state immediately and exposes status only', async () => {
    const base = await createCompletedBridge()
    const setSecret = vi.fn((request) => base.setSecret(request))
    const bridge: AncestryBridge = { ...base, setSecret }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    const credential = await screen.findByRole('region', { name: 'OpenAI API key credential settings' })
    const input = within(credential).getByLabelText('OpenAI API key')
    const canary = 'credential-value-that-must-not-render'
    await userEvent.type(input, canary)
    await userEvent.click(within(credential).getByRole('button', { name: 'Save OpenAI API key' }))

    expect(input).toHaveValue('')
    expect(setSecret).toHaveBeenCalledWith({ reference: 'openai.api_key', value: canary })
    expect(await within(credential).findByText('Status: Present')).toBeVisible()
    expect(document.body).not.toHaveTextContent(canary)
  })

  it('clears credentials after a failed write and renders only a stable code', async () => {
    const base = await createCompletedBridge()
    const setSecret = vi.fn().mockResolvedValue({
      ok: false,
      protocolVersion: '1',
      error: {
        code: 'SECRET_STORE_UNAVAILABLE',
        message: 'credential=raw-secret at /Users/example/keyring',
        remediation: 'Inspect private stderr on port 43117.',
      },
    })
    const bridge: AncestryBridge = { ...base, setSecret }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    const credential = await screen.findByRole('region', { name: 'OpenAI API key credential settings' })
    const input = within(credential).getByLabelText('OpenAI API key')
    await userEvent.type(input, 'raw-secret')
    await userEvent.click(within(credential).getByRole('button', { name: 'Save OpenAI API key' }))

    expect(input).toHaveValue('')
    expect(await within(credential).findByText('Code: SECRET_STORE_UNAVAILABLE')).toBeVisible()
    expect(document.body).not.toHaveTextContent(/raw-secret|\/Users\/example|43117|stderr/i)
  })

  it('requires an explicit delete and proves the credential is absent afterward', async () => {
    const base = await createCompletedBridge()
    const deleteSecret = vi.fn((request) => base.deleteSecret(request))
    const bridge: AncestryBridge = { ...base, deleteSecret }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)
    await userEvent.click(await screen.findByRole('link', { name: 'Settings' }))
    const credential = await screen.findByRole('region', { name: 'OpenAI API key credential settings' })
    const input = within(credential).getByLabelText('OpenAI API key')
    await userEvent.type(input, 'transient-value')
    await userEvent.click(within(credential).getByRole('button', { name: 'Save OpenAI API key' }))
    expect(await within(credential).findByText('Status: Present')).toBeVisible()
    await userEvent.type(input, 'value-that-delete-must-clear')
    await userEvent.click(within(credential).getByRole('button', { name: 'Delete OpenAI API key' }))
    expect(input).toHaveValue('')
    expect(deleteSecret).toHaveBeenCalledWith({ reference: 'openai.api_key' })
    expect(await within(credential).findByText('Status: Missing')).toBeVisible()
    expect(document.body).not.toHaveTextContent('value-that-delete-must-clear')
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
    const serviceStatus = screen.getByRole('heading', { name: 'Desktop service' }).closest('section') as HTMLElement
    expect(await within(serviceStatus).findByText('Ready')).toBeVisible()
  })

  it('keeps a degraded first run read-only and renders only sanitized component remediation', async () => {
    const base = createMockAncestryBridge('success')
    const getCapabilities = vi.fn(base.getCapabilities)
    const updatePreferences = vi.fn(base.updatePreferences)
    const previewLocalRuntime = vi.fn(base.previewLocalRuntime)
    const applyLocalRuntime = vi.fn(base.applyLocalRuntime)
    const degraded = {
      ok: true,
      protocolVersion: '1',
      data: {
        state: 'degraded',
        failure: null,
        automaticRestartsRemaining: 0,
        manualRetriesRemaining: 1,
        report: {
          schema_version: 1,
          status: 'degraded',
          platform: { operating_system: 'macos', architecture: 'arm64' },
          components: [
            {
              component: 'configuration', status: 'blocked', code: 'CONFIG_INVALID',
              message: 'The desktop configuration could not be validated.',
              remediation: 'Repair or restore config.toml, then retry startup diagnostics.',
              restart_required: false, blocks_mutations: true,
            },
            { component: 'sqlcipher', status: 'ready', code: 'SQLCIPHER_READY', message: 'SQLCipher is ready.', remediation: null, restart_required: false, blocks_mutations: false },
            { component: 'keyring', status: 'ready', code: 'KEYRING_READY', message: 'Credential storage is ready.', remediation: null, restart_required: false, blocks_mutations: false },
            { component: 'workspace', status: 'ready', code: 'DATABASE_DIRECTORY_READY', message: 'Workspace is ready.', remediation: null, restart_required: false, blocks_mutations: false },
          ],
        },
      },
    } as Awaited<ReturnType<AncestryBridge['getStartupDiagnostics']>>
    const bridge: AncestryBridge = {
      ...base,
      getCapabilities,
      getStartupDiagnostics: vi.fn().mockResolvedValue(degraded),
      updatePreferences,
      previewLocalRuntime,
      applyLocalRuntime,
    }
    Object.defineProperty(window, 'ancestry', { configurable: true, value: bridge })

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Welcome to AncestryLLM' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Continue to Home' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open read-only diagnostics' }))
    expect(await screen.findByText('CONFIG_INVALID')).toBeVisible()
    expect(screen.getByText('Repair or restore config.toml, then retry startup diagnostics.')).toBeVisible()
    expect(updatePreferences).not.toHaveBeenCalled()
    expect(getCapabilities).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('link', { name: 'Settings' }))
    expect(await screen.findByText('Settings are read-only while startup diagnostics are degraded.')).toBeVisible()
    const runtime = screen.getByRole('region', { name: 'Local container runtime' })
    expect(runtime).toBeVisible()
    expect(within(runtime).getByRole('combobox', { name: 'Operation' })).toBeDisabled()
    expect(within(runtime).getByRole('checkbox', { name: 'Use downloaded files only' })).toBeDisabled()
    const review = within(runtime).getByRole('button', { name: 'Review setup' })
    expect(review).toBeDisabled()
    await userEvent.click(review)
    expect(previewLocalRuntime).not.toHaveBeenCalled()
    expect(applyLocalRuntime).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Application settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Credentials' })).not.toBeInTheDocument()
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
