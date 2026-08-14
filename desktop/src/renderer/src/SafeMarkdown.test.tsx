/** Verifies hostile model Markdown remains inert, bounded, and user-mediated. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { SafeMarkdown } from './SafeMarkdown'

describe('hostile model Markdown boundary', () => {
  it('renders GFM text without executable HTML, remote media, or model-supplied actions', () => {
    const content = [
      '# Result',
      [
        '| relation | confidence |',
        '| --- | --- |',
        '| fictional | low |',
      ].join('\n'),
      '- [x] reviewed',
      '<script>globalThis.pwned = true</script>',
      '<button onclick="globalThis.pwned = true">Run tool</button>',
      '<svg onload="globalThis.pwned = true"><a href="javascript:alert(1)">bad</a></svg>',
      '![remote](https://attacker.invalid/tracker.png)',
      '[unsafe](javascript:alert(1))',
      '[data](data:text/html,boom)',
      '[ported](https://example.com:8443/source)',
      'https://example.com/automatic',
    ].join('\n\n')

    const { container } = render(<SafeMarkdown content={content} onOpenExternal={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Result' })).toBeVisible()
    expect(screen.getByRole('table')).toBeVisible()
    expect(container.querySelector('script, button[onclick], svg, img, iframe, embed, object')).toBeNull()
    expect(screen.queryByRole('button', { name: /run tool|unsafe|data|automatic/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /ported/i })).not.toBeInTheDocument()
    expect(document.body).not.toHaveProperty('pwned', true)
  })

  it('shows an explicit HTTPS destination and delegates only after the user clicks', async () => {
    const onOpenExternal = vi.fn(async () => undefined)
    render(<SafeMarkdown
      content="Review [the public source](https://example.com/source?q=fictional)."
      onOpenExternal={onOpenExternal}
    />)

    const linkAction = screen.getByRole('button', {
      name: 'Open external link the public source: https://example.com/source?q=fictional',
    })
    expect(screen.getByText('https://example.com/source?q=fictional')).toBeVisible()
    expect(onOpenExternal).not.toHaveBeenCalled()

    await userEvent.click(linkAction)
    expect(onOpenExternal).toHaveBeenCalledOnce()
    expect(onOpenExternal).toHaveBeenCalledWith('https://example.com/source?q=fictional')
  })

  it('bounds oversized and malformed output before mounting it', () => {
    const content = `${'a'.repeat(20_000)}\n\n[unfinished](`
    const { container } = render(<SafeMarkdown content={content} onOpenExternal={vi.fn()} />)

    const boundary = container.querySelector('[data-markdown-truncated="true"]')
    expect(boundary).not.toBeNull()
    expect(boundary?.textContent?.length).toBeLessThan(17_000)
    expect(screen.getByText('Response display was limited to 16,384 characters.')).toBeVisible()
  })
})
