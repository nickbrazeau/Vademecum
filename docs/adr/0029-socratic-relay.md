# ADR 0029 — The Socratic tutor on the phone, answered by the Mac

- Status: accepted
- Date: 2026-10-06
- Extends: [0015](0015-two-vademecums-that-sync.md), [0017](0017-the-seat-on-cloudflare.md), [0025](0025-socratic-tutor-and-podcasts.md), [0028](0028-feedback-of-5-october.md)

## Context

The owner wants a Socratic session by voice, launched from Vademecum, recorded in
Vademecum. Three things stood in the way:

1. ChatGPT reached the connector but offered only the tool list it fetched when the
   connector was added: the older Tutor tools, none of the Socratic ones. ChatGPT does
   not refetch a connector's tool list by itself.
2. ChatGPT's voice mode calls no tools at all, so a voice conversation there never
   reaches Vademecum.
3. The phone's copy has no model of its own, so its own tutor could not run.

## Decision

- **The tool list.** The Socratic tools are registered first, and the Tutor overview tells
  an assistant that lacks them to ask the owner to refresh the connector.
- **The relay.** The phone's copy queues each tutor turn in memory. The Mac collects it over
  the sync routes (`/api/sync/relay/wait`, a long poll, and `/api/sync/relay/{id}`), computes
  the next question on its own connection with the page it holds, and posts it back. The
  phone's copy records it on the session and the phone shows it, spoken in voice mode.
- **Waking.** Idle, the Mac checks a flag at the Worker (`/__relay/wanted`, the sync token,
  answered from the bucket) every four seconds: this never wakes the container. The phone's
  copy writes the flag, at most every half minute, whenever the tutor is open. With a fresh
  flag the Mac collects turns until ten quiet minutes pass.
- A chat host's `socratic_start` (no relay flag) is unchanged.

## Consequences

- The tutor works on the phone, in text or by voice with the browser's own dictation and
  speech, while the Mac is on, awake and running Vademecum. If it is not, the phone says so
  after a minute and offers ChatGPT or Claude.
- What travels is the session's dialogue and the page's id, from the owner's cloud copy to
  the owner's Mac, over the sync token; the model call is the Mac's usual one.
- About 21,600 flag reads a day at the Worker while idle, inside the free allowance.
