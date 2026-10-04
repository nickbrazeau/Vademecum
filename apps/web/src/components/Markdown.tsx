/**
 * A small Markdown reader for encyclopedia pages (ADR 0026): headings, paragraphs,
 * lists, emphasis, inline code and links. Everything is built as React elements
 * from text, so nothing in a page is ever handed to the browser as HTML. Links
 * open only to http(s) addresses.
 */

import type { ReactNode } from 'react'
import { API_ROOT } from '../lib/api'

const INLINE = /(\*\*[^*]+\*\*|\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\))/g

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let index = 0
  for (const match of text.matchAll(INLINE)) {
    const token = match[0]
    const at = match.index ?? 0
    if (at > last) out.push(text.slice(last, at))
    const key = `${keyPrefix}-${index++}`
    if (token.startsWith('**')) out.push(<strong key={key}>{token.slice(2, -2)}</strong>)
    else if (token.startsWith('`')) out.push(<code key={key}>{token.slice(1, -1)}</code>)
    else if (token.startsWith('[')) {
      const label = token.slice(1, token.indexOf(']'))
      const href = token.slice(token.indexOf('(') + 1, -1)
      out.push(
        /^https?:\/\//.test(href) ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {label}
          </a>
        ) : (
          label
        )
      )
    } else out.push(<em key={key}>{token.slice(1, -1)}</em>)
    last = at + token.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  let paragraph: string[] = []
  let list: { ordered: boolean; items: string[] } | null = null

  const flushParagraph = () => {
    if (paragraph.length === 0) return
    const joined = paragraph.join(' ')
    const key = `p-${blocks.length}`
    const fromLine = /^\*From .+\*$/.test(joined.trim())
    blocks.push(
      <p key={key} className={fromLine ? 'muted small page-sources' : 'body'}>
        {inline(fromLine ? joined.trim().slice(1, -1) : joined, key)}
      </p>
    )
    paragraph = []
  }
  const flushList = () => {
    if (list === null) return
    const key = `l-${blocks.length}`
    const items = list.items.map((item, i) => <li key={`${key}-${i}`}>{inline(item, `${key}-${i}`)}</li>)
    blocks.push(list.ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>)
    list = null
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    // A figure from the page's file: ../_figures/<image id>.<ext> is the owner's own picture on this Mac.
    const image = /^!\[([^\]]*)\]\((?:\.\.\/)?_figures\/([A-Za-z0-9_-]{1,64})(?:\.[A-Za-z0-9]{1,5})?\)$/.exec(line.trim())
    if (image) {
      flushParagraph()
      flushList()
      const key = `f-${blocks.length}`
      blocks.push(
        <figure key={key} className="page-figure">
          <img src={`${API_ROOT}/images/${image[2]}`} alt={image[1] ?? ''} loading="lazy" />
          {image[1] ? <figcaption className="muted small">From {image[1]}</figcaption> : null}
        </figure>
      )
      continue
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line)
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line)
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    if (heading) {
      flushParagraph()
      flushList()
      const level = heading[1]!.length
      const key = `h-${blocks.length}`
      const content = inline(heading[2] ?? '', key)
      blocks.push(level <= 1 ? <h3 key={key} className="page-title">{content}</h3> : level === 2 ? <h4 key={key}>{content}</h4> : <h5 key={key}>{content}</h5>)
    } else if (bullet || numbered) {
      flushParagraph()
      const ordered = Boolean(numbered)
      if (list === null || list.ordered !== ordered) {
        flushList()
        list = { ordered, items: [] }
      }
      list.items.push((bullet ?? numbered)![1] ?? '')
    } else if (line.trim() === '') {
      flushParagraph()
      flushList()
    } else {
      flushList()
      paragraph.push(line.trim())
    }
  }
  flushParagraph()
  flushList()
  return <div className="markdown">{blocks}</div>
}
