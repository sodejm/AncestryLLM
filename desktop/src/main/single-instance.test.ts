import { describe, expect, it, vi } from 'vitest'
import { acquireSingleInstanceLock, installSingleInstanceGuard } from './single-instance'

describe('acquireSingleInstanceLock', () => {
  it('lets a non-GUI caller handle a rejected lock without quitting Electron', () => {
    const onSecondInstance = vi.fn()

    expect(acquireSingleInstanceLock({
      requestLock: () => false,
      onSecondInstance,
      primaryWindow: () => undefined,
    })).toBe(false)
    expect(onSecondInstance).not.toHaveBeenCalled()
  })
})

describe('installSingleInstanceGuard', () => {
  it('quits a secondary process without registering second-instance handling', () => {
    const quit = vi.fn()
    const onSecondInstance = vi.fn()

    expect(installSingleInstanceGuard({
      requestLock: () => false,
      quit,
      onSecondInstance,
      primaryWindow: () => undefined,
    })).toBe(false)
    expect(quit).toHaveBeenCalledOnce()
    expect(onSecondInstance).not.toHaveBeenCalled()
  })

  it('focuses the primary window when another process is rejected', () => {
    const restore = vi.fn()
    const focus = vi.fn()
    let secondInstance: (() => void) | undefined

    expect(installSingleInstanceGuard({
      requestLock: () => true,
      quit: vi.fn(),
      onSecondInstance: (listener) => { secondInstance = listener },
      primaryWindow: () => ({ isMinimized: () => true, restore, focus }),
    })).toBe(true)

    expect(secondInstance).toBeTypeOf('function')
    secondInstance?.()
    expect(restore).toHaveBeenCalledOnce()
    expect(focus).toHaveBeenCalledOnce()
  })
})
