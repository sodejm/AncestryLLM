/** Renders sanitized file-grant metadata without exposing native paths. */
import type { FileGrant } from '../../../shared-contract/desktop'

function readableSize(bytes: number): string {
  if (bytes < 1_000) return `${bytes} B`
  if (bytes < 1_000_000) return `${Math.round(bytes / 1_000)} KB`
  if (bytes < 1_000_000_000) return `${Math.round(bytes / 1_000_000)} MB`
  return `${Math.round(bytes / 1_000_000_000)} GB`
}

/** Displays only the sanitized metadata and access intent carried by an opaque file grant. */
export function FileGrantCard({ grant }: Readonly<{ grant: Readonly<FileGrant> }>) {
  return (
    <article aria-label={`Selected file ${grant.metadata.displayName}`} className="rounded-lg border border-slate-300 p-4">
      <h3 className="font-semibold">{grant.metadata.displayName}</h3>
      <dl className="mt-2 grid grid-cols-2 gap-1 text-sm">
        <dt>Format</dt><dd>{grant.metadata.format.toUpperCase()}</dd>
        <dt>Size</dt><dd>{readableSize(grant.metadata.sizeBytes)}</dd>
        <dt>Intent</dt><dd>{grant.access === 'read' ? 'Read only' : 'Write destination'}</dd>
      </dl>
      {grant.metadata.validation === 'replacement-confirmed' && (
        <p role="status" className="mt-2 text-sm font-semibold text-amber-800">Existing file replacement confirmed</p>
      )}
    </article>
  )
}
