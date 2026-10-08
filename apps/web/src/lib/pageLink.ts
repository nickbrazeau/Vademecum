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


/** The Tutor, opened on one page in one mode (feedback of 6 October: Tutor mode from the map). */
export interface TutorFocus {
  mode: 'questions' | 'socratic'
  entryId: string
  title: string
}

let pendingTutor: TutorFocus | null = null

export function openTutorLater(focus: TutorFocus): void {
  pendingTutor = focus
  try {
    window.history.pushState(null, '', `/tutor?mode=${focus.mode}&page=${encodeURIComponent(focus.entryId)}`)
  } catch {
    /* a test document without history */
  }
}

/** The focus asked for, once: from a link in the app, or from the address. */
export function takePendingTutor(): TutorFocus | null {
  const asked = pendingTutor
  pendingTutor = null
  if (asked) return asked
  try {
    const params = new URLSearchParams(window.location.search)
    const mode = params.get('mode')
    const entryId = params.get('page')
    if ((mode === 'questions' || mode === 'socratic') && entryId) return { mode, entryId, title: '' }
  } catch {
    /* no address to read */
  }
  return null
}
