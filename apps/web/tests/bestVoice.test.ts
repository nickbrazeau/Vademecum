/**
 * Feedback of 7 October: the browser's default voice is often its flattest. The tutor
 * and the podcast read-aloud pick the most natural English voice the device has.
 */

import { describe, expect, it } from 'vitest'
import { bestVoice } from '../src/lib/speech'

function voice(name: string, lang = 'en-US', localService = true): SpeechSynthesisVoice {
  return { name, lang, localService, default: false, voiceURI: name } as SpeechSynthesisVoice
}

describe('the voice the tutor speaks in', () => {
  it("prefers Apple's Premium and Enhanced voices, then Siri, over the default and the novelty voices", () => {
    const voices = [voice('Samantha'), voice('Bubbles'), voice('Ava (Enhanced)'), voice('Zoe (Premium)'), voice('Siri Voice 2'), voice('Thomas', 'fr-FR')]
    expect(bestVoice(voices)?.name).toBe('Zoe (Premium)')
    expect(bestVoice(voices.filter((v) => !v.name.includes('Premium')))?.name).toBe('Ava (Enhanced)')
    expect(bestVoice([voice('Samantha'), voice('Google US English', 'en-US', false)])?.name).toBe('Google US English')
  })

  it('never picks a novelty voice when anything else is there, and none at all without English', () => {
    expect(bestVoice([voice('Bad News'), voice('Daniel', 'en-GB')])?.name).toBe('Daniel')
    expect(bestVoice([voice('Thomas', 'fr-FR')])).toBeNull()
  })
})
