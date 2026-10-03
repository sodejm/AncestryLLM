/** Rejects oversized or prototype-bearing values before bridge-specific parsing. */
/**
 * Bounds encoded bytes, nesting, collection entries, and string length at an IPC boundary.
 */
export interface StructuredCloneLimits {
  maxBytes: number
  maxDepth: number
  maxItems: number
  maxStringCharacters: number
}

/** Measures strings by encoded IPC payload size rather than JavaScript code units. */
const byteLength = (value: string): number => Buffer.byteLength(value, 'utf8')

/**
 * Rejects input that violates bounded structured-clone validation at IPC entry points before any privileged action occurs.
 */
export function validateStructuredClone(
  value: unknown,
  limits: Readonly<StructuredCloneLimits>,
): void {
  let bytes = 0
  let items = 0
  const seen = new WeakSet<object>()

  const account = (addedBytes: number, addedItems = 0): void => {
    bytes += addedBytes
    items += addedItems
    if (bytes > limits.maxBytes || items > limits.maxItems) {
      throw new Error('Structured clone exceeds bridge limits.')
    }
  }

  const visit = (candidate: unknown, depth: number): void => {
    if (depth > limits.maxDepth) throw new Error('Structured clone exceeds bridge depth.')
    if (candidate === null) { account(4); return }
    if (typeof candidate === 'string') {
      if (candidate.length > limits.maxStringCharacters) {
        throw new Error('Structured clone string exceeds bridge limits.')
      }
      account(byteLength(candidate))
      return
    }
    if (typeof candidate === 'boolean') { account(1); return }
    if (typeof candidate === 'number' && Number.isFinite(candidate)) { account(8); return }
    if (typeof candidate !== 'object') throw new Error('Unsupported structured clone value.')
    if (seen.has(candidate)) throw new Error('Structured clone contains a repeated reference.')
    seen.add(candidate)

    if (Array.isArray(candidate)) {
      if (Object.getPrototypeOf(candidate) !== Array.prototype) {
        throw new Error('Structured clone array has an unsafe prototype.')
      }
      const keys = Reflect.ownKeys(candidate)
      if (keys.some((key) => typeof key !== 'string')
        || keys.length !== candidate.length + 1
        || !keys.includes('length')) {
        throw new Error('Structured clone array is sparse or has custom properties.')
      }
      account(2, candidate.length)
      for (let index = 0; index < candidate.length; index += 1) {
        const descriptor = Object.getOwnPropertyDescriptor(candidate, String(index))
        if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) {
          throw new Error('Structured clone array has an accessor or hidden field.')
        }
        visit(descriptor.value, depth + 1)
      }
      return
    }

    const prototype = Object.getPrototypeOf(candidate)
    if (prototype !== Object.prototype && prototype !== null) {
      throw new Error('Structured clone record has an unsafe prototype.')
    }
    const ownKeys = Reflect.ownKeys(candidate)
    if (ownKeys.some((key) => typeof key !== 'string')) {
      throw new Error('Structured clone record has a symbol key.')
    }
    account(2, ownKeys.length)
    for (const key of ownKeys as string[]) {
      const descriptor = Object.getOwnPropertyDescriptor(candidate, key)
      if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) {
        throw new Error('Structured clone record has an accessor or hidden field.')
      }
      if (key.length > limits.maxStringCharacters) {
        throw new Error('Structured clone key exceeds bridge limits.')
      }
      account(byteLength(key))
      visit(descriptor.value, depth + 1)
    }
  }

  visit(value, 0)
}
