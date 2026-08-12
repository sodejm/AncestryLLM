// Fictional development-only states for the isolated component gallery.

import type { AsyncState } from '../renderer/src/design-system/contracts'
import { deepFreeze } from './fixtures'

export const componentStateFixtures = deepFreeze({
  loading: {
    kind: 'loading',
    label: 'Loading',
    title: 'Preparing local details',
    description: 'Wait while AncestryLLM reads local state.',
  },
  empty: {
    kind: 'empty',
    label: 'Empty',
    title: 'Nothing here yet',
    description: 'A future local action can add content to this area.',
  },
  offline: {
    kind: 'offline',
    label: 'Offline',
    title: 'Working offline',
    description: 'This view remains available without a network connection.',
  },
  degraded: {
    kind: 'degraded',
    label: 'Degraded',
    title: 'Some local details are unavailable',
    description: 'Open Diagnostics for bounded recovery guidance.',
  },
  error: {
    kind: 'error',
    label: 'Error',
    title: 'The view could not be loaded',
    description: 'Restart AncestryLLM and try again.',
    code: 'VIEW_UNAVAILABLE',
  },
  success: {
    kind: 'success',
    label: 'Success',
    title: 'Local work is complete',
    description: 'The requested local action finished successfully.',
  },
  permissionDenied: {
    kind: 'permission-denied',
    label: 'Permission denied',
    title: 'Access was not granted',
    description: 'Choose a permitted local resource and try again.',
    code: 'PERMISSION_DENIED',
  },
} satisfies Readonly<Record<string, AsyncState>>)
