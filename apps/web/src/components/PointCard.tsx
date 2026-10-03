/**
 * One generated learning point.
 *
 * The claim is on top and what it rests on is directly underneath: the file and
 * locator it was drawn from, then any paper it was checked against. A claim
 * shown without its sources is an assertion, and this product does not make
 * assertions.
 */

import type { LearningPoint } from '../lib/types'
import { ConfidenceBadge } from './ConfidenceBadge'
import { PaperLink } from './PaperLink'
import { SupportBadge, SupportMeaning, TopicTags } from './SupportBadge'

export function PointCard({ point }: { point: LearningPoint }) {
  return (
    <li className="point">
      <p className="title">{point.claim}</p>
      {point.detail ? <p className="body">{point.detail}</p> : null}

      <p className="badges">
        <SupportBadge support={point.support} label={point.support_label} />
        {point.evidence_grade_label ? (
          <span className="badge">{point.evidence_grade_label}</span>
        ) : null}
        {point.held ? <span className="badge badge-held">Held for review</span> : null}
      </p>
      <TopicTags topics={point.topics} />

      <details className="support-details">
        <summary>Support and sources</summary>
      <SupportMeaning meaning={point.support_meaning} />
      {point.held && point.hold_reason ? <p className="warn small">{point.hold_reason}</p> : null}

      {point.citations.length > 0 ? (
        <div className="provenance">
          <h4>From your sources</h4>
          <ul className="list small">
            {point.citations.map((citation) => (
              <li key={`${citation.source_id}-${citation.locator}`}>
                <span className="title">{citation.display_name}</span>
                <span className="muted small"> · {citation.locator} · </span>
                <ConfidenceBadge tier={citation.confidence} />
                {citation.quote ? <blockquote className="quote">{citation.quote}</blockquote> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="muted small">No source anchor was recorded for this point.</p>
      )}

      {point.evidence.length > 0 ? (
        <div className="provenance">
          <h4>Checked against</h4>
          <ul className="list small">
            {point.evidence.map((paper) => (
              <li key={`${paper.pmid}-${paper.doi}-${paper.title}`}>
                <span className="title"><PaperLink pmid={paper.pmid} title={paper.title} /></span>
                <p className="muted small">
                  {paper.journal}
                  {paper.published_on ? ` · ${paper.published_on}` : null}
                  {paper.pmid ? ` · PMID ${paper.pmid}` : null}
                  {paper.doi ? ` · DOI ${paper.doi}` : null}
                  {paper.relation ? ` · ${paper.relation}` : null}
                </p>
                {paper.quote ? <blockquote className="quote">{paper.quote}</blockquote> : null}
                {paper.retracted ? (
                  <p className="badge badge-retracted">Retracted — this paper has been withdrawn</p>
                ) : null}
                {paper.corrected ? (
                  <p className="badge badge-corrected">
                    Correction or concern notice — review the notice before using this paper.
                    This is not a retraction.
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      </details>
    </li>
  )
}
