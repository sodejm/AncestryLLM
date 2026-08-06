/** Installs the Electron single-instance guard and restores focus to the existing primary window. */
export interface SingleInstanceWindow {
  isMinimized(): boolean
  restore(): void
  focus(): void
}

export interface SingleInstanceDependencies {
  requestLock(): boolean
  quit(): void
  onSecondInstance(listener: () => void): void
  primaryWindow(): SingleInstanceWindow | undefined
}

export function installSingleInstanceGuard(dependencies: Readonly<SingleInstanceDependencies>): boolean {
  if (!dependencies.requestLock()) {
    dependencies.quit()
    return false
  }
  dependencies.onSecondInstance(() => {
    const window = dependencies.primaryWindow()
    if (!window) return
    if (window.isMinimized()) window.restore()
    window.focus()
  })
  return true
}
