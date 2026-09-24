import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Offline shell: register the precaching service worker (autoUpdate — new
// deploys activate on next launch, no prompt UI). Dynamic import because
// vitest cannot resolve the plugin's virtual module.
if (!import.meta.env.TEST && 'serviceWorker' in navigator) {
  void import('virtual:pwa-register').then(({ registerSW }) => registerSW({ immediate: true }))
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
