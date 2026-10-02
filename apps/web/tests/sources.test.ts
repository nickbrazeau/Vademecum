// @vitest-environment node
/**
 * Structural guarantees that no single component test would catch, read
 * straight off the source.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = fileURLToPath(new URL('../src', import.meta.url))

function walk(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const full = join(directory, entry)
    return statSync(full).isDirectory() ? walk(full) : [full]
  })
}

const FILES = walk(SRC).filter((path) => /\.(ts|tsx|css)$/.test(path))
const sources = FILES.map((path) => ({ path, text: readFileSync(path, 'utf8') }))
const label = (path: string) => path.slice(SRC.length + 1)

describe('the vocabulary this product refuses to use', () => {
  // From the archive's CONTEXT.md: ambient delivery means no queue to clear,
  // no count to hit, and no run to keep going.
  const FORBIDDEN = [
    'due count',
    'review queue',
    'streak',
    'daily review',
    'items due',
    'missed question',
    'review session',
    'flashcard'
  ]

  it('appears nowhere in the interface', () => {
    const offenders: string[] = []
    for (const { path, text } of sources) {
      const lowered = text.toLowerCase()
      for (const word of FORBIDDEN) {
        if (lowered.includes(word)) offenders.push(`${label(path)}: ${word}`)
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('no network destination other than the local API', () => {
  it('allows only explicit PubMed citation navigation, not external API origins', () => {
    const offenders: string[] = []
    for (const { path, text } of sources) {
      const urls = text.match(/https?:\/\/[^\s'"`)]+/g) ?? []
      for (const url of urls) {
        if (/127\.0\.0\.1|localhost|www\.w3\.org/.test(url)) continue
        if (label(path) === 'components/PaperLink.tsx' && url.startsWith('https://pubmed.ncbi.nlm.nih.gov/')) continue
        offenders.push(`${label(path)}: ${url}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('names no provider endpoint, key, or analytics', () => {
    const forbidden = [
      'openai.com',
      'anthropic',
      'api_key',
      'apikey',
      'analytics',
      'gtag',
      'sentry'
    ]
    const offenders: string[] = []
    for (const { path, text } of sources) {
      const lowered = text.toLowerCase()
      for (const word of forbidden) {
        if (lowered.includes(word)) offenders.push(`${label(path)}: ${word}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('names OpenAI only where it explains what would be sent', () => {
    // ADR 0002 rule 3 requires the interface to say where a model action would
    // send things. That sentence has to name OpenAI, and so does the privacy
    // note, which has to say that checking sign-in and usage already reaches
    // OpenAI through Codex rather than implying the connection is inert.
    // Everywhere else, the word appearing at all would mean the app had grown
    // a second destination.
    const ALLOWED = ['pages/Model.tsx', 'components/PrivacyNote.tsx', 'components/TransmissionDisclosure.tsx']
    const offenders = sources
      .filter(({ path, text }) => /openai/i.test(text) && !ALLOWED.includes(label(path)))
      .map(({ path }) => label(path))
    expect(offenders).toEqual([])
  })

  it('does not tell the owner that nothing is sent', () => {
    // The true claim is narrower: no learning content is transmitted. "Nothing
    // is sent" reads as "no network traffic", and account, sign-in and usage
    // operations go through Codex, which contacts OpenAI.
    const offenders = sources
      .filter(({ text }) => /nothing (is|was) sent/i.test(text))
      .map(({ path }) => label(path))
    expect(offenders).toEqual([])
  })

  it('says out loud that no API key is used', () => {
    const page = sources.find(({ path }) => path.endsWith('pages/Model.tsx'))?.text ?? ''
    expect(page).toMatch(/no API key is used/i)
    const note = sources.find(({ path }) => path.endsWith('PrivacyNote.tsx'))?.text ?? ''
    expect(note).toMatch(/no API key is used/i)
  })

  it('calls fetch only through the API client', () => {
    const offenders = sources
      .filter(({ path, text }) => /\bfetch\(/.test(text) && !/lib\/api\.ts$|sw\/sw\.ts$/.test(path))
      .map(({ path }) => label(path))
    expect(offenders).toEqual([])
  })
})

describe('the service worker', () => {
  it('consults the cache policy before every store', () => {
    const worker = sources.find(({ path }) => path.endsWith('sw/sw.ts'))
    expect(worker).toBeDefined()
    const text = worker?.text ?? ''
    // The only cache.put in the worker is inside the guarded helper.
    expect(text.match(/cache\.put\(/g) ?? []).toHaveLength(1)
    expect(text).toContain('mayStore(')
    expect(text).toContain('isPrivatePath(')
  })

  it('is registered at the root scope only, and only in a build', () => {
    const register = sources.find(({ path }) => path.endsWith('registerServiceWorker.ts'))
    expect(register?.text).toContain("scope: '/'")
    expect(register?.text).toContain('import.meta.env.PROD')
  })
})

describe('accessibility and touch baselines (ADR 0004)', () => {
  const css = sources.find(({ path }) => path.endsWith('styles.css'))?.text ?? ''

  it('sets a 44px minimum tap target', () => {
    expect(css).toContain('--tap: 44px')
    expect(css).toMatch(/min-height:\s*var\(--tap\)/)
  })

  it('keeps inputs at 16px so iOS Safari does not zoom on focus', () => {
    expect(css).toMatch(/font-size:\s*1rem;\s*\/\* 16px minimum/)
  })

  it('respects the safe area and both colour schemes', () => {
    expect(css).toContain('env(safe-area-inset-')
    expect(css).toContain('prefers-color-scheme: dark')
    expect(css).toContain('prefers-reduced-motion')
  })
})

describe('drafts', () => {
  it('are the only thing written to browser storage', () => {
    const offenders = sources
      // Usage, not prose: a doc comment naming localStorage is not a write.
      .filter(
        ({ path, text }) =>
          /\b(localStorage|sessionStorage|indexedDB)\s*[.[]/.test(text) &&
          !path.endsWith('lib/drafts.ts')
      )
      .map(({ path }) => label(path))
    expect(offenders).toEqual([])
  })
})
