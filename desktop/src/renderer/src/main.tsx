/** Mounts the React renderer application into the desktop shell root element. */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles.css'
createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>)
