/**
 * The browser's own voice (ADR 0025): dictation in, speech out, where the
 * browser offers them. Nothing here reaches a service: Web Speech is the
 * browser's, and on iOS and macOS Safari the recognition is the system's.
 * Where either is missing the page simply offers typing and reading.
 */

interface RecognitionResultLike {
  isFinal: boolean
  0: { transcript: string }
}

interface RecognitionLike {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((event: { resultIndex: number; results: ArrayLike<RecognitionResultLike> }) => void) | null
  onend: (() => void) | null
  onerror: (() => void) | null
  start: () => void
  stop: () => void
}

type RecognitionCtor = new () => RecognitionLike

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export function canDictate(): boolean {
  return typeof window !== 'undefined' && recognitionCtor() !== null
}

export function canSpeak(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function'
}

/** Listen once; the words arrive as they are recognised. Returns a stop function. */
export function dictate(onText: (text: string, final: boolean) => void, onDone: () => void): () => void {
  const Ctor = recognitionCtor()
  if (Ctor === null) {
    onDone()
    return () => undefined
  }
  const recognition = new Ctor()
  recognition.lang = navigator.language || 'en-US'
  recognition.interimResults = true
  recognition.continuous = true
  let spoken = ''
  recognition.onresult = (event) => {
    let interim = ''
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i]
      if (!result) continue
      if (result.isFinal) spoken += `${result[0].transcript} `
      else interim += result[0].transcript
    }
    onText(`${spoken}${interim}`.trim(), interim === '')
  }
  recognition.onend = onDone
  recognition.onerror = onDone
  try {
    recognition.start()
  } catch {
    onDone()
  }
  return () => {
    try {
      recognition.stop()
    } catch {
      /* already stopped */
    }
  }
}

/**
 * The most natural English voice this device offers (feedback of 7 October): the
 * browser's default is often its flattest. Apple's Premium and Enhanced voices, then
 * Siri's, then any marked natural or neural, then Google's, then whatever is local.
 * Nothing is downloaded or sent; these are the device's own voices.
 */
export function bestVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const english = voices.filter((voice) => voice.lang.toLowerCase().startsWith('en'))
  if (english.length === 0) return null
  const score = (voice: SpeechSynthesisVoice): number => {
    const name = voice.name.toLowerCase()
    let points = 0
    if (name.includes('premium')) points += 60
    if (name.includes('enhanced')) points += 50
    if (name.includes('siri')) points += 40
    if (name.includes('natural') || name.includes('neural')) points += 35
    if (name.includes('google')) points += 20
    if (voice.localService) points += 5
    if (voice.lang.toLowerCase() === 'en-us' || voice.lang.toLowerCase() === 'en_us') points += 3
    if (/(compact|novelty|whisper|zarvox|bells|bubbles|cellos|trinoids|bad news|good news|jester|organ|superstar|wobble|boing|bahh)/.test(name)) points -= 100
    return points
  }
  return [...english].sort((a, b) => score(b) - score(a))[0] ?? null
}

/** Say it, in the best voice the device offers. Returns a stop function. */
export function speak(text: string, options: { voice?: string; rate?: number; onEnd?: () => void } = {}): () => void {
  if (!canSpeak() || !text.trim()) {
    options.onEnd?.()
    return () => undefined
  }
  const synth = window.speechSynthesis
  synth.cancel()
  const utterance = new window.SpeechSynthesisUtterance(text)
  utterance.rate = options.rate ?? 1
  const voices = synth.getVoices()
  const chosen = (options.voice ? voices.find((voice) => voice.name === options.voice) : undefined) ?? bestVoice(voices)
  if (chosen) {
    utterance.voice = chosen
    utterance.lang = chosen.lang
  }
  if (options.onEnd) utterance.onend = options.onEnd
  synth.speak(utterance)
  return () => synth.cancel()
}

export function speechVoices(): string[] {
  if (!canSpeak()) return []
  return window.speechSynthesis
    .getVoices()
    .filter((voice) => voice.lang.toLowerCase().startsWith('en'))
    .map((voice) => voice.name)
}

// Some browsers fill the voice list only after a first request: warm the voice list early.
if (canSpeak()) {
  try {
    window.speechSynthesis.getVoices()
  } catch {
    /* a browser without a voice list */
  }
}
