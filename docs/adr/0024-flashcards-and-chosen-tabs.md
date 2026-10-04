# ADR 0024 — Flashcards from the encyclopedia, and the tabs the owner chooses

- Status: accepted
- Date: 2026-10-04
- Extends: [0001](0001-rewrite-preserving-product-patterns.md), [0020](0020-exam-reports-on-the-map.md), [0023](0023-encyclopedia-and-board-bank.md)

## Context

Two asks from the owner. A flashcard tool that pulls from the encyclopedia and from the
Improvement Map, so that practice goes where the gaps are. And a settings tab where a learner
picks which tabs the app shows, now that there are nine.

ADR 0001 refused the vocabulary of flashcard apps: due counts, review queues, streaks. The word
"flashcard" sat on that list by association. The owner asked for flashcards by name; what the
product still refuses is the queue.

## Decisions

### 1. A card is written from a page and cites its points

From each current page the Mac's own model connection writes up to eight cards: a front that
stands alone (a question, a cloze, a "what / when / how much"), a back of one to three sentences
that says only what the page says, and the handles of the points the back rests on. A card
without a mappable handle, or a front that leans on "the text", is held. Cards are eligible on
the board bank's terms: current page, same version, every cited point unheld; a rewrite holds
the cards whose points left. They are written in the same refresh as questions, after each
scheduled build run, on "Compile now" and by the dissection agent.

### 2. The next card is a weighted draw, not a queue

The draw weighs each eligible card: a topic the learner flagged as a gap, an area an exam
report put below the mark, a page whose board question was missed in the last fortnight, and a
card the learner asked to see again each add weight; a card rated "got it" in the last day
weighs a fifth; a card never seen weighs a little more. The card says why it came up. Nothing
is due, nothing is counted against the learner, and the only two ratings are "again" and "got
it". Reviews are recorded and shared between the Mac and the phone; cards are Domi's.

### 3. Today and Settings are always shown

Preferences are one row in `app_state`, synced like the rest, so the choice follows the owner
to the phone. The Settings tab lists every tab with a checkbox; Today and Settings cannot be
unticked, so there is always a way back. A tab hidden from the navigation is still reachable by
its address, and `open_vademecum` still opens it.

## Consequences

- A third model turn per page, with the same disclosure the compile card already shows; the
  privacy test names the two local routes. Two tables, one Domi-owned.
- The web app's vocabulary test drops the word "flashcard" and keeps the rest of the list.
- The in-chat app's request allowlist gains the four routes; two MCP tools run the card flow.
