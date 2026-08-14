/** Supplies shared renderer class-name composition utilities. */
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
/**
 * Merges conditional class names while resolving conflicting Tailwind utility tokens.
 */
export const cn = (...inputs: ClassValue[]): string => twMerge(clsx(inputs))
