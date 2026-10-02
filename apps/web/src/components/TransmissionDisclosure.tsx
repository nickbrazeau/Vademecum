/**
 * What is about to leave this Mac, shown before the button that would send it.
 *
 * ADR 0002 rule 3: local-first does not mean local inference. Two actions in
 * this app transmit content, and each one says exactly what it will send,
 * every time, before it sends it. The list is exhaustive on purpose — "and
 * nothing else" is a promise the rest of the code has to keep.
 */

import type { ReactNode } from 'react'

export interface Disclosure {
  headline: string
  bullets: string[]
  destination: string
}

/** Grading. Named here so the Tutor page holds no destination of its own. */
export const GRADING_DISCLOSURE: Disclosure = {
  headline: 'Pressing Grade sends three things, and nothing else:',
  bullets: [
    'the question on screen',
    'its reference answer and rubric',
    'the answer you have typed'
  ],
  destination: 'OpenAI, through Codex on this Mac. No API key is used.'
}

/** Checking the literature. Topic words only — never your material. */
export const LITERATURE_DISCLOSURE: Disclosure = {
  headline: 'Checking the literature sends public search information:',
  bullets: ['the short public search words shown in your watched topics, including any suggestions you choose', 'PubMed record identifiers for retrieving and refreshing the papers'],
  destination:
    'PubMed (NCBI). None of your material, notes or answers is included, and no model is involved.'
}

export function TransmissionDisclosure({
  disclosure,
  children
}: {
  disclosure: Disclosure
  children?: ReactNode
}) {
  return (
    <section className="disclosure-panel" aria-label="What this will send">
      <p className="disclosure-headline">{disclosure.headline}</p>
      <ul className="disclosure-list">
        {disclosure.bullets.map((bullet) => (
          <li key={bullet}>{bullet}</li>
        ))}
      </ul>
      <p className="muted small">
        <strong>Where it goes:</strong> {disclosure.destination}
      </p>
      {children}
    </section>
  )
}
