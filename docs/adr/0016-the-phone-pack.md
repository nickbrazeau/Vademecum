# ADR 0016 — The phone pack: Vademecum in a Project, with no server anywhere

- Status: superseded on 2026-10-04 by the Cloudflare copy (ADR 0017) and removed (ADR 0026)
- Date: 2026-10-02
- Extends: [0012](0012-a-local-product-each-learner-installs.md)
- Beside: [0015](0015-two-vademecums-that-sync.md) (the second-node route; this one needs no node)

## Context

The owner's architecture in their own words: the piles are the context for every answer,
PubMed and UpToDate are the review, ChatGPT or Claude is the intermediary, and Vademecum sits
on top. They asked whether the intermediary could host Vademecum, so that nothing is hosted
on Cloudflare or a machine of their own, and the phone and the Mac could work apart and be
linked later.

An assistant hosts two things: the context (a Project's files) and the conversation. It does
not run a server. Vademecum's records and checks run on the Mac. So the no-hosting design puts
the bank into the Project as a file, lets the assistant tutor from it directly, and brings the
work back as a file the Mac takes in.

## Decisions

### 1. One Markdown file is the bank on the phone

`POST /api/pack` and the `phone_pack` tool write a pack into the source folder's `phone/`
directory: the Tutor rules; the learning points by pile with their support labels, topics and
citations; every eligible Tutor question with its id, prompt, reference answer and rubric and
when it was last answered; the open flags; and the exact format of the session log. The
learner drops it into a ChatGPT or Claude Project beside their material. The reference answers
are in the pack because the assistant there is the model and grades from them; the learner is
told so.

### 2. The assistant grades directly; the Mac records it honestly

There is no host-mode handoff on the phone and no key: the assistant is the model and the
Project is where it works. A grade given there is recorded on the Mac as a model grade with
the uncertainty "graded by the assistant on the phone, from the pack, outside a checked
turn". A self-assessment is still a self-assessment.

### 3. The session log comes back through the inbox

The pack ends with the one format the assistant must write: a fenced block tagged
`vademecum-session` holding attempts (question id, answer, outcome, feedback, what was
missing or unsafe, a better answer) and flags (text, topic). The learner saves it as a file
into `<source folder>/inbox/`. Every scan reads the inbox; a file is remembered by its digest
and taken once; attempts against questions that exist are recorded; flags are created;
anything unrecognised is reported and left where it is. Nothing is deleted, as with every
scan.

### 4. What the phone keeps and what it gives up

Kept: the Tutor in the learner's own words, grading by the assistant, flags, the piles as
context, asynchronous work on both sides, linked at the next scan. Given up on the phone: the
dashboard, pictures, the map, builds and the mechanical checks, which remain the Mac's,
in the browser, in the Dock, and in Codex.

## Consequences

- Nothing new is hosted anywhere. The only transport is the learner moving two files.
- The pack is bounded (500 points, 200 questions) and names no path. It is regenerated after a
  build; an old pack still works, its unknown question ids are simply skipped.
- The inbox accepts `.md`, `.txt` and `.json` up to 2 MB; a log the assistant wrote in a chat
  is pasted or saved as is.
- The cloud-node route (ADR 0015) remains the answer for "the dashboard on the phone". The two
  compose: a pack can be written from either node.
