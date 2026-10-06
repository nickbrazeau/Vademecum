/**
 * Opening one encyclopedia page from elsewhere in the app (feedback of 5 October):
 * a flashcard, a question or a map node names its page, and a tap opens it. The
 * page to open is held here across the tab change, and also put in the address
 * (`/encyclopedia?page=<id>`) so a reload or a shared link opens it too.
 */

let pending: string | null = null

export function openPageLater(entryId: string): void {
  pending = entryId
  try {
    window.history.pushState(null, '', `/encyclopedia?page=${encodeURIComponent(entryId)}`)
  } catch {
    /* a test document without history */
  }
}

/** The page asked for, once: from a link in the app, or from the address. */
export function takePendingPage(): string | null {
  const asked = pending
  pending = null
  if (asked) return asked
  try {
    return new URLSearchParams(window.location.search).get('page')
  } catch {
    return null
  }
}
