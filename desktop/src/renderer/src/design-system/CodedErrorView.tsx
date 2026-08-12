// Sanitized stable-code error presentation with bounded recovery actions.

import { AlertTriangle } from 'lucide-react'
import type { RefObject } from 'react'
import { Button } from '../components/Button'

const STABLE_CODE = /^[A-Z][A-Z0-9_]{1,63}$/

interface CodedErrorViewProps {
  readonly code: string
  readonly title: string
  readonly recovery: string
  readonly actionLabel?: string
  readonly onAction?: () => void
  readonly focusRef?: RefObject<HTMLDivElement | null>
}

function normalizedErrorCode(code: string): string {
  return STABLE_CODE.test(code) ? code : 'UNEXPECTED_ERROR'
}

export function CodedErrorView({ code, title, recovery, actionLabel, onAction, focusRef }: CodedErrorViewProps) {
  return <div ref={focusRef} tabIndex={focusRef ? -1 : undefined} role="alert" className="error coded-error">
    <AlertTriangle aria-hidden="true" />
    <div>
      <strong>{title}</strong>
      <p className="error-code">Code: {normalizedErrorCode(code)}</p>
      <p>{recovery}</p>
      {actionLabel && onAction && <Button onClick={onAction}>{actionLabel}</Button>}
    </div>
  </div>
}
