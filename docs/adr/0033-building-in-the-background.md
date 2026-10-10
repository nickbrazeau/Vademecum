# ADR 0033 — Building in the background, and Foundation

- Status: accepted
- Date: 2026-10-10
- Supersedes the default of ADR 0018. Builds at set times remain an option.
- Prompted by the owner's feedback of 10 October.

## Context

The owner's source folder had 824 files. Nearly all had been read in, but 906 sources were
not built yet. Building ran only at three set times a day, three batches per pile per run,
so the backlog would take weeks to clear. A file that had not been built said nothing about
why.

Two pages showed parts of the same pipeline:

- **Sources:** the piles, the timer, and local data.
- **Construction:** progress, the folder drop, the dissection agent, compiling the
  encyclopedia, held items, and deleted pages.

## Decision

1. **Build whenever the Mac is awake.** The owner gives standing consent once, behind the
   same disclosure as before. From then on, the scheduler builds continuously:
   - it takes the pile with the most text still to build and runs one batch, the same batch
     the Build button would make;
   - it compiles the encyclopedia every 5 batches;
   - it rests 5 s between batches;
   - after a failure it waits 60 s, doubling up to 30 min;
   - when the model connection reports its allowance used up, it waits 15 min;
   - with nothing to build, it checks again every 10 min;
   - the owner can pause it for 2 hours.

   Progress is saved per batch through the existing per-segment cursors (`covered_upto`), so
   the Mac sleeping costs at most the batch in flight. A wake is noticed when the wall clock
   moves further than the monotonic clock, and building carries on at once. Set times
   remain an option ("Only at set times instead").
2. **Every file says why it is where it is.**
   - **Waiting to be read in:** with its place in the queue, or turned away with the
     reason.
   - **Already here under another name:** found from the folder scan's record of each file's
     contents. Such files no longer show as waiting forever.
   - **Read in but not built:**
     - being built now;
     - in line;
     - no readable text, because it needs OCR or is locked;
     - or the builder's own reason, such as paused, allowance used up, waiting after a
       failure, or off.
   - The builder's status line sits at the top of the page.
3. **Foundation** replaces Sources and Construction as one tab. Its sections, top to bottom:
   1. progress, with reasons;
   2. add a source;
   3. building in the background;
   4. the encyclopedia agent and compiling;
   5. the piles;
   6. what is held for review;
   7. deleted pages;
   8. local data.

   Old addresses (`/sources`, `/construction`, `/piles`) and saved tab choices map to
   Foundation.

## Consequences

- With consent given, building uses the owner's model allowance steadily while the Mac is
  awake. The pause and the off switch are one tap away on Foundation.
- Two builders can be active at once: the background builder and the encyclopedia agent. A
  pile already being built is reported as busy and skipped, so no batch is built twice.
- A batch interrupted by sleep is redone from its start. The cost is bounded by the batch
  size: at most 24 excerpts or 40,000 characters.
