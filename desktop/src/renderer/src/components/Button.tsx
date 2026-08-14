/** Provides the reusable typed button variants for the desktop design system. */
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../lib/utils'

const styles = cva('button', { variants: { variant: { primary: 'button-primary', quiet: 'button-quiet' } }, defaultVariants: { variant: 'primary' } })
/** Renders a styled native button, or passes button semantics through a Radix child slot. */
export function Button({ className, variant, asChild = false, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof styles> & { asChild?: boolean }) {
  const Component = asChild ? Slot : 'button'
  return <Component className={cn(styles({ variant }), className)} {...props} />
}
