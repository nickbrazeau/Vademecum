/**
 * Six views and a browser history entry each. A router library would be more
 * code than this and would not do anything else this app needs.
 */

import { useCallback, useEffect, useState } from 'react'
import { inChat } from './host'

export type RouteName = 'today' | 'tutor' | 'sources' | 'map' | 'cases' | 'model'

export const ROUTES: { name: RouteName; path: string; label: string }[] = [
  { name: 'today', path: '/', label: 'Today' },
  { name: 'tutor', path: '/tutor', label: 'Tutor' },
  { name: 'sources', path: '/sources', label: 'Sources' },
  { name: 'map', path: '/map', label: 'Improvement Map' },
  { name: 'cases', path: '/cases', label: 'Case Series' },
  { name: 'model', path: '/model', label: 'Model' }
]

/**
 * Piles were renamed to Sources. A bookmark or a pinned tab from before the
 * rename still has to land somewhere correct, so the old path is kept as an
 * alias rather than 404ing into Today.
 */
export const ALIASES: Record<string, RouteName> = {
  '/piles': 'sources'
}

export function isRouteName(value: unknown): value is RouteName {
  return typeof value === 'string' && ROUTES.some((route) => route.name === value)
}

export function routeFor(pathname: string): RouteName {
  const match = ROUTES.find((route) => route.path === pathname)
  if (match !== undefined) return match.name
  return ALIASES[pathname] ?? 'today'
}

export function pathFor(name: RouteName): string {
  return ROUTES.find((route) => route.name === name)?.path ?? '/'
}

export function useRoute(): [RouteName, (name: RouteName) => void] {
  // Inside a conversation (ADR 0014) the frame has no address of its own and
  // its history is not the owner's: the route lives in state only.
  const [route, setRoute] = useState<RouteName>(() => (inChat() ? 'today' : routeFor(window.location.pathname)))

  useEffect(() => {
    if (inChat()) return undefined
    const onPop = () => setRoute(routeFor(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const navigate = useCallback((name: RouteName) => {
    if (!inChat()) window.history.pushState({}, '', pathFor(name))
    setRoute(name)
  }, [])

  return [route, navigate]
}
