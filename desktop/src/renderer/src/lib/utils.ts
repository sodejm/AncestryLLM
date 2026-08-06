/** Provides shared renderer utility helpers for merging Tailwind and conditional class names. */
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
export const cn = (...inputs: ClassValue[]): string => twMerge(clsx(inputs))
