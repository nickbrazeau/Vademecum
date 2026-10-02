# ADR 0006 — The Codex App Server bridge

Status: accepted
Date: 2026-08-30
Supersedes nothing. Extends [ADR 0002](0002-local-first-boundary.md).

## Context

Vademecum needs model access without asking its owner for a second API key. Codex holds a
ChatGPT-managed credential and exposes it over the App Server protocol: a local child process
speaking newline-delimited JSON over stdio.

This slice adds the connection and nothing that uses it. There are no threads, no turns, no tools
and no tutoring. What it can do is report whether Codex is signed in, report how much of the plan
has been used, start and cancel ChatGPT device-code sign-in, and be restarted when it breaks.

The protocol evolves with the CLI, so AGENTS.md requires the installed schema to be the source of
truth rather than documentation or memory.

## Decisions

### 1. The contract is generated, pinned and tested against

`scripts/refresh-codex-schemas.py` runs `codex app-server generate-json-schema` and copies the
client-facing part of the bundle into `schemas/codex-app-server/`, byte for byte, plus four
method-name lists derived from the generated unions. `MANIFEST.json` records the CLI version and a
SHA-256 per file.

The full bundle is 3.6 MB across 285 files. Committing all of it would hide the change that matters
— something moving under the account or envelope surface — inside 3.5 MB of thread and tool schema
nobody reads. The committed subset is 124 KB across 26 files.

`tests/test_appserver_schemas.py` checks the bridge against those files rather than against its own
constants. The strongest of those checks is an **equality** between the bridge's server-request
policy table and `methods/server-requests.json`: a Codex upgrade that adds a server-initiated
request Vademecum has not classified fails the suite. The generic refusal would have handled it
safely anyway; the test exists so a person reads it instead.

### 2. Four layers, and a transport that can be replaced

```text
api/routes_model.py     HTTP; owns no protocol knowledge
appserver/account.py    sanitised facade; the only thing routes talk to
appserver/client.py     correlation, lifecycle, supervision, refusals
appserver/protocol.py   the wire contract as pure functions; no I/O
appserver/transport.py  one managed child process; no protocol knowledge
```

`AppServerClient` takes a transport factory. In production that builds a `SubprocessTransport`; in
tests it builds a scripted fake. The whole stack — initialisation ordering, id correlation,
notifications, refusals, timeouts, cancellation, process death, restart — is therefore exercised
with no child process and no model call, which is what makes AGENTS.md's "no production model calls
in the test suite" a property rather than a promise.

The bridge does not import storage, and storage does not know it exists.

**Alternative rejected:** a library that wraps stdio JSON-RPC. Nothing in the standard library was
missing — `asyncio.create_subprocess_exec` already gives a line-oriented reader — and a dependency
inside a trust boundary buys supply-chain surface for code that would still need every rule below
written by hand.

### 3. One process, started lazily, under a lock

Nothing spawns at import or at application startup. The first call that needs the bridge starts it.
`ensure_started` holds an `asyncio.Lock` across spawn, `initialize` and `initialized`, so eight
concurrent first requests produce one process and one initialisation. The session is published only
after `initialize` succeeds; a failed initialisation tears the child down rather than leaving a
process that is running but not usable.

Capabilities are **stated as false**, not omitted:

```json
{"experimentalApi": false, "mcpServerOpenaiFormElicitation": false, "requestAttestation": false}
```

Omission means "whatever the default is". A default that flips in a later Codex release would opt
this client into a surface it has no code to handle.

### 4. Everything gets an answer, or a category

Outbound requests carry integer ids and are keyed by a `(type, value)` pair, so a reply of `"7"`
cannot resolve request `7`. A request is always removed from the pending map — on success, on
timeout, on cancellation, and when the child dies. A reply that matches nothing is counted, not
applied to someone else's request.

Inbound server-initiated requests are answered exactly once from a table, with no branch that
leaves one hanging:

| Request | Answer |
| --- | --- |
| `item/commandExecution/requestApproval` | `{"decision": "cancel"}` |
| `item/fileChange/requestApproval` | `{"decision": "cancel"}` |
| `execCommandApproval`, `applyPatchApproval` (legacy) | `{"decision": "abort"}` |
| `item/permissions/requestApproval` | JSON-RPC `-32601` |
| `item/tool/call`, `item/tool/requestUserInput` | JSON-RPC `-32601` |
| `mcpServer/elicitation/request` | JSON-RPC `-32601` |
| `account/chatgptAuthTokens/refresh` | JSON-RPC `-32601` |
| `attestation/generate` | JSON-RPC `-32601` |
| anything unrecognised | JSON-RPC `-32601` |

`cancel` and `abort` rather than `decline`: both deny, and these also stop the turn. This slice
starts no turns, so an approval request arriving at all means something is running that should not
be, and stopping it is the correct answer to that. The refusal messages are fixed local strings —
never anything derived from the request.

`account/chatgptAuthTokens/refresh` is refused rather than answered because Vademecum uses
Codex-managed authentication and holds no token to hand back. `attestation/generate` is refused
because the capability was never negotiated.

### 5. Fail closed, and never change provider

Every failure ends in a category from a closed set and a plain-language interface state. There is
no code path that switches provider or billing mode, because there is no second provider in the
codebase to switch to.

`LoginAccountParams` in the schema offers an `apiKey` variant. The bridge can construct only
`{"type": "chatgptDeviceCode"}`, and if the server answers a device-code request with a different
variant, that is `LoginNotSupported` rather than an adaptation. An `account/read` that reports an
`apiKey` or `amazonBedrock` account is surfaced as **signed out, with a reason** — signed in with a
credential this application will not use. `tests/test_privacy.py` restricts the string `apiKey` to
the two files that exist to refuse it.

### 6. One retry path, and one explicit restart

Read-only calls (`account/read`, `account/rateLimits/read`) are retried **once**, and only for
categories that mean the pipe went away: `unavailable`, `process_exited`, `write_failed`. The retry
does not restart anything explicitly — a dead session is reaped and replaced by `ensure_started` on
the way back in, which avoids two callers racing to restart and one of them killing the other's
fresh process.

A timeout is **not** retried. The request may have been received and acted on, and this path has to
stay safe to repeat.

`POST /api/model/restart` is the explicit path. It replaces the process and drops cached plan and
rate-limit state rather than carrying it across: a snapshot from a process that is gone is a claim
the bridge cannot support.

### 7. Diagnostics that cannot carry a payload

`appserver/diagnostics.py` is the only way the bridge logs. It accepts a fixed set of field names
(`cid`, `method`, `category`, `code`, `status`, `duration_ms`, `generation`, `exit`, `lines`,
`bytes`, `count`, `state`, `pending`, `attempt`) and only scalar values. A field not in the set is
dropped rather than formatted, so `event("x", params=...)` logs nothing at all.

Consequences worth stating:

- The App Server's error `message` is discarded in `decode_line`, at the edge. `ErrorReply` has no
  attribute holding it, so no later code has the option.
- An inbound method name is server-controlled text, so it is logged only if it matches
  `[A-Za-z0-9/_.:-]{1,64}`, and otherwise as `unrecognised`.
- **stderr is drained continuously and kept never.** Draining is not optional: a full stderr pipe
  blocks the child, and a blocked child looks exactly like a hung protocol. What is optional is
  keeping any of it, and the drain keeps a line count and a byte count. Not the text, not a sample,
  not the first line "for debugging".
- A notification handler that raises is caught and logged as `handler_error` without the exception,
  because the handler was holding the payload.

### 8. The child's environment is an allowlist

`HOME`, `PATH`, `LANG`, `LC_ALL`, `TMPDIR`, `USER`, `LOGNAME`, `TERM`, and `CODEX_HOME` if set.
Nothing else is inherited, so a variable set for the web process cannot change how the bridge
authenticates. `HOME` is on the list because it is what `~/.codex` resolves against.

The child's working directory is the **data directory**, not the checkout. The App Server resolves
project context from its working directory, and it has no business being handed a source tree.

### 9. The device code is shown once and stored nowhere

`POST /api/model/login` returns the verification URL, the one-time code and an opaque login id to a
live `no-store` response. They are not logged, not written to the database, and the interface holds
them in React state — never `localStorage`, `sessionStorage` or the service-worker cache, which
`tests/model.test.tsx` and `tests/cachePolicy.test.ts` assert directly.

`GET /api/model/status` reports `login_pending: true` and nothing else about the sign-in. The
pending login id lives in the backend process, so `POST /api/model/login/cancel` takes no body: the
browser never has to keep one in order to cancel.

Nothing opens a browser. The verification link is a link, rendered as an `href` only when it parses
as `https:`. An application that launches an external browser at a URL it received over a pipe is
doing something the owner did not ask for.

### 10. `resetsAt` is interpreted cautiously

The schema types `resetsAt` as an int64 with no stated unit. It is read as Unix seconds, and
rendered only when it falls between 2020 and 2100. Outside that range the reset time is omitted
rather than shown as 1970 or 33658. Showing nothing is honest; showing a confident wrong time is
not.

### 11. Health reports two facts, not one

`model_bridge_configured: true` and `model_calls_configured: false`. Collapsing them into one flag
is how "we have Codex wired up" quietly starts meaning "we send prompts". They are different facts
and they are reported separately.

### 12. What travels over the bridge, stated precisely

The bridge is a pipe to a local process, and that process talks to OpenAI. Both halves have to be
said, because each one alone is misleading:

| Operation | Leaves this Mac? | Carries learning content? |
| --- | --- | --- |
| `account/read`, `account/rateLimits/read` | Yes, performed by Codex | No |
| `account/login/start`, `account/login/cancel` | Yes, performed by Codex | No |
| A tutoring or grading turn | Not implemented in this slice | — |

So the sentence this slice may use is "no note, question, answer or retrieved context is
transmitted", and the sentence it may **not** use is "nothing is sent". The second reads as "no
network traffic", which is false the moment the Model page is opened. The interface copy, the README,
the OpenAPI description and ADR 0002 were corrected accordingly, and `tests/test_privacy.py` fails if
the API description reverts to claiming no outbound requests.

### 13. A sign-in completion is applied only to the sign-in it names

`account/login/completed` carries an optional `loginId`. The bridge clears its pending sign-in only
when that id equals the pending one, which rules out two failures that a positional "clear whatever
is pending" would produce:

- a completion for a cancelled, superseded sign-in clearing the one the owner is currently looking
  at; and
- a completion that overtakes the `account/login/start` response — the read loop sees it before
  there is a pending id to match — leaving `login_pending` true forever for a sign-in that has
  already finished. Those are remembered by id, in a bounded deque, and applied when the reply names
  them.

A completion with no `loginId` is not correlated to anything. The pending sign-in stays pending and
the owner can cancel it; guessing which sign-in it meant is the failure this rule exists to prevent.

### 14. A cancelled or unexpected startup takes the child with it

`_start` publishes the session only after `initialize` succeeds, so between spawning the child and
publishing it there is a window in which a running process and two reader tasks belong to nobody. A
protocol failure already tore them down. Cancellation — a closed tab, a shutdown — and any
unexpected exception now do the same before unwinding, the latter wrapped as the category
`startup_failed`. Otherwise an abandoned Model-page request would leave an orphaned `codex
app-server` that no later caller can find in order to reap.

## Consequences

- The Model page works with Codex absent: `codex_not_found` is a state with a plain sentence, not a
  crash and not a configuration error at boot.
- A Codex upgrade that changes the server-request surface fails the backend suite. This is
  intended; `scripts/refresh-codex-schemas.py --check` reports the drift.
- Rate-limit windows are shown as percentages and reset times. Credit balances, spend limits, limit
  ids and limit names are dropped at the facade and have no field downstream.
- The account email exists in `GetAccountResponse` and is read by nothing. There is no field for it
  in `ModelStatus`, in `ModelStatusOut`, or in the OpenAPI document, and a test asserts that.
- A `sqlite` migration was not needed: this slice stores nothing.

## What this slice still does not do

- No model turn, no thread, no grading. AGENTS.md's Tutor requirements are slice 4, and the
  approval-policy, sandbox and `outputSchema` decisions belong with them.
- No `account/logout`. Signing out is done in Codex; adding a route for it here would be a second
  place that changes the credential.
- No automatic polling of status. The page checks when it is opened and when asked.
