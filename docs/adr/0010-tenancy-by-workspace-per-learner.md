# ADR 0010 — Tenancy: a workspace per learner, isolated by construction

- Status: accepted
- Date: 2026-09-28
- Amends: [0009](0009-hosted-personal-workspaces-chatgpt-is-the-model.md) (phase 2),
  [0003](0003-data-directory-layout.md), [0008](0008-mcp-server-for-chat-hosts.md)

## Context

ADR 0009 planned phase 2 as "`learner_id` on every row, Postgres, object storage, the job
queue, real accounts on the OAuth server, and the isolation test class". Building it, one fact
changed the shape: **personal workspaces only** means no query ever needs to see two learners'
rows at once. There is no shared bank, no cohort view, no cross-learner anything, and a new ADR is
required before there could be. A design that puts every learner in one table and relies on a
predicate in every query is defending against a class of bug -- the forgotten `WHERE` -- that a
different layout makes impossible.

The owner confirmed that ChatGPT for Clinicians is the same ChatGPT, so apps are available and
phase 0 is closed.

## Decisions

### 1. One directory, one SQLite database, per learner

`<data dir>/learners/<learner id>/` holds that learner's `vademecum.sqlite3`, `attachments/`,
`exports/`, `backups/` and `model-workspace/` -- the exact single-owner layout of ADR 0003, one
level down. A request's connection is opened on the learner's file and can reach nothing else.
**The storage layer does not know tenancy exists**: not one query changed.

Isolation is therefore a property of the filesystem path a request resolves to, and the
isolation tests (`test_tenancy.py`, `test_mcp_tenancy.py`) prove it at the API and at the tool
boundary: learner B, holding a valid token, gets a 404 for every id learner A holds, empty
listings, separate exports and backups, and cannot submit A's pending turns even against B's
own pile.

*Deferred, not rejected:* Postgres and object storage. At pilot scale, hundreds of learners with
SQLite files under one directory on one volume is well within reach, backups are per file, and
deletion is `rmtree`. When the service outgrows one volume, each learner's directory moves as a
unit; nothing in the storage layer changes then either. The job queue is deferred with them: a
build waits on the learner's ChatGPT, not on a worker pool.

### 2. Two tenancy modes, never mixed

`VADEMECUM_TENANCY=single` (default) is the owner's Mac exactly as before: one workspace at the
data-directory root, no identity, every request the owner's. `multi` is the hosted product:
every request carries a token, resolved to a learner, or it is a 401 before anything is read.
Multi requires `VADEMECUM_MODEL_PROVIDER=host`; the process refuses to start otherwise, because
the alternative is the operator's Codex grading other people's answers.

### 3. The token is the MCP server's, and the API reads the same store

The MCP server already issues OAuth tokens (ADR 0008). In multi tenancy each token names the
learner the consent page signed in, and the API resolves a token by reading the MCP server's
access store beside the data directory (`mcp/access.sqlite3`): the `tokens` table, by SHA-256
digest, live access tokens with a learner attached. The token travels from the MCP server to the
API in `X-Vademecum-Token`, attached per tool call from the SDK's verified request context.

This is an internal contract between two processes on one machine, not OAuth to the API. The
MCP suite pins the store path and issues a real token that the API's resolver resolves. The
header is not `Authorization` on purpose: the API's privacy test forbids that word in its source,
because the API has no provider credentials and must never grow any.

### 4. Accounts by invitation, with the credential the learner chooses

There is no open registration and no email. The operator runs `scripts/mcp.sh invite` and hands
the learner a one-time code (seven days, single use, kept only as a digest). The first time the
learner connects an assistant, the consent page offers "First time here?": invite code, a handle,
a passphrase of at least twelve characters. From then on, handle and passphrase. Passphrases are
scrypt records; five wrong attempts lock that handle, and only that handle, for fifteen minutes.
`scripts/mcp.sh disable HANDLE` stops sign-in and revokes every token; the workspace is
untouched, because deletion is the learner's act.

*Deferred:* Sign in with ChatGPT as an identity provider, if OpenAI opens it to apps; email
magic links. Either replaces the handle-and-passphrase form without touching tokens or tenancy.

### 5. Deletion is real, and only the learner's

`DELETE /api/workspace` with the exact confirmation phrase closes the workspace, removes the
directory with everything in it, and revokes every token the learner's assistant holds. Refused
in single tenancy, where the owner's workspace is the data directory itself. The reply and the
`GET` beside it say what deletion does not do: it removes nothing from a ChatGPT conversation.

### 6. What the operator cannot do

There is no route that lists learners' content, and the CLI lists handles, not material. The
owner's Codex bridge exists in the process but `/api/model/*` refuses every request in multi
tenancy. A health check in multi tenancy opens no learner's database.

## Consequences

- AGENTS.md's boundary 2 changes its mechanism, not its promise: "every table carries a
  `learner_id`" becomes "every connection is one learner's database".
- The MCP server's Claude and stdio paths carry no learner in multi tenancy and get a 401 from
  the API, which is the intended answer: the product's channel is ChatGPT, through the tunnel.
- The owner's single-tenancy deployment is unchanged and stays the first tenant of the design,
  not of the multi-tenant service; moving the owner's workspace under `learners/` is a copy of
  one directory, when wanted.
- Phase 3 is the two surfaces: the desk with sessions through the MCP gateway, and the ChatGPT
  app's widgets.
