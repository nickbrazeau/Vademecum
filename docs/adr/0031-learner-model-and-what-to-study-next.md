# ADR 0031 — A learner model behind the Improvement Map, and what to study next

- Status: accepted
- Date: 2026-10-08
- Phase two (pedagogy) of the owner's plan. Builds on ADR 0020 (exam reports), 0023 (board
  bank), 0024 and 0028 (flashcards and spacing), 0025 (Socratic tutor) and 0026 (strong and
  weak).

## Context

By the end of phase one, Vademecum recorded plenty about the learner: board answers,
flashcard answers, Socratic sessions and the gaps they named, pages read, open flags, and
exam-report areas. Each feature used its own slice of that. The flashcard draw weighted
flagged topics. "Strong and weak" added up signals. The Tutor shuffled every question
equally. Nothing combined the record into a picture of what the learner knows and what is
fading, and nothing used that picture to decide what comes next.

The project exists for the few spare minutes of a clinical day. Those minutes are best spent
where they help most. The learning science on that is consistent:

- **Retrieval beats rereading** (the testing effect). Producing an answer strengthens memory
  more than seeing it again. This holds in medical education too.
- **Spacing beats massing.** The same practice spread over time lasts longer, and the best
  moment to retrieve is as the memory starts to fade.
- **Interleaving beats blocking.** Mixing topics improves discrimination and transfer, even
  though it feels harder.
- **Explanation builds understanding** (self-explanation, elaborative interrogation). The
  Socratic tutor does this.
- **Spare effort for what is known.** Practice should go to the edge of competence, where it
  is deliberate, not to what is already secure.

## Decision

### One model, recomputed, never stored

`storage/learner.py` treats every current encyclopedia page as a unit of knowledge. So is
any flagged topic or exam area that no page covers yet; its only step is to add a source. A
flag or an exam area finds its page the way the map does (`pages_matching`). Board answers,
flashcards and Socratic sessions carry their page directly.

The model is recomputed from records that already sync. There is no new table, no migration
and no new sync rule, and the Mac and the phone always agree. It runs in tens of milliseconds
on the owner's 680 pages.

### Two estimates per unit

1. **How well it is understood.** A Beta(1, 1) prior updated by every outcome:
   - board answers (weight 1.0);
   - flashcards (0.5, a narrower cue);
   - Socratic sessions (1.5, because they ask for explanation; each named gap takes a fifth
     off the outcome);
   - open flags (0.75 each, at most three, as an outcome of 0);
   - exam-report areas (2.0, with below, at and above taken as 0.2, 0.6 and 0.9).

   Each item's weight halves every 60 days of age, so what you did recently counts more than
   what you did last spring. Confidence is the effective amount of evidence *n*, as
   *n / (n + 3)*. This is a simplified knowledge-tracing estimate; with so little data a fuller
   model would only be guessing its own parameters.
2. **How well it is holding now.** A forgetting curve, *recall = 2^(−days / h)*, in the spirit
   of half-life regression. *h* is 2.5 days after a first retrieval, so one right answer reads
   as holding for about a day, like a new flashcard's first gap. It is then replayed from the
   retrievals in order:
   - a success multiplies *h* by *1 + 1.5 × min(1, gap / h)*, at least 1.1, so retrieval spaced
     out to about the half-life gains the most and massed repetition gains little;
   - a failure (an outcome below 0.4) halves *h*, down to a floor of half a day;
   - an outcome in between, such as a Socratic session naming three gaps, leaves *h* where it was.

   Reading a page is study, not retrieval. It is recorded, and it decides whether "read it
   first" is still the right step, but it does not move the curve.

### A state, a need and a step

Each unit gets a state:

| State | Meaning |
|---|---|
| Not yet tried | Never retrieved |
| Still forming | Understood below 0.6 |
| Fading | Understood, but recall now below 0.75 |
| Holding | Understood and recall now at or above 0.75 |

**Need** is how much the next ten minutes would help. It is computed as
*(1 − understood × recall) × importance*, plus a little for exploring what is barely known:

- **Importance** is 1, plus 0.5 per open flag (at most three), plus 1 if an exam area is below
  the mark, or 0.3 if it is at the mark.
- Units nothing points to (no flags, no exam area, no attempts) are weighted down to a fifth,
  so seven hundred untried pages do not drown the twenty that matter. They still surface
  when nothing else does, with a small floor for exploring.
- A gap with no page is discounted by a fifth, so a page you can open now comes first.
- A flag or exam area that matches two pages counts on both. The map draws it that way too,
  and a flag about two pages usually concerns both.

The **step** is chosen for the state, and each comes with its reason in a sentence:

| Situation | Step |
|---|---|
| Untried and unread | Read the page, then test yourself (encoding before retrieval) |
| Untried and read | Board questions or flashcards (the testing effect) |
| Forming after misses | Talk it through with the Socratic tutor (self-explanation) |
| Fading | Flashcards, else a board question (spacing: retrieve just as it fades) |
| Holding | An occasional board question mixed in with other topics |
| No page | Add a source |

The **plan** takes the five units with the most need, at most one of them a gap with no page.
It orders them so that no two steps in a row share a specialty where another is available
(interleaving).

### Where it is used

- **Improvement Map:**
  - "Where to go next" lists the plan, each step with its state, reason, evidence and one
    button.
  - The graph can be coloured by what you know instead of by specialty.
  - The selected topic shows its estimate as words and meters, never as a score.
- **Strong and weak:** each topic carries its state badge.
- **Flashcards:** the draw multiplies a card's weight by its page's state (fading 1.8,
  forming 1.4, holding 0.7) and says so ("Fading: recalling it now helps it last"). The factor
  scales the draw's own boosts for flags and exam areas, on purpose.
- **Tutor:** board questions can be taken "Where you need it most". Each question comes from
  the page that needs it most. A page practised in the last 30 minutes waits while another has
  questions (interleaving). The pass counter is hidden in this mode, and in single-page mode,
  because neither is a pass. "Shuffled through everything" remains the default, and its pass is untouched
  (ADR 0023).
- **Assistants:** the MCP tool `study_next` gives ChatGPT or Claude the plan, for "what should I
  study on the drive home?". `board_next_question` and `board_advance` take
  `where: "need"`.

### What it does not do

Product rules, AGENTS.md:

- No quota, no streak, no due count, nothing owed. The plan is a suggestion that can be ignored.
- No percentage or score is shown. A meter is described in words (low, middling, high).
- No model call: the learner model is arithmetic on the learner's own records, on the device.
- No stored mastery: delete an answer or a flag and the estimate follows.
- A page the owner deleted is never suggested back, even as "add a source".

## Consequences

- The constants are a reasoned starting point, not fitted to this learner. Fitting them (the
  half-life gains, the weights) to the owner's own answers is possible once there are a few
  hundred. That is future work, and it would change numbers, not the shape.
- A flag linked to the wrong page through name matching misdirects a step. The matcher is the
  map's own, so a fix there fixes both.
- On the phone, the plan reflects the Mac's last sync for answers given on the Mac, and its own
  answers at once.

## References

- Ebbinghaus H. *Über das Gedächtnis* (Memory: A Contribution to Experimental Psychology). 1885.
- Bjork RA. Memory and metamemory considerations in the training of human beings. In Metcalfe J,
  Shimamura A, eds. *Metacognition: Knowing about Knowing*. MIT Press; 1994:185–205.
- Corbett AT, Anderson JR. Knowledge tracing: modeling the acquisition of procedural knowledge.
  *User Model User-Adapt Interact.* 1994;4:253–278.
- Chi MTH, de Leeuw N, Chiu MH, LaVancher C. Eliciting self-explanations improves understanding.
  *Cogn Sci.* 1994;18:439–477.
- Ericsson KA. Deliberate practice and the acquisition and maintenance of expert performance in
  medicine and related domains. *Acad Med.* 2004;79(10 Suppl):S70–S81.
- Roediger HL, Karpicke JD. Test-enhanced learning: taking memory tests improves long-term
  retention. *Psychol Sci.* 2006;17(3):249–255.
- Cepeda NJ, Pashler H, Vul E, Wixted JT, Rohrer D. Distributed practice in verbal recall tasks:
  a review and quantitative synthesis. *Psychol Bull.* 2006;132(3):354–380.
- Kerfoot BP, DeWolf WC, Masser BA, Church PA, Federman DD. Spaced education improves the
  retention of clinical knowledge by medical students: a randomised controlled trial.
  *Med Educ.* 2007;41(1):23–31.
- Rohrer D, Taylor K. The shuffling of mathematics problems improves learning. *Instr Sci.*
  2007;35:481–498.
- Larsen DP, Butler AC, Roediger HL. Test-enhanced learning in medical education. *Med Educ.*
  2008;42(10):959–966.
- Kornell N, Bjork RA. Learning concepts and categories: is spacing the "enemy of induction"?
  *Psychol Sci.* 2008;19(6):585–592.
- Dunlosky J, Rawson KA, Marsh EJ, Nathan MJ, Willingham DT. Improving students' learning with
  effective learning techniques. *Psychol Sci Public Interest.* 2013;14(1):4–58.
- Settles B, Meeder B. A trainable spaced repetition model for language learning. *Proceedings of
  ACL.* 2016:1848–1858.
