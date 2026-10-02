/**
 * How well a generated claim is supported, in a badge and then in plain words.
 *
 * The badge alone is jargon, so the meaning the API supplies is printed under
 * it. The strongest thing any of this may be called is "evidence-supported,
 * machine reviewed": nothing here has been clinically validated or approved by
 * a person, and the caveat is stated wherever generated material is listed.
 */

import type { Support } from '../lib/types'

export const MACHINE_REVIEWED =
  'Model-generated learning material. Each item shows its own source or evidence support and any hold. Machine review is not clinical validation or human approval. Check current references before applying it.'

export function SupportBadge({ support, label }: { support: Support; label: string }) {
  return <span className={`badge support-${support}`}>{label}</span>
}

export function SupportMeaning({ meaning }: { meaning: string }) {
  if (meaning === '') return null
  return <p className="muted small">{meaning}</p>
}

/** Said once per section rather than once per claim. */
export function MachineReviewedNote() {
  return (
    <p className="muted small" role="note">
      {MACHINE_REVIEWED}
    </p>
  )
}

export function TopicTags({ topics }: { topics: string[] }) {
  if (topics.length === 0) return null
  return (
    <ul className="tags" aria-label="Topics">
      {topics.map((topic) => (
        <li key={topic} className="tag">
          {topic}
        </li>
      ))}
    </ul>
  )
}
