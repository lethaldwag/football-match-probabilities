import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, HashRouter } from 'react-router-dom'
import { STATIC_DATA } from './api/client'
import App from './App'
import './index.css'

// A static host cannot fall back to index.html for unknown paths, so routes live in the URL hash there.
const Router = STATIC_DATA ? HashRouter : BrowserRouter

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Router>
      <App />
    </Router>
  </StrictMode>,
)
