# ADR 0028 — The owner's feedback of 5 October

- Status: accepted; amended by ADR 0029 (ChatGPT voice now calls the Socratic tools)
- Date: 2026-10-06
- Extends: [0024](0024-flashcards-and-chosen-tabs.md), [0026](0026-feedback-october.md), [0027](0027-podcast-audio-in-the-cloud-copy.md)
- Amends: [0024](0024-flashcards-and-chosen-tabs.md) on scheduling: the owner asked for spaced repetition, so "nothing is due" no longer holds for flashcards.

## Decisions

### Today
- Every section that opens and shuts starts closed.
- "I reviewed this page" counts it and moves straight to another page.
- The flag dialog saves on ⌘S (Ctrl-S elsewhere) from any field.

### Tutor
- A board question names its page only after it is answered; so does a flashcard. The title
  could give the answer away.
- Questions written during a pass are shuffled into its unasked part. A pass drawn when forty
  questions existed had held back the next two thousand until it ended. The question on screen
  keeps its place.
- The Socratic tutor launches from Vademecum: on the Mac, "Start with voice" runs its own voice
  mode; anywhere, "Open in ChatGPT" and "Open in Claude" open a text chat with a prompt that
  names the tools and asks the assistant to say so if it cannot reach them. These two links are
  the only external destinations besides PubMed in the web app, and carry a fixed prompt only.
  ChatGPT's voice mode calls no tools, so a voice conversation still reaches Vademecum only by
  `socratic_save` afterwards or by pasting its transcript.

### Flashcards: spaced repetition
- A card's schedule is replayed from its own reviews (SM-2 with two answers), so it needs no
  table and follows the reviews across devices. Got it: one day, then three, then the last gap
  times the card's ease. Again: ten minutes, and a little harder.
- Cards ready again come first, most overdue as a share of their gap, weighted by the
  Improvement Map; then twenty new cards a day, the map's gaps first; then a rest that says
  when the next card is ready, with Keep practising for more. Each answer shows its gap.
- "What this rests on" and "The points this page rests on" said nothing visible: the flex
  layout had removed the toggle marker from every collapsible section. Each now has a marker
  and says what it holds; a flashcard links to its encyclopedia page.

### Encyclopedia
- Edit is at the top of the page, with the page's Markdown file named and Show in Finder or
  Open beside it (on the Mac, inside the source folder only).
- Pictures were capped at 200 per document, so a long book kept figures from its first
  chapters only. The cap is 3000; sources that reached the old one are read again for pictures
  only, once, in a process of their own. Their text and learning points are untouched.

### Improvement Map
- Open flags are the landing view; specialty groups start closed; the strong-and-weak lines
  are spaced. A clicked node lists its encyclopedia pages and its connected topics, each a link.

### Construction
- A new pile by dropping files: the Mac writes them to `piles/<confidence>/<pile>/` in the
  source folder (one safe folder name, no full path in the reply) and reads them in.

### Settings
- On the phone's copy, the Mac's model connection shows as last seen. The Mac keeps a summary
  (which connection, signed in, plan, allowance used and when it resets; no account name or
  identifier) in `app_state`, which syncs, at each check and before each sync round.
- Loose checkboxes are on/off switches with their words beside them.
- Subspecialties, adding a topic, suggested topics and watched topics each open and shut,
  closed to start.
