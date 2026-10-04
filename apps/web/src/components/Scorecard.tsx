/**
 * The Tutor's scorecard (ADR 0026): board questions right and answered, the
 * last week, open answers, flashcards and Socratic sessions, and where you are
 * weakest and strongest. Counts of what happened; nothing is owed.
 */

import type { Scorecard as Card } from '../lib/types'

function percent(correct: number, answered: number): string {
  return answered === 0 ? '–' : `${Math.round((100 * correct) / answered)}%`
}

export function Scorecard({ card }: { card: Card }) {
  return (
    <section className="card scorecard" aria-labelledby="scorecard-heading">
      <h2 id="scorecard-heading">Scorecard</h2>
      <div className="dashboard-figures">
        <div className="figure">
          <span className="figure-number">{percent(card.board.correct, card.board.answered)}</span>
          <span className="figure-label">
            board questions right ({card.board.correct} of {card.board.answered})
          </span>
        </div>
        <div className="figure">
          <span className="figure-number">{percent(card.board.last_7_days.correct, card.board.last_7_days.answered)}</span>
          <span className="figure-label">in the last 7 days ({card.board.last_7_days.answered})</span>
        </div>
        <div className="figure">
          <span className="figure-number">{card.flashcards.reviewed}</span>
          <span className="figure-label">flashcards ({card.flashcards.got_it} got it)</span>
        </div>
        <div className="figure">
          <span className="figure-number">{card.socratic.sessions}</span>
          <span className="figure-label">Socratic sessions</span>
        </div>
        <div className="figure">
          <span className="figure-number">{card.dashboard.days_in_a_row}</span>
          <span className="figure-label">day{card.dashboard.days_in_a_row === 1 ? '' : 's'} in a row</span>
        </div>
      </div>
      {card.weakest_topics.length > 0 || card.strongest_topics.length > 0 ? (
        <div className="scorecard-topics">
          {card.weakest_topics.length > 0 ? (
            <div>
              <h4>Weakest</h4>
              <ul className="list small">
                {card.weakest_topics.map((t) => (
                  <li key={t.topic}>
                    {t.topic} <span className="muted">· {t.correct} of {t.answered}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {card.strongest_topics.length > 0 ? (
            <div>
              <h4>Strongest</h4>
              <ul className="list small">
                {card.strongest_topics.map((t) => (
                  <li key={t.topic}>
                    {t.topic} <span className="muted">· {t.correct} of {t.answered}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
      {card.open_answers.answered > 0 ? (
        <p className="muted small">
          Open answers graded: {card.open_answers.correct} of {card.open_answers.answered} correct.
        </p>
      ) : null}
    </section>
  )
}
