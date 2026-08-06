/** Resolves the renderer entry target and validates trusted renderer URLs for IPC authorization. */
import { APP_ENTRY_URL } from './security-policy'

interface RendererPolicy {
  developmentUrl: string | undefined
  isPackaged: boolean
  rendererPath: string
}

interface RendererTrustPolicy extends RendererPolicy {
  senderUrl: string
}

export type RendererTarget =
  { kind: 'url'; value: string }

export function resolveRendererTarget(policy: RendererPolicy): RendererTarget {
  void policy.rendererPath
  if (!policy.isPackaged && policy.developmentUrl) {
    return { kind: 'url', value: policy.developmentUrl }
  }
  return { kind: 'url', value: APP_ENTRY_URL }
}

export function isTrustedRendererUrl(policy: RendererTrustPolicy): boolean {
  let candidate: URL
  try {
    candidate = new URL(policy.senderUrl)
  } catch {
    return false
  }
  candidate.hash = ''

  const target = resolveRendererTarget(policy)
  try {
    const expected = new URL(target.value)
    if (expected.protocol === 'app:') return candidate.href === expected.href
    return candidate.origin === expected.origin
  } catch {
    return false
  }
}
