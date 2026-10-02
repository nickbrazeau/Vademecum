/**
 * How much of a pile has actually been through the model.
 *
 * A pile that is half covered must never read as finished, so the words come
 * first and the bar is decoration. "Complete" is only ever said when the API
 * says every passage has been covered.
 */

import type { BatchCoverage, Coverage } from '../lib/types'

export function CoverageBar({ coverage }: { coverage: Coverage }) {
  const { covered, total, unit } = coverage
  return (
    <div className="coverage">
      <p className="body">
        {total === 0
          ? 'Nothing to cover yet: no readable text in this pile.'
          : coverage.complete
            ? `All ${total} extracted-text ${unit} have been processed.`
            : `${covered} of ${total} extracted-text ${unit} processed; ${total - covered} remain.`}
      </p>
      <div className="usage-bar" aria-hidden="true">
        <div className="usage-fill" style={{ width: `${Math.max(0, Math.min(coverage.complete ? 100 : 99, coverage.percent))}%` }} />
      </div>
      <p className="muted small">Coverage is of extracted text only, not diagrams, image-only pages or omitted text. Processing does not mean clinical validation.</p>
    </div>
  )
}

/** The coverage sentence for one batch, before it is sent. */
export function BatchCoverageNote({ coverage }: { coverage: BatchCoverage }) {
  return (
    <p className="body">
      This batch contains {coverage.chars_in_batch} of {coverage.chars_total} extracted-text characters;{' '}
      {coverage.chars_covered} already processed; {coverage.chars_remaining_after} remaining
      after a successful build.
    </p>
  )
}
