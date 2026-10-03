/** Installs bounded keyboard zoom controls for Electron renderer contents. */
/**
 * Captures only the modifier and key fields needed to recognize trusted zoom shortcuts.
 */
export type KeyboardZoomInput = Readonly<{
  type: string
  key: string
  code: string
  control: boolean
  meta: boolean
  alt: boolean
}>

/**
 * Limits zoom integration to pre-input observation and bounded zoom-factor access.
 */
export type KeyboardZoomTarget = Readonly<{
  on(
    event: 'before-input-event',
    listener: (event: { preventDefault(): void }, input: KeyboardZoomInput) => void,
  ): unknown
  getZoomFactor(): number
  setZoomFactor(factor: number): void
}>

const ZOOM_LEVELS = [0.5, 0.67, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2] as const
const MINIMUM_ZOOM = 0.5
const MAXIMUM_ZOOM = 2
const ZOOM_EPSILON = 0.000_001

type ZoomCommand = 'in' | 'out' | 'reset'

function zoomCommand(input: KeyboardZoomInput, platform: NodeJS.Platform): ZoomCommand | undefined {
  if (input.type !== 'keyDown' || input.alt) return undefined
  const expectedModifier = platform === 'darwin' ? input.meta : input.control
  const unexpectedModifier = platform === 'darwin' ? input.control : input.meta
  if (!expectedModifier || unexpectedModifier) return undefined

  if (input.code === 'Equal' || input.code === 'NumpadAdd' || input.key === '+') return 'in'
  if (input.code === 'Minus' || input.code === 'NumpadSubtract' || input.key === '_') return 'out'
  if (input.code === 'Digit0' || input.code === 'Numpad0' || input.key === '0') return 'reset'
  return undefined
}

function adjacentZoom(current: number, direction: 'in' | 'out'): number {
  if (direction === 'in') {
    return ZOOM_LEVELS.find((level) => level > current + ZOOM_EPSILON)
      ?? MAXIMUM_ZOOM
  }
  return [...ZOOM_LEVELS].reverse().find((level) => level < current - ZOOM_EPSILON)
    ?? MINIMUM_ZOOM
}

/**
 * Installs platform-native zoom shortcuts and clamps renderer zoom to the reviewed 50–200% scale.
 */
export function installKeyboardZoom(
  target: KeyboardZoomTarget,
  platform: NodeJS.Platform = process.platform,
): void {
  target.on('before-input-event', (event, input) => {
    const command = zoomCommand(input, platform)
    if (!command) return
    event.preventDefault()
    target.setZoomFactor(command === 'reset' ? 1 : adjacentZoom(target.getZoomFactor(), command))
  })
}
