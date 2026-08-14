/** Enforces one Electron application instance and focuses the existing window. */
export interface SingleInstanceWindow {
  isMinimized(): boolean
  restore(): void
  focus(): void
}

export interface SingleInstanceLockDependencies {
  requestLock(): boolean
  onSecondInstance(listener: () => void): void
  primaryWindow(): SingleInstanceWindow | undefined
}

export interface SingleInstanceDependencies extends SingleInstanceLockDependencies {
  quit(): void
}

export function acquireSingleInstanceLock(
  dependencies: Readonly<SingleInstanceLockDependencies>,
): boolean {
  if (!dependencies.requestLock()) return false
  dependencies.onSecondInstance(() => {
    const window = dependencies.primaryWindow()
    if (!window) return
    if (window.isMinimized()) window.restore()
    window.focus()
  })
  return true
}

export function installSingleInstanceGuard(dependencies: Readonly<SingleInstanceDependencies>): boolean {
  if (!acquireSingleInstanceLock(dependencies)) {
    dependencies.quit()
    return false
  }
  return true
}
