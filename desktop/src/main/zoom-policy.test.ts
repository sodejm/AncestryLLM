/** Tests keyboard zoom shortcuts, bounds, and platform-specific modifiers for the desktop shell. */
import { describe, expect, it, vi } from 'vitest'
import {
  installKeyboardZoom,
  type KeyboardZoomInput,
  type KeyboardZoomTarget,
} from './zoom-policy'

function zoomHarness(initialZoom = 1) {
  let listener: ((event: { preventDefault(): void }, input: KeyboardZoomInput) => void) | undefined
  let currentZoom = initialZoom
  const setZoomFactor = vi.fn((factor: number) => { currentZoom = factor })
  const target: KeyboardZoomTarget = {
    getZoomFactor: () => currentZoom,
    setZoomFactor,
    on: (_event, installed) => { listener = installed },
  }
  const dispatch = (input: Partial<KeyboardZoomInput>) => {
    const preventDefault = vi.fn()
    listener?.({ preventDefault }, {
      type: 'keyDown',
      key: '',
      code: '',
      control: false,
      meta: false,
      alt: false,
      ...input,
    })
    return preventDefault
  }
  return { dispatch, setZoomFactor, target }
}

describe('keyboard zoom policy', () => {
  it('supports macOS keyboard zoom and caps magnification at 200%', () => {
    const { dispatch, setZoomFactor, target } = zoomHarness()
    installKeyboardZoom(target, 'darwin')

    for (let index = 0; index < 20; index += 1) {
      expect(dispatch({ code: 'Equal', key: '=', meta: true })).toHaveBeenCalledOnce()
    }

    expect(setZoomFactor).toHaveBeenLastCalledWith(2)
  })

  it('supports Control zoom in, zoom out, and reset on non-macOS platforms', () => {
    const { dispatch, setZoomFactor, target } = zoomHarness()
    installKeyboardZoom(target, 'win32')

    dispatch({ code: 'NumpadAdd', key: '+', control: true })
    dispatch({ code: 'Minus', key: '-', control: true })
    dispatch({ code: 'Digit0', key: '0', control: true })

    expect(setZoomFactor.mock.calls).toEqual([[1.1], [1], [1]])
  })

  it('caps zoom out at 50%', () => {
    const { dispatch, setZoomFactor, target } = zoomHarness()
    installKeyboardZoom(target, 'linux')

    for (let index = 0; index < 20; index += 1) {
      dispatch({ code: 'Minus', key: '-', control: true })
    }

    expect(setZoomFactor).toHaveBeenLastCalledWith(0.5)
  })

  it('ignores unrelated, modified, and non-key-down input', () => {
    const { dispatch, setZoomFactor, target } = zoomHarness()
    installKeyboardZoom(target, 'darwin')

    expect(dispatch({ code: 'Equal', key: '=', control: true })).not.toHaveBeenCalled()
    expect(dispatch({ code: 'Equal', key: '=', meta: true, control: true })).not.toHaveBeenCalled()
    expect(dispatch({ code: 'Equal', key: '=', meta: true, alt: true })).not.toHaveBeenCalled()
    expect(dispatch({ type: 'keyUp', code: 'Equal', key: '=', meta: true })).not.toHaveBeenCalled()
    expect(dispatch({ code: 'KeyA', key: 'a', meta: true })).not.toHaveBeenCalled()
    expect(setZoomFactor).not.toHaveBeenCalled()
  })
})
