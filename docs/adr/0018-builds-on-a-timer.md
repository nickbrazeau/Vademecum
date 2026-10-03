# ADR 0018 — Builds on a timer

- Status: accepted
- Date: 2026-10-03
- Extends: [0006](0006-codex-app-server-bridge.md), [0007](0007-source-intake-verification-and-public-literature.md)
- Amends: [0002](0002-local-first-boundary.md) rule on explicit action, with a standing consent

## Context

The owner wants "build learning material for my pile" to happen on a schedule through the day,
and a button for the same. A build sends excerpts of the owner's material to a model, which
until now happened only after the owner read a preview and said yes to that exact batch
(ADR 0002, 0007). A schedule has nobody reading a preview. And in host mode (ADR 0009, 0012)
the model is the assistant in a conversation; on a timer there is no conversation.

## Decisions

### 1. The Mac's own Codex connection does the turns

A scheduled build runs only where Vademecum can reach a model by itself: the Mac in `codex`
mode, through the Codex App Server bridge (ADR 0006), on the owner's ChatGPT sign-in, with no
key. `mcp.sh setup login --model codex` starts the long-running copy that way. In host mode the
schedule exists but refuses to run, and says why; Foris (ADR 0017) is always host mode and
never builds.

### 2. A standing consent, given once and visible always

Turning the schedule on is consent to what every run sends. The disclosure is returned with the
schedule and shown above the switch, in the dashboard and in the tool; it names the excerpts,
the follow-up checks and the destination, and says "without a further prompt, until you turn it
off". The moment of consent is recorded and shown; turning the schedule off withdraws it. "Build
now" is the same consent given once, for one run.

### 3. A run is the Build button, pile by pile, bounded

At each time of day, while enabled, every pile with passages not yet built from goes through up
to `batches_per_run` batches (default 3, at most 10), one build at a time, by the same code path
as the button: propose a batch, record it, start the run, wait for it. A pile with nothing new
is reported as such and sends nothing. Each run's outcome, per pile, is recorded and shown on
Today, so what was sent and what came of it is never silent.

### 4. The surface

`GET /api/build/schedule`, `PUT` (enabled, times, batches per run), `POST .../run`; the Today
page's "Builds on a timer" card; the tools `build_schedule` and `build_now`. The schedule and
the last run live in `app_state` and sync like any other setting.

## Consequences

- ADR 0002's "an explicit action for every send" becomes "an explicit action, or a standing
  consent the owner can see and withdraw". The README's table of what is sent gains a row.
- Builds run while the owner is away; the usage limits of their ChatGPT plan are the only
  budget. `batches_per_run` and the number of times a day are the levers.
- The Mac must be awake at the scheduled time; a missed time is simply skipped until the next.
- Foris receives what Domi built at the next sync, which is how the phone gets new
  material without anyone pressing anything.
