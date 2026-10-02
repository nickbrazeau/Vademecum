# ADR 0017 — The seat: the away node in a Cloudflare container

- Status: accepted (built; deployed by the owner with their own account)
- Date: 2026-10-02
- Implements the open choice in: [0015](0015-two-vademecums-that-sync.md)
- Uses: [0008](0008-mcp-server-for-chat-hosts.md), [0011](0011-the-desk-through-the-gateway.md)

## Context

ADR 0015 built two Vademecums that sync and left open where the away node runs. The owner
is open to Cloudflare if it can be "a seat for Vademecum" rather than a rewrite. Cloudflare
runs containers now, attached to a Worker and able to sleep to zero; what it does not give a
container is a durable disk. Vademecum is one Python process with SQLite files. So the seat is
the existing image plus two things: a supervisor that restores the records from object
storage at boot and snapshots them back on a timer, and a Worker that forwards every request
to the container.

## Decisions

### 1. The seat is one container running the existing code

`deploy/cloudflare/Dockerfile` builds the web app and installs the API and the MCP packages
into one image. `seat.py` supervises: restore, start the API (single tenancy, host mode, sync
role `away`, no source folder), start the gateway (OAuth, the web app behind sign-in, the
proxied API, sync passthrough), snapshot every five minutes and at shutdown. No macOS
frameworks, so no on-device page reading on the seat; a file taken in there is text-only
until the Mac has it.

### 2. Durability is a snapshot, and the Mac is still home

Every few minutes a consistent copy of each SQLite database (the records and the access store)
and every stored file not yet there go to an R2 bucket, through the Worker in front: the
container presents a key only the two of them know, and the Worker answers from a bucket
binding, so no R2 API token exists anywhere. At boot the databases come back before anything
starts and the files come back in the background. Between snapshots the seat can lose a few minutes of phone-side work
if the container dies, which for one learner is acceptable and is said plainly. The Mac remains
home: files, reading, builds, and the copy that is never behind.

### 3. The gateway serves the dashboard in single tenancy

The desk (ADR 0011) existed for many learners, each with a handle. A seat is one learner, so
the gateway now serves the web app in single tenancy too, behind the owner's passphrase: the
same consent the assistants use, as the sign-in page. The sync routes pass through the gateway
to the API untouched, carrying the peer token the API checks. The owner's passphrase can be
seeded once from a secret at start, since a container has no terminal.

### 4. The Worker is the address

A Durable Object class extending Cloudflare's `Container` with one instance, the gateway's port,
and secrets passed as the container's environment. Assistants connect to `<worker>/mcp`; the
phone opens `<worker>/` for the dashboard; the Mac syncs with `<worker>/api/sync/*`. The Worker
sleeps the container after twenty idle minutes and wakes it on the next request; restore from
R2 takes seconds.

## Consequences

- Nothing is rewritten. The seat runs the same tests the Mac does, plus its supervisor's own.
- The owner deploys from their own account: a bucket, four secrets, one `wrangler deploy`.
  This repository cannot verify a Cloudflare deployment; the README in `deploy/cloudflare`
  says what to check first.
- The seat holds a copy of the learner's material in a rented account. For one learner, their
  own; for others, the hosted-product decision.
- ChatGPT and Claude on the phone reach the dashboard and the tools through the seat; the
  phone pack (ADR 0016) remains the route that needs nothing hosted at all.
