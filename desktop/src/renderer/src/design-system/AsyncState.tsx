// Semantic presentation for the shared asynchronous state contract.

import {
  AlertCircle,
  Ban,
  CheckCircle2,
  CircleDashed,
  CloudOff,
  FolderOpen,
  TriangleAlert,
} from 'lucide-react'
import type { ReactNode } from 'react'
import type { AsyncState as AsyncStateContract, AsyncStateKind } from './contracts'

const stateIcons: Readonly<Record<AsyncStateKind, ReactNode>> = {
  loading: <CircleDashed aria-hidden="true" />,
  empty: <FolderOpen aria-hidden="true" />,
  offline: <CloudOff aria-hidden="true" />,
  degraded: <TriangleAlert aria-hidden="true" />,
  error: <AlertCircle aria-hidden="true" />,
  success: <CheckCircle2 aria-hidden="true" />,
  'permission-denied': <Ban aria-hidden="true" />,
}

export function AsyncState({ state }: Readonly<{ state: AsyncStateContract }>) {
  const liveRole = state.kind === 'loading' ? 'status' : state.kind === 'error' ? 'alert' : undefined

  return <section className={`async-state async-state-${state.kind}`} role={liveRole} aria-labelledby={`state-${state.kind}-title`}>
    <div className="async-state-icon">{stateIcons[state.kind]}</div>
    <div>
      <p className="state-label">State: {state.label}</p>
      <h2 id={`state-${state.kind}-title`}>{state.title}</h2>
      <p>{state.description}</p>
      {state.code && <p className="error-code">Code: {state.code}</p>}
    </div>
  </section>
}
