/** Installs the fail-closed permission and download policy on an Electron session. */
interface PermissionTarget {
  setPermissionRequestHandler(handler: (contents: unknown, permission: unknown, callback: (allowed: boolean) => void) => void): void
  setPermissionCheckHandler(handler: (contents: unknown, permission: unknown, requestingOrigin: string) => boolean): void
  setDevicePermissionHandler(handler: (details: unknown) => boolean): void
  setDisplayMediaRequestHandler(handler: (request: unknown, callback: (streams: Record<string, never>) => void) => void): void
  on(event: 'will-download', handler: (event: { preventDefault(): void }, item: { cancel(): void }) => void): void
}

export function installSessionPolicy(target: PermissionTarget): void {
  target.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  target.setPermissionCheckHandler(() => false)
  target.setDevicePermissionHandler(() => false)
  target.setDisplayMediaRequestHandler((_request, callback) => callback({}))
  target.on('will-download', (event, item) => {
    event.preventDefault()
    item.cancel()
  })
}
