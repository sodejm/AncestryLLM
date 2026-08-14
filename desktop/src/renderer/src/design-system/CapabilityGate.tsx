/** Presents capability-dependent content without granting runtime authority. */

import type { ReactNode } from 'react'

interface CapabilityGateProps {
  readonly available: boolean
  readonly children: ReactNode
  readonly unavailable: ReactNode
}

/** Selects available or unavailable presentation without attempting to grant the capability. */
export function CapabilityGate({ available, children, unavailable }: CapabilityGateProps) {
  return available ? children : unavailable
}
