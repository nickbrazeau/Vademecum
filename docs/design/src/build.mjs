// Assembles the artboards (*.dc.html) from shared.css and the pieces below.
// Every artboard is static: no data-dc-script, no tweaks — copy is retyped in place.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { more } from './more.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const out = join(here, '..')
const css = readFileSync(join(here, 'shared.css'), 'utf8')

// ---------- shell ----------
function page({ body, width, extraCss = '', dark = false }) {
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <style>
${css}
${extraCss}
  </style>
</helmet>
${body}
</x-dc>
</body>
</html>
`
}

// ---------- icons (stroke, 20px grid) ----------
const icon = {
  check: (c) => `<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="${c}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="8"></circle><path d="M6.5 10.5l2.5 2.5 4.5-5"></path></svg>`,
  half: (c) => `<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="${c}" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><circle cx="10" cy="10" r="8"></circle><path d="M10 2a8 8 0 0 1 0 16z" fill="${c}" stroke="none"></path></svg>`,
  cross: (c) => `<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="${c}" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><circle cx="10" cy="10" r="8"></circle><path d="M7.5 7.5l5 5m0-5l-5 5"></path></svg>`,
  person: (c) => `<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="${c}" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><circle cx="10" cy="7" r="3.25"></circle><path d="M4 17a6 6 0 0 1 12 0"></path></svg>`,
  chevron: (c) => `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="${c}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6l4 4 4-4"></path></svg>`
}

// ---------- shared pieces ----------
const NAV = ['Today', 'Tutor', 'Sources', 'Improvement Map', 'Model']
function nav(current) {
  return `<nav class="nav" style="display: flex; gap: 4px; margin: 12px 0 0;">${NAV.map(
    (n) => `<a href="#" class="${n === current ? 'current' : ''}" style="min-height: 44px; display: inline-flex; align-items: center; padding: 0 12px;">${n}</a>`
  ).join('')}</nav>`
}

function flagButton() {
  return `<div class="button primary" style="display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 16px;">Flag a gap <kbd>⌘K</kbd></div>`
}

function headerPhone(current) {
  return `<header style="display: flex; flex-direction: column; gap: 0;">
  <div class="header-row" style="display: flex; align-items: center; justify-content: space-between; gap: 12px;">
    <h1>Vademecum</h1>
    <div class="button primary" style="display: inline-flex; align-items: center; min-height: 44px; padding: 0 16px;">Flag a gap</div>
  </div>
  ${nav(current)}
</header>`
}

function headerDesktop(current) {
  return `<header style="display: flex; align-items: center; justify-content: space-between; gap: 24px; min-height: 56px; border-bottom: 1px solid var(--line);">
  <h1>Vademecum</h1>
  <nav class="nav" style="display: flex; gap: 4px; margin: 0; border-bottom: none; align-self: stretch;">${NAV.map(
    (n) => `<a href="#" class="${n === current ? 'current' : ''}" style="min-height: 56px; display: inline-flex; align-items: center; padding: 0 14px;">${n}</a>`
  ).join('')}</nav>
  ${flagButton()}
</header>`
}

const FOOTER = `<footer class="footer" style="margin-top: 16px; padding-top: 16px;">
  <p class="muted small">A personal learning workspace, augmented by AI. Educational only — not a substitute for clinical judgment, current institutional guidance, or an appropriate specialist.</p>
</footer>`

const MACHINE_REVIEWED = `<p class="muted small">Model-generated learning material. Each item shows its own source or evidence support and any hold. Machine review is not clinical validation or human approval. Check current references before applying it.</p>`

// ---------- Today pieces (sample content, ID-flavoured) ----------
const LITERATURE_CARD = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>New in the literature</h2>
  <ul class="list" style="display: flex; flex-direction: column; gap: 12px;">
    <li style="display: flex; flex-direction: column; gap: 6px;">
      <p class="title"><a href="#">Single-dose baloxavir for post-exposure prophylaxis of influenza in households</a></p>
      <p class="muted small">New England Journal of Medicine · published 12 Sep 2026 · PMID [sample]</p>
      <div class="badges" style="display: flex; flex-wrap: wrap; gap: 6px;"><span class="badge">First seen here today</span></div>
      <p class="body">Household transmission is the setting your influenza pile keeps returning to; this trial reports secondary attack rates with and without prophylaxis.</p>
      <p class="muted small">Matched your topic <strong>influenza household transmission</strong> · checked 20 minutes ago</p>
      <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
        <div class="button">Acknowledge</div>
        <div class="button ghost">Dismiss</div>
      </div>
    </li>
    <li style="display: flex; flex-direction: column; gap: 6px;">
      <p class="title"><a href="#">Correction: Duration of viral shedding after oseltamivir in hospitalised adults</a></p>
      <p class="muted small">Clinical Infectious Diseases · published 3 Sep 2026 · PMID [sample]</p>
      <div class="badges" style="display: flex; flex-wrap: wrap; gap: 6px;">
        <span class="badge">First seen here 2 Sep 2026</span>
        <span class="badge badge-corrected">Correction or concern notice — not a retraction</span>
      </div>
      <p class="warn small" style="padding: 8px 12px;">A correction or concern notice is associated with this paper. Review the notice before relying on the paper. This flag does not itself mean the paper was retracted.</p>
      <p class="muted small">Matched your topic <strong>neuraminidase inhibitors</strong> · checked 20 minutes ago</p>
      <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
        <div class="button">Acknowledge</div>
        <div class="button ghost">Dismiss</div>
      </div>
    </li>
  </ul>
</section>`

function pointCard() {
  return `<li class="point" style="display: flex; flex-direction: column; gap: 6px;">
  <p class="title">Antiviral treatment within 48 hours of symptom onset shortens influenza illness, and later treatment still benefits hospitalised patients.</p>
  <p class="body">The 48-hour window comes from outpatient trials; observational data in admitted patients support starting antivirals regardless of symptom duration.</p>
  <div class="badges" style="display: flex; flex-wrap: wrap; gap: 6px;">
    <span class="badge support-evidence_supported">Evidence-supported, machine reviewed</span>
    <span class="badge">Abstract quote matched</span>
  </div>
  <p class="muted small">Published literature was retrieved and a quote from its abstract was matched to this claim. Not verified, not human-approved, not clinically validated.</p>
  <ul class="tags" style="display: flex; flex-wrap: wrap; gap: 6px;"><li class="tag">Influenza</li><li class="tag">Antivirals</li><li class="tag">Inpatient</li></ul>
  <div class="provenance" style="display: flex; flex-direction: column; gap: 6px;">
    <h4>From your sources</h4>
    <ul class="list small" style="display: flex; flex-direction: column; gap: 8px;">
      <li style="display: flex; flex-direction: column; gap: 4px;">
        <p><span class="title">IDSA influenza guideline (lecture deck).pptx</span> <span class="muted">· slide 14, notes · </span><span class="tier tier-high">High</span></p>
        <blockquote class="quote">Clinicians should start antiviral treatment as soon as possible for adults with documented or suspected influenza who are hospitalized, irrespective of illness duration.</blockquote>
      </li>
    </ul>
  </div>
  <div class="provenance" style="display: flex; flex-direction: column; gap: 6px;">
    <h4>Checked against</h4>
    <ul class="list small" style="display: flex; flex-direction: column; gap: 8px;">
      <li style="display: flex; flex-direction: column; gap: 4px;">
        <p class="title"><a href="#">Timing of neuraminidase inhibitor therapy and outcomes in hospitalised influenza</a></p>
        <p class="muted small">Lancet Respiratory Medicine · 2014 · PMID [sample] · supports</p>
        <blockquote class="quote">Earlier initiation of neuraminidase inhibitor treatment was associated with reduced mortality, with benefit extending beyond 48 h of symptom onset.</blockquote>
      </li>
    </ul>
  </div>
</li>`
}

function pointCardHeld() {
  return `<li class="point" style="display: flex; flex-direction: column; gap: 6px;">
  <p class="title">Pre-exposure prophylaxis with oseltamivir is recommended for immunocompromised close contacts.</p>
  <div class="badges" style="display: flex; flex-wrap: wrap; gap: 6px;">
    <span class="badge support-conflicting">Conflicting</span>
    <span class="badge badge-held">Held for review</span>
  </div>
  <p class="warn small" style="padding: 8px 12px;">Two retrieved abstracts disagree on the population; this point is shown but Tutor will not ask it.</p>
  <ul class="tags" style="display: flex; flex-wrap: wrap; gap: 6px;"><li class="tag">Influenza</li><li class="tag">Prophylaxis</li></ul>
  <div class="provenance" style="display: flex; flex-direction: column; gap: 6px;">
    <h4>From your sources</h4>
    <ul class="list small" style="display: flex; flex-direction: column; gap: 8px;">
      <li style="display: flex; flex-direction: column; gap: 4px;">
        <p><span class="title">Transplant ID handout 2025.pdf</span> <span class="muted">· page 6 · </span><span class="tier tier-mid">Medium</span></p>
      </li>
    </ul>
  </div>
</li>`
}

const WORTH_A_LOOK = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Worth a look</h2>
  ${MACHINE_REVIEWED}
  <ul class="list" style="display: flex; flex-direction: column; gap: 12px;">
    ${pointCard()}
    ${pointCardHeld()}
  </ul>
</section>`

const HELD = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Held for review</h2>
  <p class="body">3 points and 2 questions are held back and are not being shown or asked.</p>
  <ul class="list small" style="display: flex; flex-direction: column; gap: 6px;">
    <li class="muted" style="border-bottom: none; padding-bottom: 0;">Conflicting evidence between retrieved abstracts</li>
    <li class="muted" style="border-bottom: none; padding-bottom: 0;">A correction notice was found for a cited paper</li>
    <li class="muted" style="border-bottom: none; padding-bottom: 0;">Source excluded after the build</li>
  </ul>
  <p class="muted small"><a href="#">Open Sources</a> to see which pile they came from.</p>
  <p class="muted small">Tutor has 41 questions ready and 2 held.</p>
</section>`

const YOUR_SOURCES = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Your sources</h2>
  <p class="body">12 files · 11 readable · 1 needs attention</p>
  <div style="display: flex; flex-direction: column; gap: 6px;">
    <p class="body">184,200 of 312,900 extracted-text characters processed; 128,700 remain.</p>
    <div class="usage-bar"><div class="usage-fill" style="width: 59%;"></div></div>
    <p class="muted small">Coverage is of extracted text only, not diagrams, image-only pages or omitted text. Processing does not mean clinical validation.</p>
  </div>
  <p class="muted small"><strong>Source confidence</strong> — your judgment of the material’s accuracy and usefulness for learning. It is not mastery, priority, difficulty, or evidence that a claim has been verified.</p>
  <ul class="tiers" style="display: flex; flex-direction: column; gap: 8px;">
    <li style="display: flex; align-items: center; gap: 8px;"><span class="tier tier-high">High</span><span class="muted small">2 piles · 5 files</span></li>
    <li style="display: flex; align-items: center; gap: 8px;"><span class="tier tier-mid">Medium</span><span class="muted small">3 piles · 6 files</span></li>
    <li style="display: flex; align-items: center; gap: 8px;"><span class="tier tier-low">Low</span><span class="muted small">1 pile · 1 file</span></li>
  </ul>
</section>`

const RECENT_FLAGS = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Recently flagged</h2>
  <ul class="list" style="display: flex; flex-direction: column; gap: 10px;">
    <li style="display: flex; flex-direction: column; gap: 2px; border-bottom: 1px solid var(--line); padding-bottom: 10px;"><span class="title">Had to look up when to add MRSA coverage in severe CAP</span><span class="muted small">Pneumonia</span></li>
    <li style="display: flex; flex-direction: column; gap: 2px; border-bottom: 1px solid var(--line); padding-bottom: 10px;"><span class="title">Unsure how long to continue antivirals in a ventilated flu patient</span><span class="muted small">Influenza</span></li>
    <li style="display: flex; flex-direction: column; gap: 2px;"><span class="title">Interpretation of a positive Strongyloides serology before steroids</span><span class="muted small">Not filed yet</span></li>
  </ul>
</section>`

const LOCAL_DATA = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Your local data</h2>
  <p class="muted">An export is readable JSON you can open in any editor. A backup is a consistent copy of the database. Both are written into your data directory on this Mac.</p>
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
    <div class="button">Export as JSON</div>
    <div class="button">Back up the database</div>
  </div>
</section>`

const LIT_SETTINGS = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Literature settings</h2>
  <p class="body">Watched topics are short public phrases derived during Build. Edit them freely; only these words are sent to PubMed.</p>
  <ul class="tags" style="display: flex; flex-wrap: wrap; gap: 6px;"><li class="tag">influenza household transmission</li><li class="tag">neuraminidase inhibitors</li><li class="tag">influenza vaccine effectiveness</li><li class="tag">strongyloides screening</li></ul>
  <p class="muted small">Last checked 20 minutes ago · weekly checking is off</p>
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
    <div class="button">Check now</div>
    <div class="button ghost">Edit topics</div>
  </div>
</section>`

const PRIVACY = `<section class="card privacy" style="display: flex; flex-direction: column; gap: 4px;">
  <h2>Where this is stored</h2>
  <dl style="margin: 0;">
    <dt>On this Mac</dt>
    <dd>Your piles, uploaded sources, notes, generated points and questions, answers and flags are saved to a SQLite database and a data directory of your own.</dd>
    <dt>Sent to a model</dt>
    <dd><strong>Build and Grade require an explicit action.</strong> Each shows what it will send first. Codex holds your ChatGPT sign-in, so no API key is used.</dd>
    <dt>Sent to PubMed</dt>
    <dd>Short public topic phrases and record identifiers — never source excerpts, filenames or answers.</dd>
    <dt>Kept in this browser</dt>
    <dd>Only what you have typed and not yet saved.</dd>
  </dl>
</section>`

// ---------- Tutor pieces ----------
const QUESTION_TOP = `<h2>Question</h2>
  <p class="muted small">27 left in this pass · pass 3</p>
  <p class="prompt">In an adult admitted with severe community-acquired pneumonia, which findings should prompt empiric MRSA coverage, and what would let you stop it early?</p>
  <div class="badges" style="display: flex; flex-wrap: wrap; gap: 6px;">
    <span class="badge support-evidence_supported">Evidence-supported, machine reviewed</span>
    <span class="badge">Abstract quote matched</span>
  </div>
  <p class="muted small">Published literature was retrieved and a quote from its abstract was matched to the claim, and the question and its reference answer separately passed an assessment against that text. Not verified, not human-approved, not clinically validated.</p>
  <ul class="tags" style="display: flex; flex-wrap: wrap; gap: 6px;"><li class="tag">Pneumonia</li><li class="tag">MRSA</li><li class="tag">Antimicrobial stewardship</li></ul>`

const ANSWER_DRAFT = `Prior MRSA isolation or colonisation, recent hospitalisation with IV antibiotics, and severe disease (ICU, necrotising or cavitary pneumonia, post-influenza). Start vancomycin or linezolid, send a nasal MRSA PCR and cultures, and stop at 48 h if the PCR is negative and cultures show no MRSA.`

const PHI = `<p class="phi-warning"><strong>No patient identifiers or HIPAA material.</strong></p>`

const DISCLOSURE = `<section class="disclosure-panel" style="display: flex; flex-direction: column; gap: 6px;">
  <p class="disclosure-headline">Pressing Grade sends three things, and nothing else:</p>
  <ul class="disclosure-list" style="display: flex; flex-direction: column; gap: 2px;">
    <li>the question on screen</li>
    <li>its reference answer and rubric</li>
    <li>the answer you have typed</li>
  </ul>
  <p class="muted small"><strong>Where it goes:</strong> OpenAI, through Codex on this Mac. No API key is used.</p>
</section>`

function questionCardAnswering() {
  return `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  ${QUESTION_TOP}
  <label class="field" style="display: flex; flex-direction: column; gap: 5px;">
    <span>Your answer</span>
    <div class="textarea" style="min-height: 176px;">${ANSWER_DRAFT}</div>
  </label>
  ${PHI}
  ${DISCLOSURE}
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
    <div class="button primary">Grade with the model</div>
    <div class="button">Show reference answer</div>
    <div class="button ghost">Next question</div>
  </div>
</section>`
}

function questionCardGraded() {
  // Collapsed after a grade: prompt and support only, so the feedback sits higher on a phone.
  return `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Question</h2>
  <p class="prompt" style="font-size: 18px;">In an adult admitted with severe community-acquired pneumonia, which findings should prompt empiric MRSA coverage, and what would let you stop it early?</p>
  <div class="badges" style="display: flex; flex-wrap: wrap; gap: 6px;">
    <span class="badge support-evidence_supported">Evidence-supported, machine reviewed</span>
  </div>
  <div class="field" style="display: flex; flex-direction: column; gap: 5px;">
    <span>Your answer</span>
    <p class="small" style="padding-left: 12px; border-left: 2px solid var(--accent-line); color: var(--ink);">${ANSWER_DRAFT}</p>
  </div>
</section>`
}

function questionCardUnavailable() {
  return `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  ${QUESTION_TOP}
  <label class="field" style="display: flex; flex-direction: column; gap: 5px;">
    <span>Your answer</span>
    <div class="textarea" style="min-height: 176px;">${ANSWER_DRAFT}</div>
  </label>
  ${PHI}
  ${DISCLOSURE}
  <p class="failure">No model grade was recorded. Codex on this Mac could not be reached. Your answer is still here; you can compare it with the reference answer.</p>
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
    <div class="button primary">Grade with the model</div>
    <div class="button disabled">Show reference answer</div>
    <div class="button ghost">Next question</div>
  </div>
</section>`
}

const SELF_ASSESS = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h3>Judge it yourself instead</h3>
  <p class="body">The model did not grade this. If you want the attempt recorded, read the reference answer below and say how you did. It will be stored as <strong>self-assessed</strong>, not as a model grade.</p>
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
    <div class="button">I had it</div>
    <div class="button">Partly</div>
    <div class="button">I did not have it</div>
  </div>
</section>`

const ATTEMPT = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <div class="attempt-head" style="display: flex; align-items: center; gap: 8px;">${icon.half('#b07d2b')}<h3>Partially correct</h3></div>
  <p class="body">You named the main risk factors and a sensible de-escalation trigger. The answer leaves out the strongest single predictor in the cited evidence and overstates what a negative nasal PCR alone can settle.</p>
  <h4>What you did well</h4>
  <p class="body">Prior MRSA isolation, recent IV antibiotics and post-influenza necrotising pneumonia are the right prompts, and pairing empiric vancomycin or linezolid with a 48-hour reassessment is the stewardship pattern the reference describes.</p>
  <h4>What is missing or unsafe</h4>
  <p class="body">Gram-positive cocci in clusters on a good-quality sputum sample is a direct prompt you did not mention. A negative nasal PCR has a high negative predictive value, but the reference stops empiric coverage on the PCR <em>and</em> negative cultures at 48 hours, not on the PCR alone.</p>
  <h4>A better answer</h4>
  <p class="body">Cover MRSA empirically when there is prior MRSA isolation or colonisation, recent hospitalisation with IV antibiotics, post-influenza or necrotising pneumonia, or gram-positive cocci in clusters on sputum. Send a nasal MRSA PCR and respiratory cultures at the start; stop coverage at 48 hours if the PCR is negative and cultures show no MRSA.</p>
  <p class="warn small" style="padding: 8px 12px;"><strong>Uncertain:</strong> the reference draws on one guideline abstract; thresholds for “recent” hospitalisation vary between sources.</p>
</section>`

const REFERENCE = `<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h3>Reference answer</h3>
  <p class="body">Empiric MRSA coverage in severe CAP is prompted by prior MRSA isolation or colonisation, recent hospitalisation with parenteral antibiotics, post-influenza or necrotising/cavitary pneumonia, or gram-positive cocci in clusters on sputum. Send a nasal MRSA PCR and cultures before starting; a negative PCR with negative cultures at 48 hours supports stopping.</p>
  <h4>Rubric</h4>
  <p class="muted small">Names at least three prompting findings (2 pts); names a de-escalation trigger that includes culture results (1 pt); does not recommend routine MRSA coverage for all CAP (1 pt).</p>
  <div class="provenance" style="display: flex; flex-direction: column; gap: 6px;">
    <h4>Where this came from</h4>
    <ul class="list small" style="display: flex; flex-direction: column; gap: 8px;">
      <li style="display: flex; flex-direction: column; gap: 4px;">
        <p><span class="title">ATS-IDSA CAP guideline summary.pdf</span> <span class="muted">· page 9 · </span><span class="tier tier-high">High</span></p>
        <blockquote class="quote">We suggest not routinely adding MRSA coverage in adults with CAP unless locally validated risk factors are present; obtain cultures and nasal PCR to allow de-escalation.</blockquote>
      </li>
    </ul>
  </div>
</section>`

// ---------- artboards ----------
const PHONE_W = 390
const phoneWrap = (inner, dark = false) =>
  `<div class="${dark ? 'dark' : ''}" style="width: ${PHONE_W}px; min-height: 100%; background: var(--paper); color: var(--ink); padding: 12px 16px 16px; display: flex; flex-direction: column; gap: 16px;">${inner}</div>`

const desktopWrap = (inner) =>
  `<div style="width: 1440px; min-height: 100%; background: var(--paper); color: var(--ink); padding: 0 48px 32px; display: flex; flex-direction: column; gap: 24px;">${inner}</div>`
const column = (inner) => `<div class="stack" style="display: flex; flex-direction: column; gap: 16px; max-width: 736px; width: 100%; margin: 0 auto;">${inner}</div>`

const artboards = {
  'Main.dc.html': page({
    body: phoneWrap(`${headerPhone('Today')}
<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">
  ${LITERATURE_CARD}
  ${WORTH_A_LOOK}
  ${HELD}
  ${YOUR_SOURCES}
  ${RECENT_FLAGS}
  ${LOCAL_DATA}
  ${LIT_SETTINGS}
  ${PRIVACY}
</main>
${FOOTER}`)
  }),

  'TodayDesktop.dc.html': page({
    body: desktopWrap(`${headerDesktop('Today')}
${column(`<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">
  ${LITERATURE_CARD}
  ${WORTH_A_LOOK}
  ${HELD}
  ${YOUR_SOURCES}
  ${RECENT_FLAGS}
  ${LOCAL_DATA}
  ${LIT_SETTINGS}
  ${PRIVACY}
</main>
${FOOTER}`)}`)
  }),

  'TutorAnswering.dc.html': page({
    body: phoneWrap(`${headerPhone('Tutor')}
<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">
  ${questionCardAnswering()}
</main>
${FOOTER}`)
  }),

  'TutorGraded.dc.html': page({
    body: phoneWrap(`${headerPhone('Tutor')}
<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">
  ${questionCardGraded()}
  ${ATTEMPT}
  ${REFERENCE}
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;"><div class="button primary">Next question</div></div>
</main>
${FOOTER}`)
  }),

  'TutorUnavailable.dc.html': page({
    body: phoneWrap(`${headerPhone('Tutor')}
<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">
  ${questionCardUnavailable()}
  ${SELF_ASSESS}
  ${REFERENCE}
</main>
${FOOTER}`)
  }),

  'TutorDesktop.dc.html': page({
    body: desktopWrap(`${headerDesktop('Tutor')}
${column(`<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">
  ${questionCardGraded()}
  ${ATTEMPT}
  ${REFERENCE}
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;"><div class="button primary">Next question</div></div>
</main>
${FOOTER}`)}`)
  }),

  'TutorDark.dc.html': page({
    body: phoneWrap(`${headerPhone('Tutor')}
<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">
  ${questionCardAnswering()}
</main>
${FOOTER}`, true)
  }),

  'FlagDialog.dc.html': page({
    body: `<div style="position: relative; width: 390px; height: 844px; overflow: hidden; background: var(--paper);">
  <div style="position: absolute; inset: 0; filter: blur(0px);">${phoneWrap(`${headerPhone('Today')}<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">${LITERATURE_CARD}</main>`)}</div>
  <div style="position: absolute; inset: 0; background: rgba(0, 0, 0, 0.45); display: flex; align-items: flex-start; justify-content: center; padding: 72px 16px 16px;">
    <div class="card" style="width: 358px; display: flex; flex-direction: column; gap: 12px; padding: 16px;">
      <h3>Flag a knowledge gap</h3>
      <p class="muted">Whatever you were unsure about, in your own words. Nothing else is required.</p>
      ${PHI}
      <label class="field" style="display: flex; flex-direction: column; gap: 5px;">
        <span>What were you unsure about?</span>
        <div class="textarea" style="min-height: 120px;"><span class="placeholder">Had to look up…</span></div>
      </label>
      <label class="field" style="display: flex; flex-direction: column; gap: 5px;">
        <span>Topic <span class="muted">(optional)</span></span>
        <div class="input"></div>
      </label>
      <p class="muted small">Saved on this Mac. Not sent anywhere.</p>
      <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end;">
        <div class="button ghost">Close</div>
        <div class="button primary disabled">Save flag</div>
      </div>
    </div>
  </div>
</div>`
  }),

  'Tokens.dc.html': page({
    body: `<div style="width: 960px; min-height: 100%; background: var(--paper); color: var(--ink); padding: 32px; display: flex; flex-direction: column; gap: 28px;">
  <div style="display: flex; flex-direction: column; gap: 4px;">
    <h1>Vademecum — design tokens</h1>
    <p class="muted">Values are the ones in <code>apps/web/src/styles.css</code>; the five tints (accent tint/line, fail bg/line, tag bg) are additions this pass proposes.</p>
  </div>

  <section style="display: flex; flex-direction: column; gap: 10px;">
    <h2>Color — light</h2>
    <div style="display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px;">
      ${swatch('paper', '#f7f5f0')}${swatch('card', '#fffefb')}${swatch('line', '#dcd7cc')}${swatch('ink', '#1c1e22')}${swatch('muted', '#5c6470')}${swatch('accent', '#2f5d50')}
      ${swatch('accent tint', '#e6efec')}${swatch('warn', '#7a4a12')}${swatch('warn bg', '#fdf3e3')}${swatch('fail', '#8a2020')}${swatch('fail bg', '#f6e3e3')}${swatch('tag bg', '#edeae2')}
    </div>
  </section>

  <section class="dark" style="display: flex; flex-direction: column; gap: 10px; background: var(--paper); color: var(--ink); padding: 16px; border-radius: 12px;">
    <h2>Color — dark</h2>
    <div style="display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px;">
      ${swatch('paper', '#14161a')}${swatch('card', '#1b1e23')}${swatch('line', '#2b2f36')}${swatch('ink', '#e8e6e1')}${swatch('muted', '#9aa2ad')}${swatch('accent', '#6fae9b')}
      ${swatch('accent tint', '#1d2c28')}${swatch('warn', '#e0b877')}${swatch('warn bg', '#2a2318')}${swatch('fail', '#e08b8b')}${swatch('fail bg', '#2c1b1b')}${swatch('tag bg', '#23272e')}
    </div>
  </section>

  <section style="display: flex; flex-direction: column; gap: 10px;">
    <h2>Type</h2>
    <div style="display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 8px 24px; align-items: baseline;">
      <p class="muted small">Wordmark · sans 20/600</p><h1>Vademecum</h1>
      <p class="muted small">Section label · sans 12/600 caps</p><h2>Worth a look</h2>
      <p class="muted small">Card heading · sans 17/600</p><h3>Reference answer</h3>
      <p class="muted small">Sub-label · sans 12/600 caps</p><h4>What you did well</h4>
      <p class="muted small">Question · serif 20/1.4</p><p class="prompt">Which findings should prompt empiric MRSA coverage?</p>
      <p class="muted small">Body · serif 16/1.55</p><p>Body text is 16px or larger so iOS Safari does not zoom on focus.</p>
      <p class="muted small">Small · serif 14/1.45</p><p class="small muted">Matched your topic · checked 20 minutes ago</p>
      <p class="muted small">Quote · serif italic 15</p><blockquote class="quote">Clinicians should start antiviral treatment as soon as possible.</blockquote>
      <p class="muted small">Code · mono 20, 0.12em</p><span class="device-code" style="justify-self: start;">ABCD-EFGH</span>
    </div>
  </section>

  <section style="display: flex; flex-direction: column; gap: 10px;">
    <h2>Badges — support, holds, notices</h2>
    <div class="badges" style="display: flex; flex-wrap: wrap; gap: 8px;">
      <span class="badge support-evidence_supported">Evidence-supported, machine reviewed</span>
      <span class="badge support-source_supported">Draft (source-supported)</span>
      <span class="badge support-uncertain">Uncertain</span>
      <span class="badge support-conflicting">Conflicting</span>
      <span class="badge badge-held">Held for review</span>
      <span class="badge badge-corrected">Correction or concern notice</span>
      <span class="badge badge-retracted">Retracted</span>
      <span class="badge">First seen here today</span>
    </div>
    <p class="muted small">One pill, four tints. Green is reserved for the single strongest claim the product makes; amber for anything held; red only for a retraction.</p>
  </section>

  <section style="display: flex; flex-direction: column; gap: 10px;">
    <h2>Source confidence and topics</h2>
    <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
      <span class="tier tier-high">High</span><span class="tier tier-mid">Medium</span><span class="tier tier-low">Low</span>
      <span class="muted small" style="margin-left: 8px;">Filled pills = the owner’s trust in the material, never mastery or evidence.</span>
    </div>
    <ul class="tags" style="display: flex; flex-wrap: wrap; gap: 6px;"><li class="tag">Influenza</li><li class="tag">Antivirals</li><li class="tag">Inpatient</li></ul>
  </section>

  <section style="display: flex; flex-direction: column; gap: 10px;">
    <h2>Controls · 44px targets, 999px radius</h2>
    <div style="display: flex; flex-wrap: wrap; gap: 8px;">
      <div class="button primary">Grade with the model</div>
      <div class="button">Show reference answer</div>
      <div class="button ghost">Next question</div>
      <div class="button primary disabled">Save flag</div>
      ${flagButton()}
    </div>
  </section>

  <section style="display: flex; flex-direction: column; gap: 10px;">
    <h2>Outcome marks · 20px stroke icons</h2>
    <div style="display: flex; flex-wrap: wrap; gap: 24px;">
      <div class="attempt-head" style="display: flex; align-items: center; gap: 8px;">${icon.check('#2f5d50')}<h3>Correct</h3></div>
      <div class="attempt-head" style="display: flex; align-items: center; gap: 8px;">${icon.half('#b07d2b')}<h3>Partially correct</h3></div>
      <div class="attempt-head" style="display: flex; align-items: center; gap: 8px;">${icon.cross('#8a2020')}<h3>Incorrect</h3></div>
      <div class="attempt-head" style="display: flex; align-items: center; gap: 8px;">${icon.person('#5c6470')}<h3>Self-assessed</h3></div>
    </div>
  </section>

  <section style="display: flex; flex-direction: column; gap: 10px;">
    <h2>Surfaces</h2>
    <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px;">
      <div class="card" style="display: flex; flex-direction: column; gap: 6px;"><h2>Card</h2><p class="body">card / line / 12px radius / 16px padding</p></div>
      ${DISCLOSURE}
      <div style="display: flex; flex-direction: column; gap: 8px;">${PHI}<p class="warn small" style="padding: 8px 12px;">Held: warn on warn-bg with a 1px warn border.</p></div>
    </div>
  </section>
</div>`
  })
}

function swatch(name, hex) {
  return `<div style="display: flex; flex-direction: column; gap: 4px;">
    <div style="height: 48px; border-radius: 8px; background: ${hex}; border: 1px solid rgba(0,0,0,0.08);"></div>
    <p class="small" style="font-family: var(--sans); font-size: 12px;">${name}</p>
    <p class="muted small" style="font-family: var(--mono); font-size: 11px;">${hex}</p>
  </div>`
}

Object.assign(artboards, more({ page, phoneWrap, desktopWrap, column, headerPhone, headerDesktop, FOOTER, PHI, icon }))

mkdirSync(out, { recursive: true })
for (const [name, html] of Object.entries(artboards)) {
  writeFileSync(join(out, name), html)
  console.log('wrote', name, html.length)
}
