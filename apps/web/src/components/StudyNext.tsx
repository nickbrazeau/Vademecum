/**
 * Next steps (ADR 0031): the learner model's suggestion for the next few minutes, on
 * the Improvement Map. Each step names the topic, how it stands and why, what the
 * estimate rests on, and one thing to do there. These are suggestions to take or ignore.
 */

import type { MouseEvent } from 'react'
import { openPageLater, openTutorLater } from '../lib/pageLink'
import type { RouteName } from '../lib/router'
import type { KnowledgeState, LearnerModel, LearnerUnit } from '../lib/types'

/** A short meter for a 0..1 estimate, read aloud as words. */
export function Meter({ value, label }: { value: number; label: string }) {
  const words = value >= 0.75 ? 'high' : value >= 0.45 ? 'middling' : 'low'
  return (
    <span className="meter" role="img" aria-label={`${label}: ${words}`} title={`${label}: ${words}`}>
      <span className="meter-fill" style={{ width: `${Math.round(Math.max(0.04, Math.min(1, value)) * 100)}%` }} />
    </span>
  )
}

export function StateBadge({ state, label }: { state: KnowledgeState; label: string }) {
  return <span className={`badge know-badge know-${state}`}>{label}</span>
}

function href(unit: LearnerUnit): string {
  const id = encodeURIComponent(unit.entry_id ?? '')
  switch (unit.next.kind) {
    case 'read':
      return `/encyclopedia?page=${id}`
    case 'board':
      return `/tutor?mode=questions&page=${id}`
    case 'socratic':
      return `/tutor?mode=socratic&page=${id}`
    case 'flashcards':
      return '/flashcards'
    default:
      return '/sources'
  }
}

export function goToStep(unit: LearnerUnit, onNavigate: (name: RouteName) => void): void {
  const entryId = unit.entry_id
  if (unit.next.kind === 'read' && entryId) {
    openPageLater(entryId)
    onNavigate('encyclopedia')
  } else if ((unit.next.kind === 'board' || unit.next.kind === 'socratic') && entryId) {
    openTutorLater({ mode: unit.next.kind === 'board' ? 'questions' : 'socratic', entryId, title: unit.title })
    onNavigate('tutor')
  } else if (unit.next.kind === 'flashcards') {
    onNavigate('flashcards')
  } else {
    onNavigate('sources')
  }
}

export function StepButton({ unit, onNavigate }: { unit: LearnerUnit; onNavigate?: (name: RouteName) => void }) {
  const go = (event: MouseEvent) => {
    if (!onNavigate) return
    event.preventDefault()
    goToStep(unit, onNavigate)
  }
  return (
    <a className="button small" href={href(unit)} onClick={go}>
      {unit.next.label}
    </a>
  )
}

export function StudyNext({ model, onNavigate }: { model: LearnerModel; onNavigate?: (name: RouteName) => void }) {
  if (model.plan.length === 0) {
    return (
      <p className="muted">
        Nothing stands out yet. Answer a few board questions or flashcards, or flag what you are unsure of, and suggestions appear here.
      </p>
    )
  }
  return (
    <ol className="study-next">
      {model.plan.map((unit) => (
        <li key={unit.key} className={`study-step know-${unit.state}`}>
          <div className="study-step-head">
            <strong>{unit.title}</strong>
            <StateBadge state={unit.state} label={unit.state_label} />
          </div>
          <p className="small">{unit.next.why}</p>
          {unit.evidence.length > 0 ? <p className="muted small">{unit.evidence.join(' ')}</p> : null}
          <StepButton unit={unit} onNavigate={onNavigate} />
        </li>
      ))}
    </ol>
  )
}

/** One topic's estimate, for the map's selected-topic panel. */
export function TopicKnowledge({ unit, onNavigate }: { unit: LearnerUnit; onNavigate?: (name: RouteName) => void }) {
  return (
    <div className="topic-knowledge">
      <h4>
        What you know <StateBadge state={unit.state} label={unit.state_label} />
      </h4>
      <dl className="knowledge-figures small">
        <dt>Understood</dt>
        <dd>
          <Meter value={unit.understood} label="Understood" />
        </dd>
        <dt>Holding now</dt>
        <dd>{unit.recall === null ? <span className="muted">not yet recalled</span> : <Meter value={unit.recall} label="Holding now" />}</dd>
        <dt>Evidence</dt>
        <dd>{unit.confidence >= 0.7 ? 'plenty' : unit.confidence >= 0.35 ? 'some' : 'little so far'}</dd>
      </dl>
      {unit.evidence.length > 0 ? (
        <ul className="list small">
          {unit.evidence.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
      <p className="small">{unit.next.why}</p>
      <StepButton unit={unit} onNavigate={onNavigate} />
    </div>
  )
}
