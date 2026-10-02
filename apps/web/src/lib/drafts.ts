/**
 * Local drafts.
 *
 * Browser storage holds regenerable drafts and nothing else: Safari evicts
 * unused origin storage, so the SQLite database on the Mac is the canonical
 * copy, always (ADR 0004). A draft is what you have typed and not yet saved —
 * losing it is an annoyance, not a data loss.
 *
 * Autosave writes here. It never calls the API, and it never calls a model.
 */

const PREFIX = 'vademecum.draft.'

export const FLAG_DRAFT_KEY = `${PREFIX}flag`

export const ITEM_DRAFT_PREFIX = `${PREFIX}item.`

export const ANSWER_DRAFT_PREFIX = `${PREFIX}answer.`

/** One draft per pile: two piles open at once must not share a body. */
export function itemDraftKey(pileId: string): string {
  return `${ITEM_DRAFT_PREFIX}${pileId}`
}

/**
 * One draft per question. A half-typed answer is worth keeping across a
 * refresh; it is still only a draft, and nothing here calls a model.
 */
export function answerDraftKey(questionId: string): string {
  return `${ANSWER_DRAFT_PREFIX}${questionId}`
}

function storage(): Storage | null {
  try {
    // Private browsing and disabled storage both throw on access.
    return window.localStorage
  } catch {
    return null
  }
}

export function loadDraft(key: string): string {
  return storage()?.getItem(key) ?? ''
}

export function saveDraft(key: string, value: string): void {
  const store = storage()
  if (store === null) return
  try {
    if (value.trim() === '') store.removeItem(key)
    else store.setItem(key, value)
  } catch {
    // A full or evicted store is not worth interrupting the owner for.
  }
}

export function clearDraft(key: string): void {
  try {
    storage()?.removeItem(key)
  } catch {
    /* nothing to do */
  }
}

/** Every draft key currently held. Used by the privacy panel to be specific. */
export function draftKeys(): string[] {
  const store = storage()
  if (store === null) return []
  const keys: string[] = []
  for (let index = 0; index < store.length; index += 1) {
    const key = store.key(index)
    if (key !== null && key.startsWith(PREFIX)) keys.push(key)
  }
  return keys
}
