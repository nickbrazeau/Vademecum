# ADR 0002 — The local-first boundary

> **Amended by [ADR 0007](0007-source-intake-verification-and-public-literature.md).**
> This ADR was written for a slice where **no learning content was transmitted** and Vademecum's
> own code opened no socket. Both statements were true then and are false now. What replaced them:
> two explicit, disclosed transmissions (Build learning material, Grade) through the local Codex
> child, and one narrow allowlisted egress to PubMed carrying only a short public topic phrase.
> Everything else below still holds — loopback binding, no API key, no analytics, no CDN, the
> service-worker allowlist, no PHI, and free text never reaching a log.


- Status: accepted
- Date: 2026-08-29
- Related: [0003](0003-data-directory-layout.md), [0005](0005-sqlite-and-hand-rolled-migrations.md)

## Context

"Local-first" is easy to claim and easy to erode. AGENTS.md states the boundary in prose; this ADR
states where it is enforced in code, so that a later slice adding the Codex App Server bridge cannot
quietly move it.

Two distinct things are being kept local:

- **Storage.** The canonical database, attachments and exports live on the owner's Mac.
- **Reachability.** The server answers only to the machine it runs on.

Neither implies local *inference*. When the App Server bridge lands, prompts and retrieved context
will leave the machine for OpenAI, and the UI must say so at the moment it happens.

## Decision

**1. `127.0.0.1`, with nothing that turns it off.**

`VADEMECUM_HOST` defaults to `127.0.0.1`, and configuration validation rejects every other value —
including `::1` and `localhost`, which are loopback but are not what AGENTS.md names. There is no
"0.0.0.0 for convenience" path and no opt-in setting: an earlier draft of this ADR had
`VADEMECUM_ALLOW_NON_LOOPBACK`, and it was removed, because AGENTS.md lists binding `127.0.0.1`
among its non-negotiable boundaries and a boundary that a variable can switch off is not one. The
failure is a refusal to start, not a warning in a log nobody reads. Changing this requires changing
the governing AGENTS.md first.

**2. No network egress from Vademecum's own code.**

The backend opens no socket and imports no HTTP client. The frontend calls only same-origin `/api`.
There are no API keys, no analytics, and no fonts or scripts from a CDN. The cover sheet's
curated-articles section is empty *because nothing fetches articles*, and it says so.

*Amended by [ADR 0006](0006-codex-app-server-bridge.md), 2026-08-30. Corrected 2026-08-30.*
Vademecum now manages one local `codex app-server` child process over stdio. The first draft of this
amendment said that this was "a pipe to a process on this Mac, not a socket to a network", which is
true of the pipe and misleading about the boundary: the process on the other end of that pipe
contacts OpenAI. Reading account state, reading rate limits and performing ChatGPT device-code
sign-in are carried out by Codex against OpenAI's servers, so opening the Model page produces
network traffic from this machine.

What the tests actually establish is narrower, and the copy must say the narrower thing:

- **Vademecum's own process opened no socket.** *(Superseded by ADR 0007.)* This was true of the
  slice this ADR describes. It no longer is: `literature/http.py` reaches one allowlisted host to
  look up published literature. Nothing else in the backend imports an HTTP client, and the
  frontend still calls only same-origin `/api`.
- **No learning content was transmitted.** *(Superseded by ADR 0007.)* This was true when there was
  no action that would send any. Two now exist — **Build learning material** and **Grade** — each
  preceded by a disclosure naming exactly what will be sent, each requiring a specific button press.
  What survives unchanged is the refusal of the shorthand: "nothing is sent" was not acceptable
  then and is not acceptable now, because a reader takes it to mean no network traffic.
- **No API key.** Unchanged and non-negotiable. Codex holds the ChatGPT credential, the bridge
  cannot construct an API-key login, every content turn re-reads account state and refuses anything
  that is not a ChatGPT account, and no test can spawn a real Codex.

The promise made here — that a model action would say what is about to be sent, each time, before
sending — is what ADR 0007's consent hashing implements.

**3. The service worker may cache the app shell and nothing else.**

Cache policy is a pure function (`apps/web/src/sw/cachePolicy.ts`) so it can be tested directly, and
it is an allowlist rather than a denylist. What may be stored is `/`, `/index.html`,
`/manifest.webmanifest`, the named icons, and hashed files under `/assets/` — content-addressed, so
immutable, which is the only reason keeping them is safe. Everything else is refused: `/api/*`, the
owner's own files under `/attachments/`, `/exports/` and `/backups/`, every client route, any
non-`GET`, anything cross-origin, anything carrying a query string, and any opaque or non-200
response. "Same-origin and not `/api`" was the earlier rule and was too generous by exactly the set
of paths that matter. Serving is a separate question from storing: a navigation to a client route is
still served from the network with the cached shell as its offline fallback, and is still not
stored. A private response never reaches `Cache.put`.

**4. No PHI, stated at the point of entry.**

Free-text inputs carry a concise warning. The backend validates length and rejects empty text but
deliberately does **not** attempt to detect identifiers — a detector that is wrong in either
direction is worse than a clear rule and an honest warning.

**5. Free text is never logged.**

Request logging records method, path, status, duration and a generated correlation id. Flag text,
item bodies and note content are not logged at any level. A test posts a marker string through the
API and asserts it does not appear in captured log output.

**6. Responses carry no filesystem paths.**

Export and backup responses name a *filename* and a *directory label* ("exports", "backups"). The
absolute data directory is printed by `scripts/dev.sh` in the terminal, where it is useful, and is
absent from every HTTP response. This preserves the archive's rule that no response carries a
filesystem path.

## Consequences

- Reaching Vademecum from an iPhone requires a private tunnel the owner sets up deliberately
  (Tailscale or equivalent), forwarding to `127.0.0.1` on the Mac. The process never binds anywhere
  else. That is the intended friction; see [0004](0004-supported-platforms.md).
- Offline is the normal case for the shell, not an error state. The API being unreachable is
  reported as unreachable, never papered over with substituted content.
- When the App Server bridge lands, it must add its own explicit-transmission disclosure. This ADR
  does not grant it one.
