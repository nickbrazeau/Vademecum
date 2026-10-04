/**
 * How well a generated claim is supported, in a badge and then in plain words.
 *
 * The badge alone is jargon, so the meaning the API supplies is printed under
 * it. The strongest thing any of this may be called is "evidence-supported,
 * machine reviewed": nothing here has been clinically validated or approved by
 * a person, and each item says so through its own badge and meaning.
 */

import type { Support } from '../lib/types'

export function SupportBadge({ support, label }: { support: Support; label: string }) {
  return <span className={`badge support-${support}`}>{label}</span>
}

export function SupportMeaning({ meaning }: { meaning: string }) {
  if (meaning === '') return null
  return <p className="muted small">{meaning}</p>
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
