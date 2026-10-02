# ADR 0001 — Rewrite the implementation, keep the product

- Status: accepted
- Date: 2026-08-29
- Supersedes: nothing
- Related: [0002](0002-local-first-boundary.md), [0003](0003-data-directory-layout.md), [0004](0004-supported-platforms.md), [0005](0005-sqlite-and-hand-rolled-migrations.md)

## Context

`Vademecum_arxive/` holds a working predecessor. It is **read-only reference** — this project reads
its `README.md` and `CONTEXT.md` and never writes to it.

What that implementation got right is a product achievement, not an implementation detail, and none
of it is cheap to rediscover:

- **Ambient, calm delivery.** Opening the app shows what is worth a look. No queue to clear, no due
  count, no streak. The dashboard is descriptive ("here is your weak-spot map"), never prescriptive
  ("N items due").
- **Minimal-effort Knowledge Gap Flag capture.** One textarea, reachable from anywhere with a single
  keystroke, offline-tolerant. Filing a flag against a taxonomy is the system's job, not the user's.
- **Transparent local memory.** Stable local ids, timestamps, real deletion, human-readable export,
  a database backup workflow.
- **Honest unavailable states.** A live read that fails says what could not be reached. It never
  substitutes invented content for the owner's own.
- **A guarded vocabulary.** `CONTEXT.md` names the words the product refuses to use ("due count",
  "review queue", "streak", "daily review"), and a test failed the build when they appeared.

What it got wrong is structural, and is why this is a rewrite rather than a refactor:

1. **Infrastructure out of proportion to a single-user local app.** Postgres/pgvector under Docker
   for one person's notes. `scripts/dev.sh` had to start a container, wait on it, and apply
   migrations before the app could answer, and the operations surface grew a `verify_restart.py
   --cycle` to prove the app survived a database restart — a failure mode SQLite does not have.
2. **The virtualenv was not relocatable.** `pip install -e` baked an absolute interpreter path into
   `.venv/bin/pytest`; moving the checkout broke the documented test command, and the README shipped
   a paragraph explaining the breakage instead of a fix.
3. **Migration concerns fused into the product.** Four importers, a source manifest, a retirement
   procedure and a release audit were load-bearing for running the app at all. Historical import is
   a real requirement, but it belongs at the end of the delivery sequence, against a schema that has
   already stabilised.
4. **Feature surface far ahead of its foundations.** Notebook, Tutor, Atlas, Deep Track and
   literature ingestion all landed before the data boundary, the transport boundary and the platform
   target were written down. The `--demo` mode existed to keep the interface demonstrable while the
   live path was unreliable, and needed its own ADR (0011) and a structural test to stop demo
   content leaking into live reads.

## Decision

Rewrite from an empty tree, in the vertical slices AGENTS.md describes, and carry the product
patterns forward deliberately rather than by porting code.

Concretely, this first slice reproduces, from scratch:

- ambient presentation and the forbidden vocabulary, enforced by a test over the UI source;
- one-keystroke flag capture (`⌘K` / `Ctrl-K`) with `text` as the only required field;
- stable ids, timestamps, real deletion, JSON export and SQLite backup;
- an honest empty state for curated articles — the cover sheet says it has nothing yet and does not
  fetch or invent anything.

Nothing is copied out of the archive. The archive is not modified, moved, or deleted.

## Consequences

- The archive stays on disk as the only copy of historical data until an importer has been written
  and its counts checked (AGENTS.md, delivery step 7). This slice does not read it at runtime.
- The product patterns above are treated as requirements with tests, not as aesthetic preferences.
- Feature parity with the archive is explicitly **not** a goal of the first slices. Tutor, Notebook,
  retrieval and infographics arrive after the boundaries they depend on exist.
