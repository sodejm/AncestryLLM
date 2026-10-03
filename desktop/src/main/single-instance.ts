/** Enforces one Electron application instance and focuses the existing window. */
/**
 * Limits second-instance handling to restoring and focusing the already trusted primary window.
 */
export interface SingleInstanceWindow {
  isMinimized(): boolean
  restore(): void
  focus(): void
}

/**
 * Supplies the process lock and primary-window operations used by single-instance coordination.
 */
export interface SingleInstanceLockDependencies {
  requestLock(): boolean
  onSecondInstance(listener: () => void): void
  primaryWindow(): SingleInstanceWindow | undefined
}

/**
 * Extends lock coordination with the application quit operation used by secondary processes.
 */
export interface SingleInstanceDependencies extends SingleInstanceLockDependencies {
  quit(): void
}

/**
 * Acquires primary-process ownership and focuses the existing window when another instance starts.
 */
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

/**
 * Installs single-instance coordination and terminates the caller when primary ownership is unavailable.
 */
export function installSingleInstanceGuard(dependencies: Readonly<SingleInstanceDependencies>): boolean {
  if (!acquireSingleInstanceLock(dependencies)) {
    dependencies.quit()
    return false
  }
  return true
}
