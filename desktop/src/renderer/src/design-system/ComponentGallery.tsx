// Development-only gallery for reviewing fictional reusable component states.

import { AsyncState as AsyncStateView } from './AsyncState'
import type { AsyncState } from './contracts'

interface ComponentGalleryProps {
  readonly states: readonly AsyncState[]
}

export function ComponentGallery({ states }: ComponentGalleryProps) {
  return <main className="component-gallery" aria-labelledby="component-gallery-title">
    <div className="gallery-header">
      <p className="eyebrow">Accessibility review surface</p>
      <h1 id="component-gallery-title">Component gallery</h1>
      <p>Fictional states for visual, keyboard, screen-reader, contrast, and zoom review.</p>
    </div>
    <div className="state-gallery">
      {states.map((state) => <AsyncStateView key={state.kind} state={state} />)}
    </div>
  </main>
}
