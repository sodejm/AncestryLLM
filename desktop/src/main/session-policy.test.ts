/** Verifies the default Electron session denies permissions, devices, and downloads. */
import { describe, expect, it, vi } from 'vitest'
import { installSessionPolicy } from './session-policy'

describe('default-session deny policy', () => {
  it('denies permission requests, permission checks, devices, display capture, and downloads', () => {
    const permissionRequest = vi.fn()
    const permissionCheck = vi.fn()
    const devicePermission = vi.fn()
    const displayMedia = vi.fn()
    const on = vi.fn()
    const target = {
      setPermissionRequestHandler: permissionRequest,
      setPermissionCheckHandler: permissionCheck,
      setDevicePermissionHandler: devicePermission,
      setDisplayMediaRequestHandler: displayMedia,
      on,
    }

    installSessionPolicy(target)

    const permissionCallback = vi.fn()
    permissionRequest.mock.calls[0]![0](undefined, 'geolocation', permissionCallback)
    expect(permissionCallback).toHaveBeenCalledWith(false)
    expect(permissionCheck.mock.calls[0]![0](undefined, 'notifications', 'app://bundle')).toBe(false)
    expect(devicePermission.mock.calls[0]![0]({ deviceType: 'usb' })).toBe(false)
    const displayCallback = vi.fn()
    displayMedia.mock.calls[0]![0]({}, displayCallback)
    expect(displayCallback).toHaveBeenCalledWith({})

    const preventDefault = vi.fn()
    const cancel = vi.fn()
    on.mock.calls.find(([event]) => event === 'will-download')?.[1]({ preventDefault }, { cancel })
    expect(preventDefault).toHaveBeenCalled()
    expect(cancel).toHaveBeenCalled()
  })
})
