/**
 * The shell: a header, five sections, and flag capture that is always one
 * action away.
 */

import { useCallback, useEffect, useState } from 'react'
import { Nav } from './components/Nav'
import { QuickFlagDialog } from './components/QuickFlagDialog'
import { api } from './lib/api'
import { inChat, onToolResult } from './lib/host'
import { FIXED_ROUTES, ROUTES, isRouteName, useRoute } from './lib/router'
import { CaseSeries } from './pages/CaseSeries'
import { Encyclopedia } from './pages/Encyclopedia'
import { Flashcards } from './pages/Flashcards'
import { ImprovementMap } from './pages/ImprovementMap'
import { Model } from './pages/Model'
import { Settings } from './pages/Settings'
import { Sources } from './pages/Sources'
import { Today } from './pages/Today'
import { Tutor } from './pages/Tutor'

const TITLES = {
  today: 'Today',
  tutor: 'Tutor',
  flashcards: 'Flashcards',
  encyclopedia: 'Encyclopedia',
  sources: 'Sources',
  map: 'Improvement Map',
  cases: 'Case Series',
  model: 'Model',
  settings: 'Settings'
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
  // In host mode the assistant in the conversation is the model: there is
  // no connection of this machine's own to show (ADR 0009).
  const [hostMode, setHostMode] = useState(false)
  // The tabs the owner chose to see (ADR 0024). Until preferences answer,
  // every tab shows; a tab hidden here is still reachable by its address.
  const [visibleTabs, setVisibleTabs] = useState<string[] | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .preferences()
      .then((preferences) => {
        if (!cancelled) setVisibleTabs(preferences.visible_tabs)
      })
      .catch(() => {
        /* no preference is every tab */
      })
    return () => {
      cancelled = true
    }
  }, [])
  // Inside a conversation (ADR 0014) there is no Model page either: the
  // assistant on the other side of the frame is the model, and a sign-in code
  // must never pass through a chat host.
  const compact = inChat()

  useEffect(() => {
    let cancelled = false
    api
      .health()
      .then((health) => {
        if (!cancelled) {
          setBehindGateway(health.tenancy === 'multi')
          setHostMode(health.model_mode === 'host')
        }
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
          routes={ROUTES.filter(
            (entry) =>
              !((behindGateway || compact || hostMode) && entry.name === 'model') &&
              // No preference, or a malformed one, is every tab: the server never answers fewer than the fixed two.
              (visibleTabs === null || visibleTabs.length === 0 || FIXED_ROUTES.includes(entry.name) || visibleTabs.includes(entry.name))
          )}
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
        {route === 'cases' ? <CaseSeries /> : null}
        {route === 'encyclopedia' ? <Encyclopedia /> : null}
        {route === 'flashcards' ? <Flashcards onNavigate={navigate} /> : null}
        {route === 'settings' ? <Settings onSaved={setVisibleTabs} /> : null}
        {route === 'model' && !compact && !hostMode ? <Model /> : null}
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
