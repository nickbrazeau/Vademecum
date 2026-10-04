# ADR 0021 — Filing flags under topics

- Status: accepted
- Date: 2026-10-03
- Extends: [0018](0018-builds-on-a-timer.md), [0020](0020-exam-reports-on-the-map.md)

## Context

A flag is one sentence, or a saved link, captured in one action; the Improvement Map draws
flags by topic, and a flag without a topic is a dot called "not filed yet". The owner's four
flags were all unfiled, so the map showed one nameless dot. Filing was "the system's job, not
done in this version".

## Decisions

1. **The assistant files as it flags.** The `flag_knowledge_gap` tool now asks for a topic
   every time, inferred from what the owner said, two to five words.
2. **The Mac files what is left.** On the Mac's own model connection, unfiled open flags go in
   one batch (up to fifty) and come back with a topic each and a subspecialty from the list.
   The topic is written on the flag; an owner's own specialty call is never overridden. This
   runs before each scheduled build and on "File them now" on the map, which says what is
   sent. In host mode it refuses and says why.
3. **Questions stand alone.** Separately, the synthesis prompt now forbids "the excerpt" and
   its kin in a question, and the Tutor shows the passage a question rests on, on request,
   before the answer is typed.

## Consequences

- A fourth route may start a model turn, with the disclosure beside the button; the privacy
  test names it. The README's table of what is sent gains a row.
- The map has names where it had a dot. Links saved as flags file under the topic their
  words suggest, or "unsorted link".
- Owner, 2026-10-03: neither "not filed yet" nor "unsorted link" is a place on the map. Flags
  with no topic are counted beside the graph and listed under it; "unsorted link" stays on the
  flag, so it is not sent for filing again, and is left out of the map's topics.
