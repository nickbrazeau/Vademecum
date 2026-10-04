/**
 * The review dashboard on Today (ADR 0026): what you reviewed today and this
 * week, and how many days in a row. The owner asked for it to make reviewing a
 * habit. It counts what happened and owes nothing: no target, nothing owed.
 */

import type { Dashboard } from '../lib/types'

const KIND_LABEL: { key: keyof Dashboard['today']; one: string; many: string }[] = [
  { key: 'question', one: 'question', many: 'questions' },
  { key: 'card', one: 'card', many: 'cards' },
  { key: 'page', one: 'page', many: 'pages' },
  { key: 'socratic', one: 'Socratic session', many: 'Socratic sessions' }
]

export function ReviewDashboard({ dashboard }: { dashboard: Dashboard }) {
  const peak = Math.max(1, ...dashboard.history.map((day) => day.count))
  const todayParts = KIND_LABEL.filter(({ key }) => dashboard.today[key] > 0).map(
    ({ key, one, many }) => `${dashboard.today[key]} ${dashboard.today[key] === 1 ? one : many}`
  )
  return (
    <section className="card review-dashboard" aria-labelledby="dashboard-heading">
      <h2 id="dashboard-heading" className="visually-hidden">
        Your review
      </h2>
      <div className={`daily-goal${dashboard.goal_met ? ' met' : ''}`} role="status">
        <p className="daily-goal-line">
          {dashboard.goal_met ? (
            <>
              <strong>Done for today.</strong> {dashboard.today_total} of {dashboard.daily_goal} reviewed.
            </>
          ) : (
            <>
              <strong>{dashboard.remaining_today} more</strong> to reach today’s {dashboard.daily_goal}. {dashboard.today_total} so far.
            </>
          )}
        </p>
        <span className="daily-goal-bar" aria-hidden="true">
          <span className="daily-goal-fill" style={{ width: `${Math.min(100, (100 * dashboard.today_total) / Math.max(1, dashboard.daily_goal))}%` }} />
        </span>
      </div>
      <div className="dashboard-figures">
        <div className="figure">
          <span className="figure-number">{dashboard.days_in_a_row}</span>
          <span className="figure-label">day{dashboard.days_in_a_row === 1 ? '' : 's'} in a row</span>
        </div>
        <div className="figure">
          <span className="figure-number">{dashboard.today_total}</span>
          <span className="figure-label">reviewed today</span>
        </div>
        <div className="figure">
          <span className="figure-number">{dashboard.week_total}</span>
          <span className="figure-label">this week</span>
        </div>
        <div className="figure">
          <span className="figure-number">{dashboard.longest_run}</span>
          <span className="figure-label">longest run</span>
        </div>
      </div>
      <ol className="dashboard-days" aria-label="The last two weeks">
        {dashboard.history.map((day) => (
          <li
            key={day.day}
            className={`day${day.count > 0 ? ' active' : ''}`}
            title={`${day.day}: ${day.count}`}
            style={{ opacity: day.count > 0 ? 0.35 + 0.65 * (day.count / peak) : 1 }}
          />
        ))}
      </ol>
      <p className="muted small">
        {todayParts.length > 0
          ? `Today: ${todayParts.join(', ')}.`
          : dashboard.days_in_a_row > 0
            ? 'Nothing yet today. Any question, card or page keeps the run going.'
            : 'Answer a question, turn a card or review a page to start a run.'}
      </p>
    </section>
  )
}
