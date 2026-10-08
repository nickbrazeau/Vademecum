/**
 * Feedback of 6 October: a tab left open across an update showed the old app. The page
 * compares the build it loaded with the one the server names now.
 */

import { describe, expect, it } from 'vitest'
import { loadedBuild, newerBuildServed, servedBuild, typedSomething } from '../src/lib/updateCheck'

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

describe('reloading on return', () => {
  it('holds back while something is typed or a dialog is open', () => {
    const doc = document.implementation.createHTMLDocument('t')
    expect(typedSomething(doc)).toBe(false)
    const area = doc.createElement('textarea')
    doc.body.appendChild(area)
    expect(typedSomething(doc)).toBe(false)
    area.value = 'half an answer'
    expect(typedSomething(doc)).toBe(true)
    area.value = ''
    const dialog = doc.createElement('dialog')
    dialog.setAttribute('open', '')
    doc.body.appendChild(dialog)
    expect(typedSomething(doc)).toBe(true)
  })
})

describe('reloading for a newer build', () => {
  it('tries one reload per build, marked in the address, and never loops', async () => {
    const { reloadTarget } = await import('../src/lib/updateCheck')
    const first = reloadTarget('/assets/main-abc12345.js', 'https://x.test/tutor?mode=socratic&page=p1')
    expect(first).toBe('https://x.test/tutor?mode=socratic&page=p1&build=abc12345')
    expect(reloadTarget('/assets/main-abc12345.js', first!)).toBeNull()
    expect(reloadTarget('/assets/main-def67890.js', first!)).toContain('build=def67890')
  })
})
