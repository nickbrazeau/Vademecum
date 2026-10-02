import { ROUTES, type RouteName } from '../lib/router'

export function Nav({
  route,
  onNavigate,
  routes = ROUTES
}: {
  route: RouteName
  onNavigate: (name: RouteName) => void
  /** Behind the gateway the owner's Model page is not offered (ADR 0011). */
  routes?: typeof ROUTES
}) {
  return (
    <nav className="nav" aria-label="Sections">
      {routes.map((entry) => (
        <a
          key={entry.name}
          href={entry.path}
          aria-current={route === entry.name ? 'page' : undefined}
          onClick={(event) => {
            // Plain links, so a middle click and a bookmark both still work.
            if (event.metaKey || event.ctrlKey || event.shiftKey) return
            event.preventDefault()
            onNavigate(entry.name)
          }}
        >
          {entry.label}
        </a>
      ))}
    </nav>
  )
}
