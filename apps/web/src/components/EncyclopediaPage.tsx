/**
 * One encyclopedia page (ADR 0023): the title, the summary, the sections, and
 * after every paragraph the sources it rests on. Nothing on a page is
 * unsourced, and the page says so by showing where each paragraph came from
 * rather than by claiming to be verified.
 */

import { dateLabel, momentLabel } from '../lib/format'
import { API_ROOT } from '../lib/api'
import type { EncyclopediaEntry, PageCitation, PageFigure } from '../lib/types'
import { Markdown } from './Markdown'
import { PaperLink } from './PaperLink'

function sourceLabels(pointIds: string[], citations: Map<string, PageCitation>): string[] {
  const labels: string[] = []
  for (const id of pointIds) {
    const citation = citations.get(id)
    if (!citation) continue
    for (const source of citation.sources) {
      const label = source.locator ? `${source.display_name}, ${source.locator}` : source.display_name
      if (!labels.includes(label)) labels.push(label)
    }
    if (citation.sources.length === 0 && !labels.includes(citation.claim)) labels.push(citation.claim)
  }
  return labels
}

/** A picture from the owner's source, with where it came from. Served by this Mac; never generated. */
export function Figure({ figure }: { figure: PageFigure }) {
  return (
    <figure className="page-figure">
      <img
        src={`${API_ROOT}/images/${figure.image_id}`}
        alt={`From ${figure.source}, ${figure.locator}`}
        loading="lazy"
        width={figure.width}
        height={figure.height}
      />
      <figcaption className="muted small">
        From {figure.source}, {figure.locator}
      </figcaption>
    </figure>
  )
}

export function EncyclopediaPage({ page, compact = false }: { page: EncyclopediaEntry; compact?: boolean }) {
  const citations = new Map(page.citations.map((citation) => [citation.id, citation]))
  return (
    <article className="encyclopedia-page">
      {page.edited ? null : <h3 className="page-title">{page.title}</h3>}
      <p className="muted small">
        Compiled from {page.point_count} learning point{page.point_count === 1 ? '' : 's'} in your sources
        {page.version > 1 ? ` · rewritten ${page.version - 1} time${page.version === 2 ? '' : 's'}` : null}
        {page.question_count > 0 ? ` · ${page.question_count} board question${page.question_count === 1 ? '' : 's'}` : null}
      </p>
      {page.edited ? (
        <>
          <p className="badges">
            <span className="badge">Your edit</span>
            {page.edit_outdated ? <span className="badge">The compiled page has changed since</span> : null}
          </p>
          <Markdown text={page.body_md} />
        </>
      ) : null}
      {!page.edited && page.summary ? <p className="body page-summary">{page.summary}</p> : null}
      {page.edited ? null : page.sections.map((section) => (
        <section key={section.heading} className="page-section">
          <h4>{section.heading}</h4>
          {section.paragraphs.map((paragraph, index) => {
            const labels = sourceLabels(paragraph.point_ids, citations)
            return (
              <div key={`${section.heading}-${index}`} className="page-paragraph">
                <p className="body">{paragraph.text}</p>
                {(paragraph.figures ?? []).map((figure) => (
                  <Figure key={figure.image_id} figure={figure} />
                ))}
                {labels.length > 0 ? (
                  <p className="muted small page-sources">From {labels.join(' · ')}</p>
                ) : null}
              </div>
            )
          })}
        </section>
      ))}
      {page.literature.length > 0 ? (
        <section className="page-section page-literature">
          <h4>Literature reviewed for this page</h4>
          <ul className="list small">
            {page.literature.map((record) => (
              <li key={record.record_id}>
                <PaperLink pmid={record.pmid} title={record.title} />
                <span className="muted small">
                  {record.journal ? ` · ${record.journal}` : null}
                  {record.published_on ? ` · ${dateLabel(record.published_on)}` : null}
                  {record.priority === 'guideline' ? ' · guideline' : null}
                  {record.cited ? ' · drawn on above' : null}
                </span>
                {record.retracted ? <span className="badge badge-retracted"> Retracted</span> : null}
                {record.corrected && !record.retracted ? <span className="badge badge-corrected"> Correction notice</span> : null}
              </li>
            ))}
          </ul>
          {page.literature_checked_at ? <p className="muted small">Searched {momentLabel(page.literature_checked_at)}; PubMed, the topic’s own words only.</p> : null}
        </section>
      ) : page.literature_note ? (
        <p className="muted small">{page.literature_note}</p>
      ) : null}
      {!compact && page.citations.length > 0 ? (
        <details className="support-details">
          <summary>
            Where this page comes from: {page.citations.length} learning point{page.citations.length === 1 ? '' : 's'}, each with the passage in your
            sources it was taken from
          </summary>
          <ul className="list small">
            {page.citations.map((citation) => (
              <li key={citation.id}>
                <span className="title">{citation.claim}</span>
                <span className="muted small"> · {citation.support_label}</span>
                {citation.sources.map((source) => (
                  <blockquote key={`${source.source_id}-${source.locator}`} className="quote">
                    {source.quote}
                    <footer className="muted small">
                      {source.display_name}
                      {source.locator ? ` · ${source.locator}` : null}
                    </footer>
                  </blockquote>
                ))}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {page.status_detail ? <p className="muted small">{page.status_detail}</p> : null}
    </article>
  )
}
