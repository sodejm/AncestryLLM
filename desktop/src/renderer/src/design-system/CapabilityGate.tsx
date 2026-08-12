// Presentation-only capability branching without granting runtime authority.

import type { ReactNode } from 'react'

interface CapabilityGateProps {
  readonly available: boolean
  readonly children: ReactNode
  readonly unavailable: ReactNode
}

export function CapabilityGate({ available, children, unavailable }: CapabilityGateProps) {
  return available ? children : unavailable
}
