/** Verifies the file-grant card renders sanitized metadata and replacement intent. */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FileGrantCard } from './FileGrantCard'

describe('FileGrantCard', () => {
  it('shows only sanitized metadata and read intent', () => {
    render(<FileGrantCard grant={{
      grantId: `grt_${'a'.repeat(64)}`,
      purpose: 'gedcom-read',
      access: 'read',
      scope: { originatingWindow: 'requesting-window', lifetime: 'app-session', redemption: 'single-use' },
      metadata: { displayName: 'fictional.ged', format: 'gedcom', sizeBytes: 1024, validation: 'validated-input' },
    }} />)
    expect(screen.getByText('fictional.ged')).toBeInTheDocument()
    expect(screen.getByText('Read only')).toBeInTheDocument()
    expect(screen.getByText('1 KB')).toBeInTheDocument()
    expect(screen.queryByText(/Users|\\Users|tmp/)).not.toBeInTheDocument()
  })

  it('makes destructive replacement confirmation visible', () => {
    render(<FileGrantCard grant={{
      grantId: `grt_${'b'.repeat(64)}`,
      purpose: 'gedcom-write',
      access: 'write',
      scope: { originatingWindow: 'requesting-window', lifetime: 'app-session', redemption: 'single-use' },
      metadata: { displayName: 'replace.ged', format: 'gedcom', sizeBytes: 42, validation: 'replacement-confirmed' },
    }} />)
    expect(screen.getByText('Write destination')).toBeInTheDocument()
    expect(screen.getByText('Existing file replacement confirmed')).toBeInTheDocument()
  })
})
