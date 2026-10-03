/**
 * The shell: a header, five sections, and flag capture that is always one
 * action away.
 */

import { useCallback, useEffect, useState } from 'react'
import { Nav } from './components/Nav'
import { QuickFlagDialog } from './components/QuickFlagDialog'
import { api } from './lib/api'
import { inChat, onToolResult } from './lib/host'
import { ROUTES, isRouteName, useRoute } from './lib/router'
import { ImprovementMap } from './pages/ImprovementMap'
import { Model } from './pages/Model'
import { Sources } from './pages/Sources'
import { Today } from './pages/Today'
import { Tutor } from './pages/Tutor'

const TITLES = {
  today: 'Today',
  tutor: 'Tutor',
  sources: 'Sources',
  map: 'Improvement Map',
  model: 'Model'
} as const

/** ⌘K on macOS, Ctrl-K everywhere else. One keystroke, from anywhere. */
export function isQuickFlagShortcut(event: {
  key: string
  metaKey: boolean
  ctrlKey: boolean
  altKey: boolean
}): boolean {
  return (event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k'
}

export function App() {
  const [route, navigate] = useRoute()
  const [flagOpen, setFlagOpen] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)
  const [online, setOnline] = useState(() => navigator.onLine)
  // Behind the gateway (ADR 0011) the desk belongs to one learner among many:
  // there is a sign-out, and no Model page, because there is no model
  // connection of the learner's own to show. Until health answers, and on the
  // owner's Mac, nothing changes.
  const [behindGateway, setBehindGateway] = useState(false)
  // Inside a conversation (ADR 0014) there is no Model page either: the
  // assistant on the other side of the frame is the model, and a sign-in code
  // must never pass through a chat host.
  const compact = inChat()

  useEffect(() => {
    let cancelled = false
    api
      .health()
      .then((health) => {
        if (!cancelled) setBehindGateway(health.tenancy === 'multi')
      })
      .catch(() => {
        /* the pages report their own failures; the shell stays as it is */
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Inside a conversation, the tool that opened the dashboard may say which
  // page: open_vademecum(view="tutor") lands on the Tutor.
  useEffect(() => {
    if (!compact) return undefined
    return onToolResult((result) => {
      const view = (result as { view?: unknown } | null)?.view
      if (isRouteName(view) && view !== 'model') navigate(view)
    })
  }, [compact, navigate])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isQuickFlagShortcut(event)) return
      // Capture must work while typing in a field, so this deliberately does
      // not exempt inputs.
      event.preventDefault()
      setFlagOpen(true)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => {
    const update = () => setOnline(navigator.onLine)
    window.addEventListener('online', update)
    window.addEventListener('offline', update)
    return () => {
      window.removeEventListener('online', update)
      window.removeEventListener('offline', update)
    }
  }, [])

  const onSaved = useCallback(() => setReloadToken((value) => value + 1), [])

  return (
    <div className="app">
      <header className="header">
        <div className="header-row">
          <h1>Vademecum</h1>
          <button
            type="button"
            className="button primary flag-button"
            onClick={() => setFlagOpen(true)}
          >
            Flag a gap
            <kbd aria-hidden="true">⌘K</kbd>
          </button>
          {behindGateway ? (
            // A plain form to the gateway: the session is an HttpOnly cookie
            // this script cannot see, so signing out is the server's act.
            <form method="post" action="/logout" className="sign-out">
              <button type="submit" className="button">
                Sign out
              </button>
            </form>
          ) : null}
        </div>
        <Nav
          route={route}
          onNavigate={navigate}
          routes={behindGateway || compact ? ROUTES.filter((entry) => entry.name !== 'model') : ROUTES}
        />
        {online || compact ? null : (
          <p className="offline" role="status">
            This device is offline. The app shell is showing from the local cache; your saved work
            is on the Mac and is untouched.
          </p>
        )}
      </header>

      <main id="main">
        <h2 className="visually-hidden">{TITLES[route]}</h2>
        {route === 'today' ? <Today reloadToken={reloadToken} onNavigate={navigate} /> : null}
        {route === 'tutor' ? <Tutor onNavigate={navigate} /> : null}
        {route === 'sources' ? <Sources /> : null}
        {route === 'map' ? <ImprovementMap reloadToken={reloadToken} /> : null}
        {route === 'model' && !compact ? <Model /> : null}
      </main>

      <footer className="footer">
        <p className="muted small">
          A personal learning workspace, augmented by AI. Educational only — not a substitute
          for clinical judgment.
        </p>
      </footer>

      <QuickFlagDialog open={flagOpen} onClose={() => setFlagOpen(false)} onSaved={onSaved} />
    </div>
  )
}
