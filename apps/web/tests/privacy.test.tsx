/**
 * The privacy surfaces, read as the owner would read them.
 *
 * Two distinct claims have to stay distinguishable: what is stored on this Mac,
 * and what would be sent to a model. Collapsing them is the failure mode this
 * copy exists to prevent (ADR 0002).
 */

import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PhiWarning } from '../src/components/PhiWarning'
import { PrivacyNote } from '../src/components/PrivacyNote'

describe('the privacy note', () => {
  it('distinguishes local storage from model transmission', () => {
    render(<PrivacyNote />)
    const section = within(screen.getByRole('region', { name: /where this is stored/i }))
    for (const heading of ['On this Mac', 'Sent to a model', 'Kept in this browser']) {
      expect(section.getByText(heading).tagName).toBe('DT')
    }
  })

  it('discloses explicit Build and Grade transmission including follow-up evidence checks', () => {
    render(<PrivacyNote />)
    const section = screen.getByRole('region', { name: /where this is stored/i })
    // A connection to Codex now exists and is used. What has not changed is
    // that no learning content goes through it, and the copy has to keep the
    // two apart rather than rounding both down to "nothing".
    expect(section).toHaveTextContent(
      /Build and Grade require an explicit action/i
    )
    expect(section).toHaveTextContent(/filenames, confidence labels and locations/i)
    expect(section).toHaveTextContent(/generated claims and context with retrieved abstracts/i)
    expect(section).toHaveTextContent(/reference answer and rubric with selected passages and abstracts/i)
    expect(section).toHaveTextContent(/no API key is used/i)
  })

  it('does not claim there is no network traffic, because the account check makes some', () => {
    render(<PrivacyNote />)
    const section = screen.getByRole('region', { name: /where this is stored/i })
    expect(section).toHaveTextContent(/goes through Codex, which contacts OpenAI to answer it/i)
    // "Nothing is sent" reads as "no network traffic", which is false the
    // moment this page checks whether Codex is signed in.
    expect(section.textContent ?? '').not.toMatch(/nothing is sent/i)
  })

  it('does not claim there is no model connection now that there is one', () => {
    render(<PrivacyNote />)
    const section = screen.getByRole('region', { name: /where this is stored/i })
    expect(section).not.toHaveTextContent(/no model connection at all/i)
    expect(section).toHaveTextContent(/to OpenAI through Codex/i)
  })

  it('requires disclosure before explicit model actions and separately discloses weekly opt-in', () => {
    render(<PrivacyNote />)
    expect(screen.getByRole('region', { name: /where this is stored/i })).toHaveTextContent(
      /Each shows what it will send first/i
    )
    expect(screen.getByRole('region', { name: /where this is stored/i })).toHaveTextContent(/Weekly checks are off until you opt in/i)
  })

  it('says browser storage holds only unsaved drafts', () => {
    render(<PrivacyNote />)
    expect(screen.getByRole('region', { name: /where this is stored/i })).toHaveTextContent(
      /only what you have typed and not yet saved/i
    )
  })
})

describe('the no-PHI warning', () => {
  it('is one short rule, not a policy document', () => {
    render(<PhiWarning />)
    const note = screen.getByRole('note')
    expect(note).toHaveTextContent(/no patient identifiers/i)
    expect(note.textContent?.length ?? 0).toBeLessThan(160)
  })

  it('names HIPAA material as well as identifiers', () => {
    // Copy decided 2026-09-17: one rule, two nouns, no list of examples.
    render(<PhiWarning />)
    expect(screen.getByRole('note')).toHaveTextContent(/hipaa material/i)
  })
})
