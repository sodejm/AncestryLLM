/** Runs automated accessibility checks for reusable shell and state components. */

import { render, screen } from '@testing-library/react'
import axe from 'axe-core'
import { describe, expect, it } from 'vitest'
import { createMockAncestryBridge } from '../../../mock-bridge/desktop'
import { componentStateFixtures } from '../../../mock-bridge/shell-fixtures'
import { App } from '../App'
import { ComponentGallery } from './ComponentGallery'

async function completedBridge() {
  const bridge = createMockAncestryBridge('success')
  await bridge.updatePreferences({ expectedRevision: 0, onboardingCompleted: true })
  return bridge
}

async function expectNoAxeViolations(container: Element) {
  const result = await axe.run(container, {
    rules: {
      // Electron supplies the document title in the renderer HTML. These focused
      // component tests intentionally scan only the rendered review surface.
      'document-title': { enabled: false },
      // jsdom does not implement the canvas API axe uses for contrast. The
      // Chromium accessibility gate runs this rule against the packaged shell.
      'color-contrast': { enabled: false },
    },
  })
  expect(result.violations, result.violations.map((violation) =>
    `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(', ')}`,
  ).join('\n')).toEqual([])
}

describe('automated desktop accessibility', () => {
  it('finds no detectable WCAG A or AA violations in the production shell', async () => {
    Object.defineProperty(window, 'ancestry', { configurable: true, value: await completedBridge() })
    window.location.hash = '#/'

    const { container } = render(<App />)

    await screen.findByRole('heading', { name: 'Home' })
    await expectNoAxeViolations(container)
  })

  it('finds no detectable WCAG A or AA violations in every shared state', async () => {
    const { container } = render(<ComponentGallery states={Object.values(componentStateFixtures)} />)

    await expectNoAxeViolations(container)
  })
})
