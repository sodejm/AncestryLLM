import { describe, expect, it, vi } from 'vitest'
import { externalLinkPrompt, openExternalLinkWithConfirmation, validateExternalLink } from './external-links'

describe('external-link policy', () => {
  it('accepts only the allowlisted HTTPS destination', () => {
    expect(validateExternalLink('https://github.com/sodejm/AncestryLLM/issues/100')).toBe('https://github.com/sodejm/AncestryLLM/issues/100')
  })

  it.each([
    'http://github.com/sodejm/AncestryLLM',
    'https://attacker.invalid/',
    'https://github.com.attacker.invalid/',
    ['https://user', 'password@github.com/'].join(':'),
    'https://github.com:8443/',
    'javascript:alert(1)',
    'not a url',
  ])('rejects a non-HTTPS, credentialed, ported, or unallowlisted URL: %s', (url) => {
    expect(() => validateExternalLink(url)).toThrow('External link denied')
  })

  it('shows the normalized destination and requires explicit confirmation before opening', async () => {
    const destination = 'https://github.com/sodejm/AncestryLLM/issues/100'
    expect(externalLinkPrompt(destination)).toMatchObject({
      buttons: ['Cancel', 'Open link'],
      defaultId: 0,
      cancelId: 0,
      message: 'Open this destination outside AncestryLLM?',
      detail: expect.stringContaining(destination),
    })
    expect(externalLinkPrompt(destination).detail).toContain('Only continue if you intended')
    const confirm = vi.fn(async () => true)
    const openExternal = vi.fn(async () => undefined)
    const result = await openExternalLinkWithConfirmation(
      destination,
      { confirm, openExternal },
    )
    expect(confirm).toHaveBeenCalledWith(destination)
    expect(openExternal).toHaveBeenCalledWith(destination)
    expect(result).toEqual({ status: 'opened' })
  })

  it('does not open when the user cancels', async () => {
    const openExternal = vi.fn(async () => undefined)
    const result = await openExternalLinkWithConfirmation(
      'https://github.com/sodejm/AncestryLLM',
      { confirm: async () => false, openExternal },
    )
    expect(openExternal).not.toHaveBeenCalled()
    expect(result).toEqual({ status: 'cancelled' })
  })
})
