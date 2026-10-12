# ADR 0035 — One encyclopedia page per subject

- Status: accepted
- Date: 2026-10-11
- Amends ADR 0023 (pages compiled per topic).
- Prompted by the owner's feedback of 11 October.

## Context

A page was compiled for each topic string, and topic strings are the model's free wording
of each learning point. On 11 October the encyclopedia had 1,052 pages from 2,996 points:

- the median page rested on one point, and 928 pages rested on two or fewer;
- 2,640 points appeared on more than one page;
- related pages sat side by side: 11 "Antifungal …" pages (mechanism, mechanisms, spectrum,
  resistance, toxicity …), 10 "AmpC …" pages.

The owner asked for pages to be combined and added to, not repeated.

## Decision

1. **A page is a subject.** `page_topics` files a topic under a subject, which is a page's own
   topic. A page gathers the points of its subject and of every topic filed under it, up to
   80 points (the most recent kept). Pages may have ten sections of five paragraphs.
2. **Pages are added to, not replaced.** The page prompt receives the topics the page
   gathers, with a section per aspect where the points allow, and the page as it stands
   with its point handles. The model keeps what it says, adds what the new points bring, and
   reconciles the two.
3. **New topics are placed before anything is written.**
   - First, by spelling: case, hyphens, punctuation and plural endings set aside.
   - Then, for a topic with pages it might belong on (by shared words), one model turn over
     topic names and page titles only decides: one of those pages, or its own.
   - A topic with no candidate page gets its own page without a turn.
   - Up to 200 topics are placed per refresh; the rest wait.
4. **Existing pages are grouped, automatically.** The owner chose this over reviewing a
   proposal. Pages are grouped first by spelling, then by one model turn per specialty
   (in chunks of 120 titles) over page titles and point counts only.
   - Each group is folded into the page the owner edited, or else the page with the most
     points.
   - Two pages the owner edited are never folded together.
   - A group over 80 points is left as it is.
   - Grouping runs again over a specialty once 40 pages have been made since the last run, so
     new topics that belong together still end up on one page.
5. **Nothing is lost in a merge.** Before an absorbed page is removed, its board questions,
   flashcards, literature, Socratic sessions, podcast links and pending page changes move to
   the page that stays. Answers and reviews hang on the questions and cards, so they move
   with them. The absorbed id redirects (`page_redirects` in `app_state`), so old links open
   the combined page. The Markdown mirror removes files of pages that no longer exist.
6. **A larger page asks more.** A page wants a question and a card for every four points,
   at least four and at most fifteen.
7. **Lookups follow subjects.** The learner model, the map, flags and page matching count a
   filed topic as its page. `page_topics` is the Mac's and syncs and exports like the pages.

## Consequences

- The first refresh after the update spends about 15 grouping turns. Every merged page is
  then written again from all its points, through the normal compile, 12 per refresh.
- A point whose topics belong to two subjects appears on both pages, as a cross-reference.
- What the model sees for grouping and placing is page titles and topic names: the same
  public strings already used as PubMed queries.
