/**
 * The entry point for the app inside a conversation (ADR 0014).
 *
 * Same App, different place: no service worker, no history, and every request
 * routed through the chat host as a tool call. `markInChat()` is the one
 * switch; the rest of the app reads it.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { detectHost, markInChat, watchHeight } from './lib/host'
import './styles.css'

markInChat()
document.documentElement.setAttribute('data-host', 'chat')

const root = document.getElementById('root')
if (root !== null) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>
  )
}

void detectHost().then(() => watchHeight())
