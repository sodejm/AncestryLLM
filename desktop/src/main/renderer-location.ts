import { pathToFileURL } from 'node:url'

interface RendererPolicy {
  developmentUrl: string | undefined
  isPackaged: boolean
  rendererPath: string
}

interface RendererTrustPolicy extends RendererPolicy {
  senderUrl: string
}

export type RendererTarget =
  | { kind: 'file'; value: string }
  | { kind: 'url'; value: string }

export function resolveRendererTarget(policy: RendererPolicy): RendererTarget {
  if (!policy.isPackaged && policy.developmentUrl) {
    return { kind: 'url', value: policy.developmentUrl }
  }
  return { kind: 'file', value: policy.rendererPath }
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
  if (target.kind === 'file') {
    return candidate.href === pathToFileURL(target.value).href
  }

  try {
    return candidate.origin === new URL(target.value).origin
  } catch {
    return false
  }
}
