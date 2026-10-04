/**
 * Strong and weak, and why (ADR 0026). Specialty by specialty, weakest first;
 * open one to see its topics, open a topic to see the reasons and the very
 * evidence: the board questions missed, the flags, the exam-report lines.
 */

import type { SpecialtyStrength, Strengths, TopicStrength } from '../lib/types'

const LABEL: Record<TopicStrength['label'], string> = { weak: 'Weak', mixed: 'Mixed', strong: 'Strong' }

/** A bar from weak (left, red) to strong (right, green), centred on mixed. */
function Bar({ score }: { score: number }) {
  const clamped = Math.max(-3, Math.min(3, score))
  const width = (Math.abs(clamped) / 3) * 50
  return (
    <span className="strength-bar" aria-hidden="true">
      <span className={`strength-fill ${clamped < 0 ? 'weak' : 'strong'}`} style={clamped < 0 ? { right: '50%', width: `${width}%` } : { left: '50%', width: `${width}%` }} />
      <span className="strength-mid" />
    </span>
  )
}

function Topic({ topic }: { topic: TopicStrength }) {
  const { missed_questions: missed, flags, exam_areas: areas } = topic.evidence
  return (
    <details className={`strength-topic standing-${topic.label}`}>
      <summary>
        <span className="strength-name">{topic.topic}</span>
        <span className={`badge strength-${topic.label}`}>{LABEL[topic.label]}</span>
        <Bar score={topic.score} />
      </summary>
      <ul className="list small">
        {topic.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
      {missed.length > 0 ? (
        <>
          <h5>Questions missed</h5>
          <ul className="list small">
            {missed.map((stem) => (
              <li key={stem} className="muted">
                {stem}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {flags.length > 0 ? (
        <>
          <h5>Flagged</h5>
          <ul className="list small">
            {flags.map((text) => (
              <li key={text} className="muted">
                {text}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {areas.length > 0 ? (
        <>
          <h5>Exam reports</h5>
          <ul className="list small">
            {areas.map((area) => (
              <li key={area.quote} className="muted">
                {area.standing}: “{area.quote}”
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </details>
  )
}

function Specialty({ group }: { group: SpecialtyStrength }) {
  return (
    <details className={`strength-specialty standing-${group.label}`} open={group.weak > 0 && group.label === 'weak'}>
      <summary>
        <span className="strength-name">{group.name}</span>
        <span className="muted small">
          {group.weak} weak · {group.strong} strong · {group.topics.length} topic{group.topics.length === 1 ? '' : 's'}
        </span>
        <Bar score={group.score} />
      </summary>
      <div className="strength-topics">
        {group.topics.map((topic) => (
          <Topic key={topic.topic} topic={topic} />
        ))}
      </div>
    </details>
  )
}

export function StrengthsView({ strengths }: { strengths: Strengths }) {
  if (strengths.specialties.length === 0) {
    return (
      <p className="muted">
        Nothing to weigh yet. Board questions answered, flashcards turned, flags and exam reports fill this in, topic by topic.
      </p>
    )
  }
  return (
    <div className="strengths">
      <p className="muted small">Weakest first. Open a specialty for its topics, and a topic for the reasons and the evidence.</p>
      {strengths.specialties.map((group) => (
        <Specialty key={group.id} group={group} />
      ))}
    </div>
  )
}
