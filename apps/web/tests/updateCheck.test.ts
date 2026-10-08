/**
 * Feedback of 6 October: a tab left open across an update showed the old app. The page
 * compares the build it loaded with the one the server names now.
 */

import { describe, expect, it } from 'vitest'
import { loadedBuild, newerBuildServed, servedBuild } from '../src/lib/updateCheck'

function page(script: string | null): Document {
  const doc = document.implementation.createHTMLDocument('t')
  if (script) {
    const tag = doc.createElement('script')
    tag.setAttribute('type', 'module')
    tag.setAttribute('src', script)
    doc.head.appendChild(tag)
  }
  return doc
}

const reply = (html: string, ok = true) => (async () => new Response(html, { status: ok ? 200 : 503 })) as unknown as typeof fetch

describe('the newer-build check', () => {
  it('reads the build from the page and from the served HTML', () => {
    expect(loadedBuild(page('/assets/main-AAAA1111.js'))).toBe('/assets/main-AAAA1111.js')
    expect(loadedBuild(page(null))).toBeNull()
    expect(servedBuild('<script type="module" src="/assets/main-BBBB2222.js"></script>')).toBe('/assets/main-BBBB2222.js')
    expect(servedBuild('<form action="/login"></form>')).toBeNull()
  })

  it('says newer only when the server names a different build', async () => {
    const doc = page('/assets/main-AAAA1111.js')
    expect(await newerBuildServed(reply('<script src="/assets/main-BBBB2222.js"></script>'), doc)).toBe(true)
    expect(await newerBuildServed(reply('<script src="/assets/main-AAAA1111.js"></script>'), doc)).toBe(false)
    expect(await newerBuildServed(reply('<p>Sign in</p>'), doc)).toBe(false)
    expect(await newerBuildServed(reply('', false), doc)).toBe(false)
    expect(await newerBuildServed(reply('<script src="/assets/main-BBBB2222.js"></script>'), page(null))).toBe(false)
  })
})
