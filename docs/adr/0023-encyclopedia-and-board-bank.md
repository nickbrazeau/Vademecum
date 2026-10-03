# ADR 0023 — The encyclopedia and the board bank

- Status: accepted
- Date: 2026-10-03
- Extends: [0007](0007-source-intake-verification-and-public-literature.md), [0018](0018-builds-on-a-timer.md), [0022](0022-case-series-hub.md)

## Context

Until now a Build turned a pile's excerpts into learning points and, beside each point, one or
two open-answer questions, and the Tutor asked those. The owner's correction: piles should be
synthesised into an encyclopedia; Today should open on one page of it to review; and the Tutor
should write its own high-yield questions in the ABIM board format, drawn from the encyclopedia
and from other context, rather than ask the per-point questions.

## Decisions

### 1. A page per topic, every paragraph cited

A Build still makes learning points: the claim, its detail, its support and its anchors are the
verified atoms, and nothing here changes how they are earned (ADR 0007). The synthesis prompt no
longer asks for questions beside each point, and asks for a detail that reads well on a page.

A page is one topic's unheld, machine-reviewed points, handed to the Mac's own model connection
as numbered handles, compiled into a title, a summary and sections of paragraphs. The server
keeps a paragraph only when every handle it names maps to a real point, drops a section with no
such paragraph, and keeps no page with no section. A page records the hash of the point set it
was compiled from; when a Build adds to the topic, the page is stale and is compiled again,
bumping its version. Compilation runs after each scheduled build run and on "Compile now",
with a disclosure that names what is sent: the points' claims, details, labels and source
names, never a source file.

### 2. One page a day on Today

Today opens on a page chosen from the date, so the same page shows all day and a different one
tomorrow; "Another page" is an explicit act. It counts nothing and asks for nothing back.

### 3. Board questions written from a page, checked locally

From each current page the model writes up to five single-best-answer questions in the ABIM
style: a vignette, one lead-in, five parallel options, one key, an explanation of every option,
an educational objective, and the handles of the points the key rests on. The prompt also carries
other context -- teaching points from the Case Series in the page's specialty (ADR 0022) and the
learner's open flags on the topic -- to shape what is worth asking, never as the source of an
answer. The server drops a draft with a bad key, fewer than five distinct options or no
explanation, and holds one that cites no point, leans on "the text", or offers "none of the
above". A question is written for a page version; a rewrite holds any question whose cited
points left the page. Eligibility is re-proved on every call: current page, same version, every
cited point unheld and machine reviewed.

Checking a choice is local: the key is on this Mac, so no model turn and no disclosure. The
cycle is the Tutor's cycle, shuffled and redrawn the same way, and answers are recorded with the
stem as asked.

### 4. Where the Tutor's eligibility rule stands

ADR 0007 admitted only evidence-supported points to the open-answer bank. The board bank admits
a question whose cited points are unheld and machine reviewed, source-supported included: the
page it comes from shows the support of every point, the explanation cites them, and the answer
is checked against a key rather than graded by a model. The open-answer bank keeps its rule and
is still asked when the board bank is empty.

## Consequences

- Two more model turns, both on the Mac's own connection, both with a disclosure; the privacy
  test names the routes. In host mode pages and questions wait, and Foris shows what sync brings.
- Four tables: pages and questions are Domi's, the cycle and the answers are shared.
- A seventh view, Encyclopedia, and `open_vademecum` takes `view="encyclopedia"`; six MCP tools
  read pages and run the board flow.
- A Build now makes points without questions, which removes the assessment turn per question.
