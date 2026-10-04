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

### 5. A literature review per page

Compiling a page runs one public PubMed search of the topic's own words, through the same
provider and preferences as the literature watch (guidelines and the preferred journals first),
and hands the top records' titles and abstracts to the compile turn as handles beside the
points. A paragraph may rest on points, on records, or both; a page is never records alone.
The page ends with "In the literature", and lists every record reviewed, marking those the page
drew on, with retraction and correction flags as the watch shows them.

### 6. The dissection agent

A textbook is thousands of pages of this. The dissection agent takes one pile and works it
through to the end: a batch built as the Build button would, every few batches a compile of
what is stale (pages, their reviews, their questions), and on any failure a growing pause and
another try, never a stop. Its record is written after every step, so a restart resumes it;
when the pile is fully built and every page current it says "complete" and keeps watching the
pile for files added later. Starting it is a standing consent like the schedule's (ADR 0018),
with its own disclosure; stopping it withdraws the consent. One compile runs at a time whoever
asks for it, so the agent, the schedule and "Compile now" never compile the same topic twice.

### 7. Shelved by subject, and no disclaimers in the prose

The Encyclopedia lists its pages under their subspecialty, in the map's order, beneath a table
of contents; a page with no subspecialty comes last under "Other topics". A model told never to
imply endorsement tends to write "this is a description of the source's protocol, not an
endorsement" into pages, points and explanations. The citation already says whose statement it
is, so the instructions now forbid the disclaimer, the server drops such a clause from any
prose it accepts, and prose kept earlier is tidied once when a workspace opens.

Today's page to review is chosen from the fuller pages: those resting on two or more points, or
that drew on the literature. A page that restates a single point is still in the Encyclopedia,
and is the day's page only while no fuller one exists. Today opens with what is new in the
literature, then the page.

## Consequences

- Two more model turns, both on the Mac's own connection, both with a disclosure; the privacy
  test names the routes. In host mode pages and questions wait, and Foris shows what sync brings.
- Four tables: pages and questions are Domi's, the cycle and the answers are shared.
- A seventh view, Encyclopedia, and `open_vademecum` takes `view="encyclopedia"`; six MCP tools
  read pages and run the board flow.
- A Build now makes points without questions, which removes the assessment turn per question.
