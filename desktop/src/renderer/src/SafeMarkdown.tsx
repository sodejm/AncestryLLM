/** Renders bounded model Markdown through a fixed inert React element allowlist. */
import { Children, type ReactNode } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** Maximum model-output characters accepted by the bounded Markdown renderer. */
export const MAX_RENDERED_CHAT_CHARACTERS = 16_384

const allowedElements = Object.freeze([
  'a',
  'blockquote',
  'br',
  'code',
  'del',
  'em',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'li',
  'ol',
  'p',
  'pre',
  'strong',
  'table',
  'tbody',
  'td',
  'th',
  'thead',
  'tr',
  'ul',
] as const)

function visibleText(children: ReactNode): string {
  return Children.toArray(children).map((child) => {
    if (typeof child === 'string' || typeof child === 'number') return String(child)
    if (child && typeof child === 'object' && 'props' in child) {
      return visibleText((child as { props: { children?: ReactNode } }).props.children)
    }
    return ''
  }).join('')
}

function explicitPublicHttpsDestination(href: string | undefined, label: string): string | null {
  if (href === undefined || label.trim() === href.trim()) return null
  try {
    const destination = new URL(href)
    if (destination.protocol !== 'https:'
      || destination.hostname === ''
      || destination.username !== ''
      || destination.password !== ''
      || destination.port !== ''
      || destination.href.length > 2_048) return null
    return destination.href
  } catch {
    return null
  }
}

/** Inputs for rendering bounded Markdown and delegating reviewed external-link confirmation. */
export interface SafeMarkdownProps {
  readonly content: string
  readonly onOpenExternal: (destination: string) => void | Promise<void>
}

/**
 * Renders model output through a fixed React AST. Raw HTML, embedded media,
 * automatic links, and model-authored executable controls never reach the DOM.
 */
export function SafeMarkdown({ content, onOpenExternal }: SafeMarkdownProps) {
  const characters = Array.from(content)
  const truncated = characters.length > MAX_RENDERED_CHAT_CHARACTERS
  const boundedContent = characters.slice(0, MAX_RENDERED_CHAT_CHARACTERS).join('')

  return (
    <div className="safe-markdown" data-markdown-truncated={String(truncated)}>
      <Markdown
        allowedElements={allowedElements}
        components={{
          a: ({ children, href }) => {
            const label = visibleText(children)
            const destination = explicitPublicHttpsDestination(href, label)
            if (destination === null) return <span>{children}</span>
            return (
              <span className="safe-markdown__external-link">
                <button
                  type="button"
                  aria-label={`Open external link ${label}: ${destination}`}
                  onClick={() => { void onOpenExternal(destination) }}
                >
                  {children}
                </button>
                <code>{destination}</code>
              </span>
            )
          },
        }}
        remarkPlugins={[remarkGfm]}
        skipHtml
        unwrapDisallowed={false}
      >
        {boundedContent}
      </Markdown>
      {truncated ? (
        <p className="safe-markdown__boundary-notice" role="status">
          Response display was limited to 16,384 characters.
        </p>
      ) : null}
    </div>
  )
}
