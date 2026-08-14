/** Provides keyboard-first route navigation with deterministic focus restoration. */

import { Search, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type MouseEvent, type RefObject } from 'react'
import type { NavigationItem } from './contracts'

interface CommandPaletteProps {
  readonly open: boolean
  readonly items: readonly NavigationItem[]
  readonly restoreFocusTo: RefObject<HTMLButtonElement | null>
  readonly onOpenChange: (open: boolean) => void
  readonly onNavigate: (item: NavigationItem) => void
}

/**
 * Filters and selects renderer routes in a modal dialog, restoring focus after dismissal.
 */
export function CommandPalette({ open, items, restoreFocusTo, onOpenChange, onNavigate }: CommandPaletteProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const filterRef = useRef<HTMLInputElement>(null)
  const restoreFocus = useRef(true)
  const wasOpen = useRef(false)
  const [filter, setFilter] = useState('')
  const visibleItems = useMemo(() => {
    const query = filter.trim().toLocaleLowerCase()
    return query.length === 0
      ? items
      : items.filter((item) => `${item.label} ${item.description}`.toLocaleLowerCase().includes(query))
  }, [filter, items])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open) {
      wasOpen.current = true
      restoreFocus.current = true
      if (!dialog.open) {
        if (typeof dialog.showModal === 'function') dialog.showModal()
        else dialog.setAttribute('open', '')
      }
      filterRef.current?.focus()
      return
    }
    if (dialog.open) {
      if (typeof dialog.close === 'function') dialog.close()
      else dialog.removeAttribute('open')
    }
    if (wasOpen.current && restoreFocus.current) restoreFocusTo.current?.focus()
    wasOpen.current = false
    setFilter('')
  }, [open, restoreFocusTo])

  const dismiss = () => {
    restoreFocus.current = true
    onOpenChange(false)
  }

  const selectRoute = (event: MouseEvent<HTMLAnchorElement>, item: NavigationItem) => {
    event.preventDefault()
    restoreFocus.current = false
    onOpenChange(false)
    onNavigate(item)
  }

  return <dialog
    ref={dialogRef}
    className="command-palette"
    aria-labelledby="command-palette-title"
    onCancel={(event) => {
      event.preventDefault()
      dismiss()
    }}
  >
    <div className="command-palette-header">
      <div>
        <p className="eyebrow">Keyboard navigation</p>
        <h2 id="command-palette-title">Go to a workspace</h2>
      </div>
      <button type="button" className="icon-button" aria-label="Close command palette" onClick={dismiss}>
        <X aria-hidden="true" />
      </button>
    </div>
    <label className="command-filter">
      <span className="sr-only">Filter destinations</span>
      <Search aria-hidden="true" />
      <input
        ref={filterRef}
        type="search"
        value={filter}
        placeholder="Filter destinations"
        onChange={(event) => setFilter(event.currentTarget.value)}
      />
    </label>
    {visibleItems.length > 0
      ? <ul className="command-list">
        {visibleItems.map((item) => <li key={item.route}>
          <a href={item.href} onClick={(event) => selectRoute(event, item)}>
            <span><strong>{item.label}</strong><small>{item.description}</small></span>
            <kbd>{item.shortcut}</kbd>
          </a>
        </li>)}
      </ul>
      : <p role="status" className="command-empty">No matching destination.</p>}
  </dialog>
}
