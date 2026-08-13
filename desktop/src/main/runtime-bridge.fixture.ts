import { createMockAncestryBridge } from '../mock-bridge/desktop'
import type { JobEvent, JobEventSubscriptionRequest } from '../shared-contract/desktop'
import type { MainDesktopBridge } from './ipc-handlers'

interface FixtureRuntimeBridge {
  bridge: MainDesktopBridge
  supervisor?: never
  prepareJobShutdown?: never
}

export async function startRuntimeBridge(
  _onSupervisorOwned?: (supervisor: never, prepareJobShutdown: never) => void,
  _options: unknown = {},
): Promise<FixtureRuntimeBridge> {
  void _onSupervisorOwned
  void _options
  const fixture = process.env.ANCESTRYLLM_DESKTOP_FIXTURE
  const rendererBridge = createMockAncestryBridge(
    fixture === 'degraded' || fixture === 'unavailable' ? fixture : 'success',
  )
  const streamJobEvents = async (
    request: JobEventSubscriptionRequest,
    listener: (event: Readonly<JobEvent>) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    let finish = (): void => undefined
    const complete = new Promise<void>((resolve) => { finish = resolve })
    const onAbort = (): void => { finish() }
    const removeListener = rendererBridge.onJobEvent((delivery) => {
      if (
        delivery.kind !== 'event'
        || delivery.subscription_id !== request.subscription_id
        || delivery.event === null
      ) return
      listener(delivery.event)
      if (delivery.event.kind === 'terminal') finish()
    })

    try {
      const result = await rendererBridge.subscribeJobEvents(request)
      if (!result.ok) throw new Error(result.error.code)
      if (signal?.aborted) finish()
      else signal?.addEventListener('abort', onAbort, { once: true })
      await complete
    } finally {
      signal?.removeEventListener('abort', onAbort)
      removeListener()
      await rendererBridge.unsubscribeJobEvents({
        schema_version: 1,
        subscription_id: request.subscription_id,
      })
    }
  }
  const streamChatEvents: MainDesktopBridge['streamChatEvents'] = async (
    request,
    listener,
    signal,
  ) => {
    let finish = (): void => undefined
    const complete = new Promise<void>((resolve) => { finish = resolve })
    const onAbort = (): void => { finish() }
    const flow = Object.freeze({ pause: (): void => undefined, resume: (): void => undefined })
    const removeListener = rendererBridge.onChatEventBatch((delivery) => {
      if (delivery.session_id !== request.session_id || delivery.run_id !== request.run_id) return
      if (delivery.kind === 'failure') {
        finish()
        return
      }
      for (const event of delivery.events ?? []) {
        if (event.sequence > request.after) listener(event, flow)
        if (event.type === 'completed' || event.type === 'interrupted' || event.type === 'failed') {
          finish()
        }
      }
    })

    try {
      if (signal?.aborted) finish()
      else signal?.addEventListener('abort', onAbort, { once: true })
      await complete
    } finally {
      signal?.removeEventListener('abort', onAbort)
      removeListener()
    }
  }
  const bridge: MainDesktopBridge = Object.freeze({
    ...rendererBridge,
    streamChatEvents,
    streamJobEvents,
  })
  return {
    bridge,
  }
}
