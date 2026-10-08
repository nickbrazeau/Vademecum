# ADR 0030 — The owner's feedback of 6 October

- Status: accepted
- Date: 2026-10-08
- Extends: [0027](0027-podcast-audio-in-the-cloud-copy.md), [0028](0028-feedback-of-5-october.md), [0029](0029-socratic-relay.md)

## What was already done

About half the file repeated the feedback of 5 October, built and deployed on 6 October.
The owner confirmed that a hard reload showed it: a browser tab left open across several
updates had kept the old app while its data came live from the new server. The cause is
fixed at its root (below), not rebuilt.

## Decisions

- **A newer build offers to reload.** The page compares the main script it loaded with
  the one the server's page names, when the tab comes back into view and every ten
  minutes, and shows "A newer Vademecum is ready — Reload". Only the app's own page is
  fetched, from its own origin.
- **Footer:** "A personal tutor and learning workspace, augmented by AI."
- **Add source, beside Flag a gap:** paste text or drop files into a pile you pick or a new
  one. On the Mac they are filed in the source folder (`piles/<confidence>/<pile>/`) and
  read in; on the phone they are uploaded to the cloud copy (95 MB per file, checked in the
  browser, under the Worker's limit), which reads them, and the Mac takes them in at its
  next sync, after which the encyclopedia agent is nudged.
- **Figures on the phone.** The cloud copy holds records only, so its pages' figures were
  broken. After each sync the Mac sends the pictures its current pages place and the cloud
  copy lacks (sixty a round, digest-checked, refused unless a page places them); the cloud
  copy serves them by image id from `attachments/figures/` (mirrored to R2) and deletes any
  no page places any more, there and in R2. A figure not there yet is left out, not broken.
- **Deleting a page** (owner's choice: removed and never rebuilt): on the Mac, the page goes
  with its board questions, flashcards, literature records and Markdown file; answers and
  reviews stay, unlinked; learning points and sources stay. Its topic is remembered in
  `app_state` (`deleted_pages`) and skipped by compile. Construction lists deleted pages,
  each with Bring back.
- **The Improvement Map** (owner's choices: all three):
  - each flagged topic is matched to the pages that cover it (an exact or whole-title match,
    never one shared generic word) and the pages are drawn as small nodes, so flags about one
    page meet there; pages that share learning points are joined;
  - topics gather in their specialty's labelled area; Re-arrange lays the map out afresh;
  - the layout is live after it settles: a drag wakes it and neighbours follow; selecting a
    node lights its connections and dims the rest; a page node opens its page.
- **Tutor mode from the map:** the topic panel offers the Socratic tutor on its page (here,
  or in ChatGPT with `socratic_start` given the page) or that page's board questions, asked
  outside the shuffled pass and leaving it untouched.
- **Construction shows progress:** files waiting in the source folder (found by pile and name,
  nothing read or hashed), sources read in and how far each is built (by text covered), the
  last scan's take and what it turned away and why, a filterable list of every source.
- **Wording:** the board question's "What this rests on" says what it holds, as the
  flashcards' and pages' already did.
