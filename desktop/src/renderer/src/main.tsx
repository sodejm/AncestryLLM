/** Mounts the isolated React renderer into the trusted desktop document root. */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles.css'

const root = createRoot(document.getElementById('root')!)

async function render() {
  if (import.meta.env.DEV && import.meta.env.MODE === 'gallery') {
    const [{ ComponentGallery }, { componentStateFixtures }] = await Promise.all([
      import('./design-system/ComponentGallery'),
      import('../../mock-bridge/shell-fixtures'),
    ])
    root.render(
      <StrictMode><ComponentGallery states={Object.values(componentStateFixtures)}/></StrictMode>,
    )
    return
  }

  root.render(<StrictMode><App/></StrictMode>)
}

void render()
