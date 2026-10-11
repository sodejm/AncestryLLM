/** Verify opaque header backgrounds and WCAG AA text contrast in every supported theme. */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import test from 'node:test'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const stylesheet = readFileSync(resolve(scriptDirectory, '../src/renderer/src/styles.css'), 'utf8')

function declarationsFor(selector) {
  const matches = []
  for (const match of stylesheet.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = match[1].split(',').map((candidate) => candidate.trim())
    if (selectors.includes(selector)) matches.push(match[2])
  }
  return matches
}

function tokenValue(selector, token) {
  for (const declarations of declarationsFor(selector)) {
    const match = declarations.match(new RegExp(`${token}:\\s*(#[0-9a-f]{6})`, 'i'))
    if (match) return match[1]
  }
  throw new Error(`Missing ${token} in ${selector}`)
}

function contrastRatio(foreground, background) {
  const luminance = (color) => {
    const channels = color.slice(1).match(/../g).map((hex) => Number.parseInt(hex, 16) / 255)
    const linear = channels.map((channel) => channel <= 0.04045
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4)
    return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2])
  }
  const foregroundLuminance = luminance(foreground)
  const backgroundLuminance = luminance(background)
  return (Math.max(foregroundLuminance, backgroundLuminance) + 0.05)
    / (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)
}

test('header text containers own opaque backgrounds for rendered contrast analysis', () => {
  for (const selector of ['.brand', '.local-state']) {
    assert.match(declarationsFor(selector).join('\n'), /background-color:\s*var\(--color-brand\)/)
  }
  assert.match(declarationsFor('.command-button').join('\n'), /background-color:\s*var\(--color-brand-interactive\)/)
  assert.match(declarationsFor('.command-button:hover').join('\n'), /background-color:\s*var\(--color-brand-interactive-hover\)/)
})

test('brand interaction surfaces retain WCAG AA contrast in light, dark, and system themes', () => {
  const foreground = tokenValue('[data-theme="light"]', '--color-brand-text')
  for (const selector of ['[data-theme="light"]', '[data-theme="dark"]', '[data-theme="system"]']) {
    for (const token of ['--color-brand', '--color-brand-interactive', '--color-brand-interactive-hover']) {
      assert.ok(
        contrastRatio(foreground, tokenValue(selector, token)) >= 4.5,
        `${selector} ${token} must retain at least 4.5:1 contrast`,
      )
    }
  }
})
