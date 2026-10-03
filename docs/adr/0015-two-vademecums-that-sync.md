# ADR 0015 — Two Vademecums that sync

- Status: accepted (engine built; Foris's hosting chosen in ADR 0017)
- Vocabulary: the Mac is **Domi** (Latin: at home), the always-awake copy is **Foris** (abroad); renamed 2026-10-03 from home/away and briefly harbour/sea; the settings accept all spellings
- Date: 2026-10-02
- Extends: [0012](0012-a-local-product-each-learner-installs.md), [0014](0014-the-web-app-inside-the-conversation.md)
- Keeps: [0002](0002-local-first-boundary.md), [0009](0009-hosted-personal-workspaces-chatgpt-is-the-model.md)

## Context

The learner wants Vademecum on the phone while the Mac sleeps: the Tutor, flags, the dashboard,
with ChatGPT doing the model work, and everything landing back on the Mac, which is where the
files are. A Mac behind a tunnel answers only while it is awake. A copy of the workspace that
is always awake answers always, but then there are two copies, and two copies have to agree.

The owner also said: no VPS yet, and not Cloudflare as the place the copy lives. So this ADR
settles the part that is the same whatever the second node turns out to be -- a container, a
small machine, or the phone's own browser -- which is how two workspaces exchange changes
without losing or duplicating anything.

## Decisions

### 1. Two whole Vademecums, one of them Domi

Both nodes run the same code, migrations and web app. The **Domi** is the Mac: the source folder,
the originals, pictures, on-device reading, and builds. The **Foris** is wherever the learner is
when the Mac is not: it holds a copy of the records, serves the dashboard and the MCP tools,
runs the Tutor and grading in host mode through the learner's assistant, and takes a file from
the phone as text-only intake. A node's role is a setting (`sync_role`).

### 2. A change log, filled by triggers, read at push time

Migration 0007 adds `sync_log` and three triggers per synced table (every exported table
except the seeded specialty list; never `pending_turns`, never the log itself). A trigger writes
which row changed and how; the payload is not copied. At push time the row is read as it is
then, so many edits to one row travel as one change, and a row deleted since travels as a
tombstone. `sync_state` is one row: this node's id, the cursors, and the `applying` flag the
triggers consult, so that changes applied *from* the peer are never logged and bounced back.

### 3. Domi initiates; the routes live on Foris

Foris serves `GET /api/sync/status`, `GET /api/sync/changes?since=`, `POST /api/sync/apply`,
and `GET`/`PUT /api/sync/file/{kind}/{name}`, only when `sync_accept_token` is set, only in
single tenancy, and only to a caller presenting that token in `X-Vademecum-Sync`; without it
the routes do not exist. Domi, on a timer and from `python -m vademecum sync`, pulls away's
changes and applies them, then pushes its own. Each batch is one transaction and the cursor
moves only with it. Files go by content-addressed name: Domi puts a file before pushing the row
that names it; Domi fetches a file Foris's row names; a batch that would leave a child without
its parent is rolled back whole and tried again.

### 4. Ownership instead of conflict resolution

- **Domi-owned**: sources, their text and pictures, builds, learning points, questions,
  evidence, literature records and checks. Foris may *create* a source it took in from the
  phone, and Domi accepts a row it has never seen; every other change to these tables
  flows Domi → Foris and is overwritten there. A rating or exclusion changed on the phone is not
  carried; it is done on the Mac or in the browser dashboard.
- **Shared**: piles, notes, flags, Tutor attempts and cycle entries, literature topics, map
  positions, settings. A row with `updated_at` goes to the later write. A row without one is
  insert-only and merges, which is what the Tutor cycle needs: what has been asked is a union.
- **Removal** is an explicit act on one side and is carried to the other. A scan never deletes
  (ADR 0012) and neither does a sync on its own.

### 5. What leaves the Mac, and to whom

This is the third thing this process transmits, after a model turn and a PubMed query: the
learner's own workspace, to the learner's own second Vademecum (Foris), at the address they configured,
over HTTPS, presenting the token they set. Nothing is sent unless `sync_peer_url` is set, and
nothing is sent anywhere else. The README says so beside the other two.

## Consequences

- The engine is testable with two in-process nodes and is tested that way: a bank built on
  Domi reaches Foris with its text and original; work done on Foris (a self-assessed answer, a
  flag, a note) reaches Domi; a third round moves nothing; a removal propagates; a phone-side
  change to a Domi-owned row is skipped, a phone-created source is taken.
- Where Foris runs is still open: a container on a small machine with a volume, or, as a
  different design, the phone's browser as Foris. Both use this engine unchanged;
  the second needs a client written for the browser.
- Grading on Foris is host mode: the assistant on the phone does the turn. There is still no
  key anywhere.
- Two nodes mean two copies of the material. For one learner that is their own business; for
  other learners it is the hosted product decision, taken deliberately or not at all.
