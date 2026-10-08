/**
 * Is a newer Vademecum being served than the one on screen? (feedback of 6 October)
 *
 * The web app is a single page: switching tabs never fetches it again, so a tab left
 * open for days keeps an old version while its data comes live from the new server.
 * The build's main script carries a content hash in its name; this compares the one
 * this page loaded with the one the server's page names now. Nothing but the app's
 * own page is fetched, from its own origin.
 */

const MAIN_SCRIPT = /\/assets\/main-[A-Za-z0-9_-]+\.js/

/** The main script this page was built from, or null (in a conversation, or in development). */
export function loadedBuild(doc: Document = document): string | null {
  for (const script of Array.from(doc.querySelectorAll('script[src]'))) {
    const match = (script.getAttribute('src') ?? '').match(MAIN_SCRIPT)
    if (match) return match[0]
  }
  return null
}

/** The main script named by a page's HTML, or null if it names none (a sign-in page, say). */
export function servedBuild(html: string): string | null {
  const match = html.match(MAIN_SCRIPT)
  return match ? match[0] : null
}

/** The build the server serves now when it differs from the one on screen, else null. */
export async function newerBuild(fetcher: typeof fetch = fetch, doc: Document = document): Promise<string | null> {
  const loaded = loadedBuild(doc)
  if (loaded === null) return null
  try {
    const response = await fetcher('/', { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'text/html' } })
    if (!response.ok) return null
    const served = servedBuild(await response.text())
    return served !== null && served !== loaded ? served : null
  } catch {
    return null
  }
}

/** True when the server is serving a different build from the one on screen. */
export async function newerBuildServed(fetcher: typeof fetch = fetch, doc: Document = document): Promise<boolean> {
  return (await newerBuild(fetcher, doc)) !== null
}

/**
 * The address to reload to for a build, or null when a reload for that build was already
 * tried (feedback of 8 October). The build is marked in the address, so a reload that
 * still shows the old app (a cache, a proxy) is tried once, never again in a loop.
 */
export function reloadTarget(served: string, href: string = window.location.href): string | null {
  const mark = (served.match(/main-([A-Za-z0-9_-]+)\.js/) ?? [])[1] ?? served
  const url = new URL(href)
  if (url.searchParams.get('build') === mark) return null
  url.searchParams.set('build', mark)
  return url.toString()
}

/** Whether anything is typed on the page that a reload would lose. */
export function typedSomething(doc: Document = document): boolean {
  for (const field of Array.from(doc.querySelectorAll('textarea, input[type="text"], input[type="search"], input:not([type])'))) {
    if ((field as HTMLInputElement | HTMLTextAreaElement).value.trim() !== '') return true
  }
  return Boolean(doc.querySelector('dialog[open]'))
}
