import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import Router from './Router'
import './styles.css'
import './VisualPolish.css'
import './HeaderExperiment.css'
import './Typography.css'
import './Theme.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Router />
  </StrictMode>,
)
