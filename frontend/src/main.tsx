import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { AccessGate } from './components/AccessGate'
import { DemoBanner } from './components/DemoBanner'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DemoBanner />
    <AccessGate>
      <App />
    </AccessGate>
  </StrictMode>,
)
